from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from .models import AttemptResult

FIELDS = ("ts", "provider", "kind", "ok", "status", "elapsed_ms", "detail")


class RunLog:
    def __init__(self, path: str | Path = "pacification.log") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            with self.path.open("w", newline="", encoding="utf-8") as handle:
                csv.DictWriter(handle, fieldnames=FIELDS).writeheader()

    def write(self, result: AttemptResult) -> None:
        row = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "provider": result.provider,
            "kind": result.kind,
            "ok": result.ok,
            "status": result.status if result.status is not None else "",
            "elapsed_ms": result.elapsed_ms,
            "detail": result.detail.replace("\n", " ")[:300],
        }
        with self.path.open("a", newline="", encoding="utf-8") as handle:
            csv.DictWriter(handle, fieldnames=FIELDS).writerow(row)
