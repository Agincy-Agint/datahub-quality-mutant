from __future__ import annotations

from typing import Any

from quality_mutant.model import CatalogSnapshot


def suggest_for_survivor(snapshot: CatalogSnapshot, operator: str) -> dict[str, Any]:
    target = snapshot.datasets[snapshot.target_dataset]
    if operator == "orphan_fk" and target.foreign_keys:
        key = target.foreign_keys[0]
        return {
            "kind": "foreign_key",
            "dataset": target.name,
            "field": key.field,
            "target_dataset": key.target_dataset,
            "target_field": key.target_field,
            "rationale": "The field remained non-null but pointed to no parent row.",
            "minimal_sql": (
                f"SELECT count(*) FROM {target.table} s LEFT JOIN "
                f"{snapshot.datasets[key.target_dataset].table} t "
                f"ON s.{key.field}=t.{key.target_field} "
                f"WHERE s.{key.field} IS NOT NULL AND t.{key.target_field} IS NULL"
            ),
        }
    mapping = {
        "null": "not_null",
        "duplicate": "unique",
        "enum": "accepted_values",
        "boundary_extreme": "range",
        "stale_timestamp": "freshness",
    }
    return {
        "kind": mapping.get(operator, "custom"),
        "dataset": target.name,
        "rationale": f"The current assertion set did not detect the {operator} counterexample.",
    }
