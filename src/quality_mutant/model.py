from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from hashlib import sha256
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def stable_hash(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def stable_rows_hash(rows: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> str:
    """Hash a row multiset without treating row order as a semantic change."""
    normalized = sorted(canonical_json(row) for row in rows)
    return stable_hash(normalized)


def parse_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    raise ValueError(f"Invalid boolean value: {value!r}")


@dataclass(frozen=True)
class FieldSpec:
    name: str
    type: str
    nullable: bool = True
    tags: tuple[str, ...] = ()
    terms: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> FieldSpec:
        return cls(
            name=value["name"],
            type=value.get("type", "VARCHAR"),
            nullable=parse_bool(value.get("nullable"), default=True),
            tags=tuple(value.get("tags", [])),
            terms=tuple(value.get("terms", [])),
        )


@dataclass(frozen=True)
class ForeignKeySpec:
    field: str
    target_dataset: str
    target_field: str
    name: str | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ForeignKeySpec:
        return cls(
            field=value["field"],
            target_dataset=value["target_dataset"],
            target_field=value["target_field"],
            name=value.get("name"),
        )


@dataclass(frozen=True)
class AssertionSpec:
    id: str
    kind: str
    dataset: str
    field: str | None = None
    values: tuple[Any, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    max_age_hours: float | None = None
    target_dataset: str | None = None
    target_field: str | None = None
    source: str = "fixture"

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> AssertionSpec:
        return cls(
            id=value["id"],
            kind=value["kind"],
            dataset=value["dataset"],
            field=value.get("field"),
            values=tuple(value.get("values", [])),
            minimum=value.get("minimum"),
            maximum=value.get("maximum"),
            max_age_hours=value.get("max_age_hours"),
            target_dataset=value.get("target_dataset"),
            target_field=value.get("target_field"),
            source=value.get("source", "fixture"),
        )


@dataclass(frozen=True)
class ContractSpec:
    id: str
    assertion_ids: tuple[str, ...]
    source: str = "fixture"

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ContractSpec:
        return cls(
            id=value["id"],
            assertion_ids=tuple(value.get("assertion_ids", [])),
            source=value.get("source", "fixture"),
        )


@dataclass
class DatasetSpec:
    urn: str
    name: str
    table: str
    fields: list[FieldSpec]
    rows: list[dict[str, Any]]
    primary_key: tuple[str, ...] = ()
    foreign_keys: tuple[ForeignKeySpec, ...] = ()
    tags: tuple[str, ...] = ()
    terms: tuple[str, ...] = ()
    assertions: list[AssertionSpec] = field(default_factory=list)
    contract: ContractSpec | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> DatasetSpec:
        return cls(
            urn=value["urn"],
            name=value["name"],
            table=value.get("table", value["name"]),
            fields=[FieldSpec.from_dict(item) for item in value.get("fields", [])],
            rows=[dict(item) for item in value.get("rows", [])],
            primary_key=tuple(value.get("primary_key", [])),
            foreign_keys=tuple(
                ForeignKeySpec.from_dict(item) for item in value.get("foreign_keys", [])
            ),
            tags=tuple(value.get("tags", [])),
            terms=tuple(value.get("terms", [])),
            assertions=[AssertionSpec.from_dict(item) for item in value.get("assertions", [])],
            contract=(ContractSpec.from_dict(value["contract"]) if value.get("contract") else None),
        )


@dataclass
class CatalogSnapshot:
    as_of: datetime
    target_dataset: str
    datasets: dict[str, DatasetSpec]
    source: str

    @classmethod
    def from_dict(cls, value: dict[str, Any], source: str = "fixture") -> CatalogSnapshot:
        parsed = [DatasetSpec.from_dict(item) for item in value.get("datasets", [])]
        names = [item.name for item in parsed]
        if len(names) != len(set(names)):
            raise ValueError("Dataset names must be unique")
        snapshot = cls(
            as_of=datetime.fromisoformat(value["as_of"]),
            target_dataset=value["target_dataset"],
            datasets={item.name: item for item in parsed},
            source=source,
        )
        snapshot.validate()
        return snapshot

    def validate(self) -> None:
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("as_of must include a timezone offset")
        if self.target_dataset not in self.datasets:
            raise ValueError(f"Unknown target_dataset: {self.target_dataset}")
        assertion_ids: set[str] = set()
        for dataset in self.datasets.values():
            if not dataset.urn or not dataset.name or not dataset.table:
                raise ValueError("Dataset urn, name, and table must be non-empty")
            field_names = [item.name for item in dataset.fields]
            if not field_names or any(not item for item in field_names):
                raise ValueError(f"Dataset {dataset.name} requires named fields")
            if len(field_names) != len(set(field_names)):
                raise ValueError(f"Duplicate fields in {dataset.name}")
            unknown_pk = set(dataset.primary_key) - set(field_names)
            if unknown_pk:
                raise ValueError(
                    f"Unknown primary key fields in {dataset.name}: {sorted(unknown_pk)}"
                )
            for row in dataset.rows:
                unknown_columns = set(row) - set(field_names)
                if unknown_columns:
                    raise ValueError(
                        f"Unknown row columns in {dataset.name}: {sorted(unknown_columns)}"
                    )
            for key in dataset.foreign_keys:
                if key.field not in field_names:
                    raise ValueError(f"Unknown foreign key field {dataset.name}.{key.field}")
                if key.target_dataset not in self.datasets:
                    raise ValueError(f"Unknown foreign key dataset {key.target_dataset}")
                target_fields = {item.name for item in self.datasets[key.target_dataset].fields}
                if key.target_field not in target_fields:
                    raise ValueError(
                        f"Unknown foreign key target {key.target_dataset}.{key.target_field}"
                    )
            local_ids: set[str] = set()
            for assertion in dataset.assertions:
                if not assertion.id:
                    raise ValueError(f"Dataset {dataset.name} has an assertion without an id")
                if assertion.id in assertion_ids or assertion.id in local_ids:
                    raise ValueError(f"Duplicate assertion id: {assertion.id}")
                if assertion.dataset != dataset.name:
                    raise ValueError(
                        f"Assertion {assertion.id} points to {assertion.dataset}, expected {dataset.name}"
                    )
                if assertion.field and assertion.field not in field_names:
                    raise ValueError(f"Unknown assertion field {dataset.name}.{assertion.field}")
                field_required = {
                    "not_null",
                    "unique",
                    "accepted_values",
                    "range",
                    "freshness",
                    "foreign_key",
                }
                if assertion.kind in field_required and not assertion.field:
                    raise ValueError(f"Assertion {assertion.id} requires a field")
                if assertion.kind == "accepted_values" and not assertion.values:
                    raise ValueError(f"Assertion {assertion.id} requires accepted values")
                if assertion.kind == "range":
                    if assertion.minimum is None and assertion.maximum is None:
                        raise ValueError(f"Assertion {assertion.id} requires a range boundary")
                    if (
                        assertion.minimum is not None
                        and assertion.maximum is not None
                        and assertion.minimum > assertion.maximum
                    ):
                        raise ValueError(f"Assertion {assertion.id} has an inverted range")
                if assertion.kind == "freshness" and (
                    assertion.max_age_hours is None or assertion.max_age_hours <= 0
                ):
                    raise ValueError(f"Assertion {assertion.id} requires max_age_hours > 0")
                if assertion.kind == "foreign_key":
                    if not assertion.target_dataset or not assertion.target_field:
                        raise ValueError(f"Assertion {assertion.id} requires a foreign-key target")
                    if assertion.target_dataset not in self.datasets:
                        raise ValueError(
                            f"Assertion {assertion.id} has unknown target {assertion.target_dataset}"
                        )
                    target_fields = {
                        item.name for item in self.datasets[assertion.target_dataset].fields
                    }
                    if assertion.target_field not in target_fields:
                        raise ValueError(
                            f"Assertion {assertion.id} has unknown target field "
                            f"{assertion.target_dataset}.{assertion.target_field}"
                        )
                local_ids.add(assertion.id)
            assertion_ids.update(local_ids)
            if dataset.contract:
                unknown = set(dataset.contract.assertion_ids) - local_ids
                if unknown:
                    raise ValueError(
                        f"Contract {dataset.contract.id} references unknown assertions: {sorted(unknown)}"
                    )

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["as_of"] = self.as_of.isoformat()
        return value

    @property
    def fingerprint(self) -> str:
        return stable_hash(self.as_dict())


@dataclass(frozen=True)
class CheckResult:
    assertion_id: str
    status: str
    observed: str


@dataclass(frozen=True)
class Mutation:
    operator: str
    dataset: str
    field: str
    description: str
    before: Any
    after: Any
    rows: tuple[dict[str, Any], ...]
    mutant_hash: str


@dataclass(frozen=True)
class MutationResult:
    operator: str
    mutant_hash: str
    equivalent: bool
    killed: bool
    killed_by: tuple[str, ...]
    checks: tuple[CheckResult, ...]


class BaselineFailure(RuntimeError):
    """Raised when the starting rows do not satisfy all declared checks."""
