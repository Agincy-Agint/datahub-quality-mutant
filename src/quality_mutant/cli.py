from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from quality_mutant.adapters.fixture import FixtureAdapter
from quality_mutant.adapters.mcp import MCPMetadataAdapter, MCPStdioClient, RowsInput
from quality_mutant.engine import run_campaign
from quality_mutant.payloads import build_custom_assertion_payload, build_document_payload
from quality_mutant.render import render_markdown


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_outputs(snapshot, output: Path, threshold: float) -> dict[str, Any]:
    report = run_campaign(snapshot, threshold=threshold)
    markdown = render_markdown(report)
    output.mkdir(parents=True, exist_ok=True)
    _write_json(output / "mutation-report.json", report)
    (output / "mutation-report.md").write_text(markdown, encoding="utf-8")
    _write_json(
        output / "datahub-custom-assertion-result.prepared.json",
        build_custom_assertion_payload(report),
    )
    _write_json(
        output / "datahub-coverage-document.prepared.json",
        build_document_payload(report, markdown),
    )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="quality-mutant",
        description="Measure whether DataHub assertions kill adversarial data mutants.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    fixture = sub.add_parser("run-fixture", help="Run from a deterministic fixture")
    fixture.add_argument("--fixture", type=Path, required=True)
    fixture.add_argument("--output", type=Path, required=True)
    fixture.add_argument("--threshold", type=float, default=1.0)

    mcp = sub.add_parser("run-mcp", help="Read catalog metadata via official DataHub MCP")
    mcp.add_argument("--urn", required=True)
    mcp.add_argument(
        "--rows",
        type=Path,
        required=True,
        help="Explicit local data-plane rows; MCP does not return table data",
    )
    mcp.add_argument("--output", type=Path, required=True)
    mcp.add_argument("--threshold", type=float, default=1.0)
    mcp.add_argument("--mcp-command", default="uvx mcp-server-datahub@latest")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 0 <= args.threshold <= 1:
        raise SystemExit("--threshold must be between 0 and 1")
    if args.command == "run-fixture":
        snapshot = FixtureAdapter(args.fixture).load()
    else:
        rows = RowsInput.load(args.rows)
        with MCPStdioClient(args.mcp_command) as client:
            snapshot = MCPMetadataAdapter(client).load(args.urn, rows)
    report = _write_outputs(snapshot, args.output, args.threshold)
    summary = report["mutation_summary"]
    print(
        f"baseline=PASS killed={summary['killed']}/{summary['counted']} "
        f"score={summary['score']:.1%} threshold_met={summary['threshold_met']} "
        f"writeback=PREPARED_NOT_SENT"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
