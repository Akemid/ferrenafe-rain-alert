"""File-backed `SnapshotPublisher` for the public status page."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class JsonFileSnapshotPublisher:
    """`SnapshotPublisher` writing to a local path, for development and for
    keeping the default suite offline. Mirrors `json_alert_repository.py`."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def publish(self, document: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
