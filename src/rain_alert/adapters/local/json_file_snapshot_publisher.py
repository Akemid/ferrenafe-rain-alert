"""File-backed `SnapshotPublisher` for the public status page."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: Environment variable for the local snapshot path (development, and the
#: default offline suite). Mirrors `s3_snapshot_publisher.py`'s pair.
SNAPSHOT_PATH_ENV_VAR = "RAIN_ALERT_SNAPSHOT_PATH"


class JsonFileSnapshotPublisher:
    """`SnapshotPublisher` writing to a local path, for development and for
    keeping the default suite offline.

    Mirrors `json_alert_repository.py` in its constructor and path shape, but
    writes directly rather than atomically. Atomic writes are necessary there to
    prevent corruption on dedup failure; here, the production path is S3 with
    atomic `PutObject`, and a truncated local file surfaces as the page's
    "could not load" error rather than as wrong data.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def publish(self, document: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
