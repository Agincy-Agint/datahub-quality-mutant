from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import UTC
from typing import Any

import duckdb

from quality_mutant.model import (
    AssertionSpec,
    BaselineFailure,
    CatalogSnapshot,
    CheckResult,
    Mutation,
    MutationResult,
    stable_hash,
    stable_rows_hash,
)
from quality_mutant.mutants import generate_mutants
from quality_mutant.suggestions import suggest_for_survivor


def quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


DUCK_TYPES = {
    "STRING": "VARCHAR",
    "VARCHAR": "VARCHAR",
    "TEXT": "VARCHAR",
    "INTEGER": "BIGINT",
    "INT": "BIGINT",
    "BIGINT": "BIGINT",
    "FLOAT": "DOUBLE",
    "DOUBLE": "DOUBLE",
    "NUMBER": "DOUBLE",
    "DECIMAL": "DOUBLE",
    "TIMESTAMP": "TIMESTAMPTZ",
    "TIMESTAMPTZ": "TIMESTAMPTZ",
    "BOOLEAN": "BOOLEAN",
}


def _duck_type(value: str) -> str:
    normalized = value.upper().split("(", 1)[0]
    if normalized not in DUCK_TYPES:
        raise ValueError(f"Unsupported data type for deterministic DuckDB execution: {value}")
    return DUCK_TYPES[normalized]


def _load_tables(connection: duckdb.DuckDBPyConnection, snapshot: CatalogSnapshot) -> None:
    for dataset in snapshot.datasets.values():
        columns = ", ".join(
            f"{quote(item.name)} {_duck_type(item.type)}" for item in dataset.fields
        )
        connection.execute(f"CREATE TABLE {quote(dataset.table)} ({columns})")
        if dataset.rows:
            names = [field.name for field in dataset.fields]
            placeholders = ", ".join("?" for _ in names)
            values = [[row.get(name) for name in names] for row in dataset.rows]
            connection.executemany(
                f"INSERT INTO {quote(dataset.table)} VALUES ({placeholders})", values
            )


def _evaluate(
    connection: duckdb.DuckDBPyConnection,
    snapshot: CatalogSnapshot,
    assertion: AssertionSpec,
) -> CheckResult:
    dataset = snapshot.datasets[assertion.dataset]
    table = quote(dataset.table)
    column = quote(assertion.field) if assertion.field else None

    if assertion.kind == "not_null" and column:
        invalid = connection.execute(
            f"SELECT count(*) FROM {table} WHERE {column} IS NULL"
        ).fetchone()[0]
        return CheckResult(
            assertion.id, "PASS" if invalid == 0 else "FAIL", f"null_count={invalid}"
        )

    if assertion.kind == "unique" and column:
        total, distinct = connection.execute(
            f"SELECT count(*), count(DISTINCT {column}) FROM {table}"
        ).fetchone()
        return CheckResult(
            assertion.id,
            "PASS" if total == distinct else "FAIL",
            f"rows={total};distinct={distinct}",
        )

    if assertion.kind == "accepted_values" and column:
        placeholders = ", ".join("?" for _ in assertion.values)
        invalid = connection.execute(
            f"SELECT count(*) FROM {table} WHERE {column} IS NOT NULL AND {column} NOT IN ({placeholders})",
            list(assertion.values),
        ).fetchone()[0]
        return CheckResult(
            assertion.id,
            "PASS" if invalid == 0 else "FAIL",
            f"unexpected_count={invalid}",
        )

    if assertion.kind == "range" and column:
        clauses: list[str] = []
        parameters: list[Any] = []
        if assertion.minimum is not None:
            clauses.append(f"{column} < ?")
            parameters.append(assertion.minimum)
        if assertion.maximum is not None:
            clauses.append(f"{column} > ?")
            parameters.append(assertion.maximum)
        invalid = connection.execute(
            f"SELECT count(*) FROM {table} WHERE {column} IS NOT NULL AND ({' OR '.join(clauses)})",
            parameters,
        ).fetchone()[0]
        return CheckResult(
            assertion.id,
            "PASS" if invalid == 0 else "FAIL",
            f"out_of_range_count={invalid}",
        )

    if assertion.kind == "freshness" and column and assertion.max_age_hours is not None:
        latest = connection.execute(f"SELECT max({column}) FROM {table}").fetchone()[0]
        if latest is None:
            return CheckResult(assertion.id, "FAIL", "latest=NULL")
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=UTC)
        age = (snapshot.as_of - latest).total_seconds() / 3600
        return CheckResult(
            assertion.id,
            "PASS" if age <= assertion.max_age_hours else "FAIL",
            f"age_hours={age:.3f}",
        )

    if assertion.kind == "foreign_key" and column:
        target = snapshot.datasets[assertion.target_dataset or ""]
        target_column = quote(assertion.target_field or "")
        invalid = connection.execute(
            f"SELECT count(*) FROM {table} s LEFT JOIN {quote(target.table)} t "
            f"ON s.{column}=t.{target_column} WHERE s.{column} IS NOT NULL AND t.{target_column} IS NULL"
        ).fetchone()[0]
        return CheckResult(
            assertion.id,
            "PASS" if invalid == 0 else "FAIL",
            f"orphan_count={invalid}",
        )

    return CheckResult(
        assertion.id,
        "NOT_EXECUTED",
        f"unsupported assertion semantics: {assertion.kind}",
    )


def run_checks(snapshot: CatalogSnapshot) -> list[CheckResult]:
    connection = duckdb.connect(":memory:")
    try:
        _load_tables(connection, snapshot)
        assertions = [
            assertion for dataset in snapshot.datasets.values() for assertion in dataset.assertions
        ]
        return [_evaluate(connection, snapshot, assertion) for assertion in assertions]
    finally:
        connection.close()


def apply_mutation(snapshot: CatalogSnapshot, mutation: Mutation) -> CatalogSnapshot:
    clone = deepcopy(snapshot)
    clone.datasets[mutation.dataset].rows = [dict(row) for row in mutation.rows]
    clone.source = f"{snapshot.source}+mutant:{mutation.operator}"
    return clone


def run_campaign(snapshot: CatalogSnapshot, threshold: float = 1.0) -> dict[str, Any]:
    baseline = run_checks(snapshot)
    failed_baseline = [item for item in baseline if item.status == "FAIL"]
    if failed_baseline:
        names = ", ".join(item.assertion_id for item in failed_baseline)
        raise BaselineFailure(f"Baseline must pass before mutation; failing checks: {names}")
    executable_count = sum(item.status != "NOT_EXECUTED" for item in baseline)
    if executable_count == 0:
        raise BaselineFailure("Baseline cannot be established: no executable assertions")

    baseline_rows = snapshot.datasets[snapshot.target_dataset].rows
    baseline_rows_hash = stable_rows_hash(baseline_rows)
    results: list[MutationResult] = []
    descriptions: dict[str, str] = {}
    mutations, unavailable = generate_mutants(snapshot)
    for mutation in mutations:
        descriptions[mutation.operator] = mutation.description
        equivalent = stable_rows_hash(mutation.rows) == baseline_rows_hash
        if equivalent:
            results.append(
                MutationResult(
                    operator=mutation.operator,
                    mutant_hash=mutation.mutant_hash,
                    equivalent=True,
                    killed=False,
                    killed_by=(),
                    checks=(),
                )
            )
            continue
        checks = tuple(run_checks(apply_mutation(snapshot, mutation)))
        killed_by = tuple(item.assertion_id for item in checks if item.status == "FAIL")
        results.append(
            MutationResult(
                operator=mutation.operator,
                mutant_hash=mutation.mutant_hash,
                equivalent=False,
                killed=bool(killed_by),
                killed_by=killed_by,
                checks=checks,
            )
        )

    counted = [item for item in results if not item.equivalent]
    killed_count = sum(item.killed for item in counted)
    score = killed_count / len(counted) if counted else 0.0
    survivors = [item for item in counted if not item.killed]
    report: dict[str, Any] = {
        "format_version": "quality-mutant-report/v1",
        "source": snapshot.source,
        "target_dataset": snapshot.target_dataset,
        "target_urn": snapshot.datasets[snapshot.target_dataset].urn,
        "as_of": snapshot.as_of.isoformat(),
        "fixture_hash": snapshot.fingerprint,
        "baseline": {
            "status": (
                "PASS_WITH_NOT_EXECUTED"
                if any(item.status == "NOT_EXECUTED" for item in baseline)
                else "PASS"
            ),
            "executable": executable_count,
            "not_executed": sum(item.status == "NOT_EXECUTED" for item in baseline),
            "checks": [asdict(item) for item in baseline],
        },
        "unavailable_operators": unavailable,
        "mutation_summary": {
            "requested_operators": 6,
            "generated": len(results),
            "equivalent_excluded": sum(item.equivalent for item in results),
            "counted": len(counted),
            "killed": killed_count,
            "survived": len(survivors),
            "score": score,
            "threshold": threshold,
            "threshold_met": score >= threshold,
        },
        "kill_matrix": [
            {
                "operator": item.operator,
                "description": descriptions[item.operator],
                "mutant_hash": item.mutant_hash,
                "equivalent": item.equivalent,
                "killed": item.killed,
                "killed_by": list(item.killed_by),
                "checks": {check.assertion_id: check.status for check in item.checks},
            }
            for item in results
        ],
        "survivors": [
            {
                "operator": item.operator,
                "mutant_hash": item.mutant_hash,
                "suggestion": suggest_for_survivor(snapshot, item.operator),
            }
            for item in survivors
        ],
    }
    report["report_hash"] = stable_hash(report)
    return report
