from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import Any

from quality_mutant.model import (
    CatalogSnapshot,
    DatasetSpec,
    Mutation,
    stable_hash,
    stable_rows_hash,
)


def _build_mutation(
    *,
    operator: str,
    dataset: DatasetSpec,
    field: str,
    description: str,
    before: Any,
    after: Any,
    rows: list[dict[str, Any]],
) -> Mutation:
    material = {
        "operator": operator,
        "dataset": dataset.name,
        "field": field,
        "before": before,
        "after": after,
        "rows_hash": stable_rows_hash(rows),
    }
    return Mutation(
        operator=operator,
        dataset=dataset.name,
        field=field,
        description=description,
        before=before,
        after=after,
        rows=tuple(rows),
        mutant_hash=stable_hash(material),
    )


def _replace_first(
    dataset: DatasetSpec, field: str, value: Any, operator: str, note: str
) -> Mutation:
    rows = deepcopy(dataset.rows)
    before = rows[0][field]
    rows[0][field] = value
    return _build_mutation(
        operator=operator,
        dataset=dataset,
        field=field,
        description=note,
        before=before,
        after=value,
        rows=rows,
    )


def _find_assertion_field(dataset: DatasetSpec, kind: str) -> str:
    match = next(
        (item.field for item in dataset.assertions if item.kind == kind and item.field), None
    )
    if not match:
        raise ValueError(f"Cannot generate {kind} mutant: no matching assertion field")
    return match


def null_mutant(snapshot: CatalogSnapshot, dataset: DatasetSpec) -> Mutation:
    field = next((item.name for item in dataset.fields if not item.nullable), None)
    if not field:
        field = _find_assertion_field(dataset, "not_null")
    return _replace_first(dataset, field, None, "null", f"Set {field} to NULL")


def duplicate_mutant(snapshot: CatalogSnapshot, dataset: DatasetSpec) -> Mutation:
    field = (
        dataset.primary_key[0] if dataset.primary_key else _find_assertion_field(dataset, "unique")
    )
    rows = deepcopy(dataset.rows)
    duplicate = deepcopy(rows[0])
    rows.append(duplicate)
    return _build_mutation(
        operator="duplicate",
        dataset=dataset,
        field=field,
        description=f"Duplicate a row while preserving {field}={duplicate[field]!r}",
        before=len(dataset.rows),
        after=len(rows),
        rows=rows,
    )


def orphan_fk_mutant(snapshot: CatalogSnapshot, dataset: DatasetSpec) -> Mutation:
    if not dataset.foreign_keys:
        raise ValueError("Cannot generate orphan_fk mutant: target has no foreign key metadata")
    foreign_key = dataset.foreign_keys[0]
    referenced = snapshot.datasets[foreign_key.target_dataset]
    existing = {row[foreign_key.target_field] for row in referenced.rows}
    sample = next(iter(existing), None)
    if isinstance(sample, bool):
        raise TypeError("Cannot produce a guaranteed orphan for a boolean foreign key")
    if isinstance(sample, int):
        candidate: Any = 9_999_999
        while candidate in existing:
            candidate += 1
    elif isinstance(sample, float):
        candidate = 9_999_999.5
        while candidate in existing:
            candidate += 1.0
    else:
        candidate = "__QUALITY_MUTANT_ORPHAN__"
        while candidate in existing:
            candidate += "_X"
    return _replace_first(
        dataset,
        foreign_key.field,
        candidate,
        "orphan_fk",
        f"Point {foreign_key.field} at a missing {foreign_key.target_dataset} row",
    )


def enum_mutant(snapshot: CatalogSnapshot, dataset: DatasetSpec) -> Mutation:
    field = _find_assertion_field(dataset, "accepted_values")
    assertion = next(item for item in dataset.assertions if item.kind == "accepted_values")
    sample = next(iter(assertion.values), None)
    if isinstance(sample, bool):
        raise TypeError("Cannot guarantee an invalid value for a boolean enumeration")
    if isinstance(sample, int):
        candidate: Any = max(assertion.values) + 1
        while candidate in assertion.values:
            candidate += 1
    elif isinstance(sample, float):
        candidate = max(assertion.values) + 1.0
        while candidate in assertion.values:
            candidate += 1.0
    else:
        candidate = "__QUALITY_MUTANT_INVALID_ENUM__"
        while candidate in assertion.values:
            candidate += "_X"
    return _replace_first(
        dataset,
        field,
        candidate,
        "enum",
        f"Set {field} to a value outside the declared enumeration",
    )


def boundary_extreme_mutant(snapshot: CatalogSnapshot, dataset: DatasetSpec) -> Mutation:
    field = _find_assertion_field(dataset, "range")
    assertion = next(item for item in dataset.assertions if item.kind == "range")
    if assertion.maximum is not None:
        value = assertion.maximum + max(abs(assertion.maximum), 1) * 1_000
    elif assertion.minimum is not None:
        value = assertion.minimum - max(abs(assertion.minimum), 1) * 1_000
    else:
        value = 1e100
    return _replace_first(
        dataset,
        field,
        value,
        "boundary_extreme",
        f"Push {field} beyond its declared numeric boundary",
    )


def stale_timestamp_mutant(snapshot: CatalogSnapshot, dataset: DatasetSpec) -> Mutation:
    field = _find_assertion_field(dataset, "freshness")
    rows = deepcopy(dataset.rows)
    before = [row[field] for row in rows]
    stale_value = "1970-01-01T00:00:00+00:00"
    for row in rows:
        row[field] = stale_value
    return _build_mutation(
        operator="stale_timestamp",
        dataset=dataset,
        field=field,
        description=f"Move every {field} value far outside the freshness window",
        before=before,
        after=[stale_value] * len(rows),
        rows=rows,
    )


OPERATORS: tuple[Callable[[CatalogSnapshot, DatasetSpec], Mutation], ...] = (
    null_mutant,
    duplicate_mutant,
    orphan_fk_mutant,
    enum_mutant,
    boundary_extreme_mutant,
    stale_timestamp_mutant,
)


def generate_mutants(snapshot: CatalogSnapshot) -> tuple[list[Mutation], list[dict[str, str]]]:
    target = snapshot.datasets[snapshot.target_dataset]
    mutants: list[Mutation] = []
    unavailable: list[dict[str, str]] = []
    for operator in OPERATORS:
        try:
            mutants.append(operator(snapshot, target))
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            unavailable.append(
                {"operator": operator.__name__.removesuffix("_mutant"), "reason": str(exc)}
            )
    return mutants, unavailable
