from __future__ import annotations

from typing import Any


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["mutation_summary"]
    lines = [
        f"# Quality mutation coverage: {report['target_dataset']}",
        "",
        (
            f"**Result:** {summary['killed']}/{summary['counted']} non-equivalent mutants "
            f"killed ({summary['score']:.1%}); threshold {summary['threshold']:.1%} "
            f"{'met' if summary['threshold_met'] else 'not met'}."
        ),
        "",
        (
            "The baseline passed before mutation. Unsupported live assertion semantics are shown "
            "as `NOT_EXECUTED`; they are never counted as passing."
        ),
        "",
        "## Operator kill matrix",
        "",
        "| Operator | Result | Killed by | Mutant hash |",
        "|---|---|---|---|",
    ]
    for item in report["kill_matrix"]:
        result = "EXCLUDED" if item["equivalent"] else ("KILLED" if item["killed"] else "SURVIVED")
        lines.append(
            f"| `{item['operator']}` | {result} | "
            f"{', '.join(item['killed_by']) or '—'} | `{item['mutant_hash'][:16]}` |"
        )
    if report["unavailable_operators"]:
        lines.extend(["", "## Unavailable operators", ""])
        for item in report["unavailable_operators"]:
            lines.append(f"- `{item['operator']}`: {item['reason']}")
    lines.extend(["", "## Surviving counterexamples", ""])
    if not report["survivors"]:
        lines.append("None.")
    for item in report["survivors"]:
        suggestion = item["suggestion"]
        lines.append(
            f"- `{item['operator']}` survived. Suggested `{suggestion['kind']}` check: "
            f"{suggestion['rationale']}"
        )
        if suggestion.get("minimal_sql"):
            lines.extend(["", "```sql", suggestion["minimal_sql"], "```"])
    lines.extend(
        [
            "",
            "## Evidence",
            "",
            f"- Fixture/catalog hash: `{report['fixture_hash']}`",
            f"- Report hash: `{report['report_hash']}`",
            f"- Source: `{report['source']}`",
            f"- Evaluated at fixed timestamp: `{report['as_of']}`",
            "- Writeback state: **prepared, not sent**",
            "",
        ]
    )
    return "\n".join(lines)
