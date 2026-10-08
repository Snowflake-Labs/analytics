"""Progress events: one JSON object per stdout line."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from typing import Any, TextIO

SCHEMA_VERSION = 1


class Events:
    def __init__(self, run_id: str, stream: TextIO | None = None) -> None:
        self.run_id = run_id
        self._stream = stream or sys.stdout

    def emit(self, step: str, status: str, **detail: Any) -> None:
        event = {
            "iwb": "event",
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "step": step,
            "status": status,
            "detail": detail,
        }
        self._stream.write(json.dumps(event, default=str) + "\n")
        self._stream.flush()
