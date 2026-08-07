from __future__ import annotations

import json
from pathlib import Path

from quality_mutant.model import CatalogSnapshot


class FixtureAdapter:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> CatalogSnapshot:
        with self.path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
        return CatalogSnapshot.from_dict(payload, source=f"fixture:{self.path.name}")
