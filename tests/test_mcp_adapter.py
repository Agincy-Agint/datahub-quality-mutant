from __future__ import annotations

from datetime import UTC, datetime

import pytest

from quality_mutant.adapters.mcp import (
    MCPMetadataAdapter,
    MCPProtocolError,
    RowsInput,
    decode_tool_result,
)

ORDERS_URN = "urn:li:dataset:(urn:li:dataPlatform:duckdb,showcase.orders,PROD)"
CUSTOMERS_URN = "urn:li:dataset:(urn:li:dataPlatform:duckdb,showcase.customers,PROD)"


class FakeClient:
    def __init__(self, tools=None):
        self.tools = tools or {"get_entities", "list_schema_fields", "get_dataset_assertions"}
        self.calls: list[tuple[str, dict]] = []

    def list_tools(self):
        return self.tools

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        urn = arguments.get("urn") or arguments.get("urns")
        if name == "get_entities" and urn == ORDERS_URN:
            return {
                "urn": urn,
                "name": "orders",
                "tags": {"tags": [{"tag": {"properties": {"name": "Tier1"}}}]},
                "schemaMetadata": {
                    "primaryKeys": ["order_id"],
                    "foreignKeys": [
                        {
                            "name": "orders_customer_fk",
                            "sourceFields": [{"fieldPath": "customer_id"}],
                            "foreignFields": [{"fieldPath": "customer_id"}],
                            "foreignDataset": {"urn": CUSTOMERS_URN},
                        }
                    ],
                },
            }
        if name == "get_entities":
            return {
                "urn": urn,
                "name": "customers",
                "schemaMetadata": {"primaryKeys": ["customer_id"]},
            }
        if name == "list_schema_fields" and urn == ORDERS_URN:
            return {
                "fields": [
                    {"fieldPath": "order_id", "nativeDataType": "BIGINT", "nullable": False},
                    {"fieldPath": "customer_id", "nativeDataType": "BIGINT", "nullable": False},
                ]
            }
        if name == "list_schema_fields":
            return {
                "fields": [
                    {"fieldPath": "customer_id", "nativeDataType": "BIGINT", "nullable": False}
                ]
            }
        if name == "get_dataset_assertions" and urn == ORDERS_URN:
            return {
                "success": True,
                "data": {
                    "assertions": [
                        {
                            "urn": "urn:li:assertion:orders-id-not-null",
                            "type": "CUSTOM",
                            "column": "order_id",
                            "definition": {"nativeType": "expect_column_values_to_not_be_null"},
                        }
                    ]
                },
            }
        return {"success": True, "data": {"assertions": []}}


def rows_input() -> RowsInput:
    return RowsInput(
        as_of=datetime(2026, 8, 7, 12, tzinfo=UTC),
        by_urn={
            ORDERS_URN: ("orders", [{"order_id": 1, "customer_id": 10}]),
            CUSTOMERS_URN: ("customers", [{"customer_id": 10}]),
        },
    )


def test_real_adapter_uses_official_tool_names_and_shapes() -> None:
    client = FakeClient()
    snapshot = MCPMetadataAdapter(client).load(ORDERS_URN, rows_input())
    assert snapshot.target_dataset == "orders"
    assert snapshot.datasets["orders"].foreign_keys[0].target_dataset == "customers"
    assert snapshot.datasets["orders"].assertions[0].kind == "not_null"
    assert {name for name, _ in client.calls} == {
        "get_entities",
        "list_schema_fields",
        "get_dataset_assertions",
    }
    assert (
        "exact Data Contract membership is not exposed"
        in snapshot.datasets["orders"].contract.source
    )


def test_adapter_requires_quality_tool() -> None:
    client = FakeClient({"get_entities", "list_schema_fields"})
    with pytest.raises(MCPProtocolError, match="DATA_QUALITY_TOOLS_ENABLED"):
        MCPMetadataAdapter(client).load(ORDERS_URN, rows_input())


def test_decode_tool_result_handles_structured_and_text() -> None:
    assert decode_tool_result({"structuredContent": {"result": {"ok": True}}}) == {"ok": True}
    assert decode_tool_result({"content": [{"type": "text", "text": '{"ok": true}'}]}) == {
        "ok": True
    }
