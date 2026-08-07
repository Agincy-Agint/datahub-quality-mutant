from __future__ import annotations

import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, Self

from quality_mutant.model import (
    AssertionSpec,
    CatalogSnapshot,
    ContractSpec,
    DatasetSpec,
    FieldSpec,
    ForeignKeySpec,
    parse_bool,
)

READ_TOOLS = ("get_entities", "list_schema_fields", "get_dataset_assertions")


class ToolCaller(Protocol):
    def list_tools(self) -> set[str]: ...

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


class MCPProtocolError(RuntimeError):
    pass


class MCPStdioClient:
    """Small synchronous MCP stdio client for the official DataHub server."""

    def __init__(self, command: str):
        self.command = command
        self.process: subprocess.Popen[str] | None = None
        self.next_id = 1

    def __enter__(self) -> Self:
        environment = dict(os.environ)
        environment["TOOLS_IS_MUTATION_ENABLED"] = "false"
        environment["DATA_QUALITY_TOOLS_ENABLED"] = "true"
        self.process = subprocess.Popen(
            shlex.split(self.command),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
            bufsize=1,
            env=environment,
        )
        self._request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "datahub-quality-mutant", "version": "0.1.0"},
            },
        )
        self._notify("notifications/initialized", {})
        return self

    def __exit__(self, *args: object) -> None:
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)

    def _send(self, payload: dict[str, Any]) -> None:
        if not self.process or not self.process.stdin:
            raise MCPProtocolError("MCP process is not running")
        self.process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
        self.process.stdin.flush()

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method: str, params: dict[str, Any]) -> Any:
        request_id = self.next_id
        self.next_id += 1
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        if not self.process or not self.process.stdout:
            raise MCPProtocolError("MCP process has no stdout")
        while True:
            line = self.process.stdout.readline()
            if line == "":
                code = self.process.poll()
                raise MCPProtocolError(f"MCP server closed stdout (exit={code})")
            try:
                response = json.loads(line)
            except json.JSONDecodeError:
                continue
            if response.get("id") != request_id:
                continue
            if response.get("error"):
                raise MCPProtocolError(f"MCP error: {response['error']}")
            return response.get("result")

    def list_tools(self) -> set[str]:
        result = self._request("tools/list", {})
        return {item["name"] for item in result.get("tools", [])}

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise MCPProtocolError(f"DataHub MCP tool {name} failed: {result.get('content')}")
        return decode_tool_result(result)


def decode_tool_result(result: dict[str, Any]) -> Any:
    structured = result.get("structuredContent")
    if structured is not None:
        if isinstance(structured, dict) and "result" in structured and len(structured) == 1:
            return structured["result"]
        return structured
    text_parts = [
        item.get("text", "") for item in result.get("content", []) if item.get("type") == "text"
    ]
    text = "".join(text_parts).strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _names(wrapper: Any, child_key: str, entity_key: str) -> tuple[str, ...]:
    if not isinstance(wrapper, dict):
        return ()
    output: list[str] = []
    for item in wrapper.get(child_key, []) or []:
        entity = item.get(entity_key, {}) if isinstance(item, dict) else {}
        properties = entity.get("properties", {}) or {}
        name = properties.get("name") or entity.get("name") or entity.get("urn")
        if name:
            output.append(str(name))
    return tuple(output)


def _field_from_mcp(value: dict[str, Any]) -> FieldSpec:
    field_type = value.get("type")
    if isinstance(field_type, dict):
        field_type = field_type.get("type") or field_type.get("nativeType")
    return FieldSpec(
        name=value.get("fieldPath") or value.get("name"),
        type=str(value.get("nativeDataType") or field_type or "VARCHAR"),
        nullable=parse_bool(value.get("nullable"), default=True),
        tags=_names(value.get("tags"), "tags", "tag"),
        terms=_names(value.get("glossaryTerms"), "terms", "term"),
    )


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _first(value: Any, key: str) -> Any:
    return next((item[key] for item in _walk(value) if key in item and item[key] is not None), None)


def assertion_from_mcp(value: dict[str, Any], dataset_name: str) -> AssertionSpec:
    definition = value.get("definition") or {}
    operator = str(_first(definition, "operator") or "").upper()
    native_type = str(_first(definition, "nativeType") or "").lower()
    assertion_type = str(value.get("type") or "").upper()
    field = value.get("column") or _first(definition, "path")
    kind = "external"
    if (
        "NOT_NULL" in operator
        or "not_null" in native_type
        or "not_be_null" in native_type
        or "notnull" in native_type
    ):
        kind = "not_null"
    elif "UNIQUE" in operator or "unique" in native_type:
        kind = "unique"
    elif "IN_SET" in operator or "accepted_values" in native_type:
        kind = "accepted_values"
    elif "BETWEEN" in operator or "range" in native_type:
        kind = "range"
    elif assertion_type == "FRESHNESS" or "freshness" in native_type:
        kind = "external_freshness"
    return AssertionSpec(
        id=value.get("urn") or f"mcp:{dataset_name}:{kind}:{field or 'dataset'}",
        kind=kind,
        dataset=dataset_name,
        field=field,
        source="mcp:get_dataset_assertions",
    )


@dataclass(frozen=True)
class RowsInput:
    as_of: datetime
    by_urn: dict[str, tuple[str, list[dict[str, Any]]]]

    @classmethod
    def load(cls, path: Path) -> RowsInput:
        with path.open(encoding="utf-8") as handle:
            value = json.load(handle)
        as_of = datetime.fromisoformat(value["as_of"])
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("Rows input as_of must include a timezone offset")
        by_urn = {
            item["urn"]: (item.get("table", item["name"]), [dict(row) for row in item["rows"]])
            for item in value.get("datasets", [])
        }
        return cls(as_of=as_of, by_urn=by_urn)


class MCPMetadataAdapter:
    """Read catalog metadata from MCP and join it to explicitly supplied data-plane rows."""

    def __init__(self, client: ToolCaller):
        self.client = client

    def load(self, target_urn: str, rows: RowsInput) -> CatalogSnapshot:
        available = self.client.list_tools()
        missing = set(READ_TOOLS) - available
        if missing:
            raise MCPProtocolError(
                "Official DataHub MCP tools unavailable: "
                + ", ".join(sorted(missing))
                + ". get_dataset_assertions requires DATA_QUALITY_TOOLS_ENABLED=true."
            )
        target_entity = self.client.call_tool("get_entities", {"urns": target_urn})
        schema = (target_entity or {}).get("schemaMetadata") or {}
        foreign_urns = [
            item.get("foreignDataset", {}).get("urn")
            for item in schema.get("foreignKeys", []) or []
            if item.get("foreignDataset", {}).get("urn")
        ]
        urns = [target_urn, *foreign_urns]
        datasets: list[DatasetSpec] = []
        for urn in urns:
            if urn not in rows.by_urn:
                raise ValueError(
                    f"No data-plane rows supplied for {urn}; MCP provides metadata, not table data"
                )
            entity = (
                target_entity
                if urn == target_urn
                else self.client.call_tool("get_entities", {"urns": urn})
            )
            schema_payload = (entity or {}).get("schemaMetadata") or {}
            fields_payload = self.client.call_tool(
                "list_schema_fields", {"urn": urn, "limit": 100, "offset": 0}
            )
            assertions_payload = self.client.call_tool(
                "get_dataset_assertions", {"urn": urn, "start": 0, "count": 20}
            )
            name = (
                (entity or {}).get("name")
                or ((entity or {}).get("properties") or {}).get("name")
                or urn.rsplit(",", 2)[-2]
            )
            table, data_rows = rows.by_urn[urn]
            assertions = [
                assertion_from_mcp(item, name)
                for item in ((assertions_payload or {}).get("data") or {}).get("assertions", [])
            ]
            foreign_keys: list[ForeignKeySpec] = []
            for foreign in schema_payload.get("foreignKeys", []) or []:
                target = (foreign.get("foreignDataset") or {}).get("urn")
                source_fields = foreign.get("sourceFields") or []
                target_fields = foreign.get("foreignFields") or []
                if target and source_fields and target_fields:
                    foreign_keys.append(
                        ForeignKeySpec(
                            field=source_fields[0]["fieldPath"],
                            target_dataset=target,
                            target_field=target_fields[0]["fieldPath"],
                            name=foreign.get("name"),
                        )
                    )
            datasets.append(
                DatasetSpec(
                    urn=urn,
                    name=name,
                    table=table,
                    fields=[_field_from_mcp(item) for item in fields_payload.get("fields", [])],
                    rows=data_rows,
                    primary_key=tuple(schema_payload.get("primaryKeys", []) or []),
                    foreign_keys=tuple(foreign_keys),
                    tags=_names((entity or {}).get("tags"), "tags", "tag"),
                    terms=_names((entity or {}).get("glossaryTerms"), "terms", "term"),
                    assertions=assertions,
                    contract=ContractSpec(
                        id=f"asset-assertion-bundle:{urn}",
                        assertion_ids=tuple(item.id for item in assertions),
                        source=(
                            "mcp:get_dataset_assertions; exact Data Contract membership is not exposed "
                            "by the current official MCP server"
                        ),
                    ),
                )
            )
        # Resolve FK dataset names after every entity has been read.
        names_by_urn = {item.urn: item.name for item in datasets}
        for dataset in datasets:
            dataset.foreign_keys = tuple(
                ForeignKeySpec(
                    field=key.field,
                    target_dataset=names_by_urn.get(key.target_dataset, key.target_dataset),
                    target_field=key.target_field,
                    name=key.name,
                )
                for key in dataset.foreign_keys
            )
        target_name = names_by_urn[target_urn]
        snapshot = CatalogSnapshot(
            as_of=rows.as_of,
            target_dataset=target_name,
            datasets={item.name: item for item in datasets},
            source="official-datahub-mcp+explicit-data-plane-rows",
        )
        snapshot.validate()
        return snapshot
