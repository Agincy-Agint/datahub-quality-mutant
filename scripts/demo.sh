#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

python_bin="${QUALITY_MUTANT_PYTHON:-$project_dir/.venv/bin/python}"
if [[ ! -x "$python_bin" ]]; then
  echo "Missing virtual environment. Run: python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'" >&2
  exit 2
fi

"$python_bin" -m quality_mutant.cli run-fixture \
  --fixture fixtures/ecommerce.json \
  --output sample-output \
  --threshold 1.0

"$python_bin" - <<'PY'
import json
from pathlib import Path

report = json.loads(Path("sample-output/mutation-report.json").read_text())
print("\noperator           result     killed by")
print("-----------------  ---------  --------------------------------")
for item in report["kill_matrix"]:
    result = "KILLED" if item["killed"] else "SURVIVED"
    print(f"{item['operator']:<17}  {result:<9}  {', '.join(item['killed_by']) or 'none'}")
print("\nSurvivor suggestion:")
print(report["survivors"][0]["suggestion"]["minimal_sql"])
PY

