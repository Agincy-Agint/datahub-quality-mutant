from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from quality_mutant.adapters.fixture import FixtureAdapter
from quality_mutant.engine import _duck_type, run_campaign
from quality_mutant.model import AssertionSpec, BaselineFailure, parse_bool, stable_rows_hash
from quality_mutant.mutants import generate_mutants
from quality_mutant.payloads import build_custom_assertion_payload

ROOT = Path(__file__).parents[1]


def snapshot():
    return FixtureAdapter(ROOT / "fixtures" / "ecommerce.json").load()


def test_campaign_finds_green_but_missing_foreign_key_check() -> None:
    report = run_campaign(snapshot())
    summary = report["mutation_summary"]
    assert report["baseline"]["status"] == "PASS"
    assert summary == {
        "requested_operators": 6,
        "generated": 6,
        "equivalent_excluded": 0,
        "counted": 6,
        "killed": 5,
        "survived": 1,
        "score": pytest.approx(5 / 6),
        "threshold": 1.0,
        "threshold_met": False,
    }
    survivor = report["survivors"][0]
    assert survivor["operator"] == "orphan_fk"
    assert survivor["suggestion"]["kind"] == "foreign_key"
    orphan = next(item for item in report["kill_matrix"] if item["operator"] == "orphan_fk")
    assert orphan["checks"]["orders.customer_id.not_null"] == "PASS"
    assert not orphan["killed"]


def test_campaign_is_deterministic() -> None:
    first = run_campaign(snapshot())
    second = run_campaign(snapshot())
    assert first["report_hash"] == second["report_hash"]
    assert [item["mutant_hash"] for item in first["kill_matrix"]] == [
        item["mutant_hash"] for item in second["kill_matrix"]
    ]


def test_baseline_must_pass() -> None:
    value = snapshot()
    value.datasets["orders"].rows[0]["status"] = "invalid"
    with pytest.raises(BaselineFailure, match="Baseline must pass"):
        run_campaign(value)


def test_result_payload_fails_when_threshold_not_met() -> None:
    payload = build_custom_assertion_payload(run_campaign(snapshot()))
    assert payload["delivery_status"] == "PREPARED_NOT_SENT"
    assert payload["result_type"] == "FAILURE"
    report_result = payload["operations"][1]["variables"]["result"]
    assert report_result["type"] == "FAILURE"
    assert payload["operations"][0]["variables"]["input"]["parameters"]["value"] == {
        "value": "1.0",
        "type": "NUMBER",
    }


def test_result_payload_succeeds_only_when_declared_threshold_is_met() -> None:
    payload = build_custom_assertion_payload(run_campaign(snapshot(), threshold=0.8))
    assert payload["result_type"] == "SUCCESS"
    assert payload["operations"][1]["variables"]["result"]["type"] == "SUCCESS"


def test_unsupported_assertion_is_never_green() -> None:
    value = snapshot()
    value.datasets["orders"].assertions.append(
        AssertionSpec(
            id="orders.external",
            kind="external",
            dataset="orders",
            source="mcp:get_dataset_assertions",
        )
    )
    report = run_campaign(value)
    check = next(
        item for item in report["baseline"]["checks"] if item["assertion_id"] == "orders.external"
    )
    assert check["status"] == "NOT_EXECUTED"
    assert report["baseline"]["not_executed"] == 1
    assert report["baseline"]["status"] == "PASS_WITH_NOT_EXECUTED"


def test_no_executable_baseline_fails_transparently() -> None:
    value = snapshot()
    value.datasets["orders"].assertions = [
        AssertionSpec(id="orders.external", kind="external", dataset="orders")
    ]
    with pytest.raises(BaselineFailure, match="no executable assertions"):
        run_campaign(value)


def test_schema_constraints_can_drive_null_and_duplicate_mutants() -> None:
    value = snapshot()
    value.datasets["orders"].assertions = [
        item
        for item in value.datasets["orders"].assertions
        if item.kind not in {"not_null", "unique"}
    ]
    mutants, unavailable = generate_mutants(value)
    operators = {item.operator for item in mutants}
    assert {"null", "duplicate"} <= operators
    assert not any(item["operator"] in {"null", "duplicate"} for item in unavailable)


def test_row_hash_ignores_order_but_preserves_multiplicity() -> None:
    rows = [{"id": 1}, {"id": 2}]
    assert stable_rows_hash(rows) == stable_rows_hash(list(reversed(rows)))
    assert stable_rows_hash(rows) != stable_rows_hash([*rows, {"id": 2}])


def test_nullability_parser_and_unknown_duck_type_fail_closed() -> None:
    assert parse_bool("false") is False
    assert parse_bool("true") is True
    with pytest.raises(ValueError, match="Invalid boolean"):
        parse_bool("sometimes")
    with pytest.raises(ValueError, match="Unsupported data type"):
        _duck_type("GEOGRAPHY")


def test_validation_rejects_bad_contract_reference() -> None:
    value = snapshot()
    value.datasets["orders"].contract = deepcopy(value.datasets["orders"].contract)
    object.__setattr__(
        value.datasets["orders"].contract,
        "assertion_ids",
        (*value.datasets["orders"].contract.assertion_ids, "missing"),
    )
    with pytest.raises(ValueError, match="unknown assertions"):
        value.validate()
