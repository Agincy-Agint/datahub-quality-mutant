from __future__ import annotations

from datetime import datetime
from typing import Any

UPSERT_MUTATION = """mutation UpsertQualityMutationCoverage($urn: String!, $input: UpsertCustomAssertionInput!) {
  upsertCustomAssertion(urn: $urn, input: $input) { urn }
}"""

REPORT_MUTATION = """mutation ReportQualityMutationCoverage($urn: String!, $result: AssertionResultInput!) {
  reportAssertionResult(urn: $urn, result: $result)
}"""


def build_custom_assertion_payload(report: dict[str, Any]) -> dict[str, Any]:
    summary = report["mutation_summary"]
    result_type = "SUCCESS" if summary["threshold_met"] else "FAILURE"
    assertion_urn = f"urn:li:assertion:quality-mutant-{report['report_hash'][:20]}"
    timestamp_millis = int(datetime.fromisoformat(report["as_of"]).timestamp() * 1000)
    return {
        "delivery_status": "PREPARED_NOT_SENT",
        "transport": "DataHub GraphQL API",
        "warning": "This file is inert. Quality Mutant never sends it.",
        "assertion_urn": assertion_urn,
        "declared_threshold": summary["threshold"],
        "observed_score": summary["score"],
        "result_type": result_type,
        "operations": [
            {
                "operation_name": "UpsertQualityMutationCoverage",
                "query": UPSERT_MUTATION,
                "variables": {
                    "urn": assertion_urn,
                    "input": {
                        "entityUrn": report["target_urn"],
                        "type": "Quality Mutation Coverage",
                        "description": (
                            "Fraction of non-equivalent adversarial data mutants detected by the "
                            "dataset's executable assertion set."
                        ),
                        "platform": {"name": "datahub-quality-mutant"},
                        "scope": "DATASET_ROWS",
                        "operator": "GREATER_THAN_OR_EQUAL_TO",
                        "aggregation": "IDENTITY",
                        "parameters": {
                            "value": {"value": str(summary["threshold"]), "type": "NUMBER"}
                        },
                        "nativeType": "quality_mutation_score",
                        "nativeParameters": [
                            {"key": "threshold", "value": str(summary["threshold"])},
                            {"key": "report_hash", "value": report["report_hash"]},
                        ],
                    },
                },
            },
            {
                "operation_name": "ReportQualityMutationCoverage",
                "query": REPORT_MUTATION,
                "variables": {
                    "urn": assertion_urn,
                    "result": {
                        "timestampMillis": timestamp_millis,
                        "type": result_type,
                        "properties": [
                            {"key": "mutation_score", "value": f"{summary['score']:.6f}"},
                            {"key": "threshold", "value": str(summary["threshold"])},
                            {"key": "killed", "value": str(summary["killed"])},
                            {"key": "survived", "value": str(summary["survived"])},
                            {"key": "report_hash", "value": report["report_hash"]},
                        ],
                    },
                },
            },
        ],
    }


def build_document_payload(report: dict[str, Any], markdown: str) -> dict[str, Any]:
    return {
        "delivery_status": "PREPARED_NOT_SENT",
        "transport": "official DataHub MCP tool",
        "tool": "save_document",
        "warning": "This file is inert. Quality Mutant never calls save_document.",
        "arguments": {
            "document_type": "Analysis",
            "title": f"Quality mutation coverage: {report['target_dataset']}",
            "content": markdown,
            "topics": ["data-quality", "mutation-testing", "quality-mutant"],
            "related_assets": [report["target_urn"]],
        },
    }
