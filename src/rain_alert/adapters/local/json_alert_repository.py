"""File-backed `AlertRepository` over a gitignored local JSON file
(design.md 8.3, 4.1).

This adapter exists for one reason: **each CLI run is a fresh process.**
Without durable state, dedup and outage-notice dedup could not work at all —
three consecutive dual-outage cycles would emit three notices instead of
one, and a community would be re-alerted for a window already sent.

The document holds the same two collections as the DynamoDB table in change
3 (`alerts`, `active_outage`), through `adapters/serialization.py`, so the
two adapters are shape-identical.

Two behaviours are deliberate:

- **Writes are atomic**: serialize fully, write to a temp file in the *same
  directory*, then `os.replace`. Same directory because `os.replace` is only
  atomic within one filesystem. A crash or a serialization failure therefore
  leaves the previous state readable rather than a truncated document.
- **A corrupt file raises.** Starting from empty state would look like "no
  alert has ever been sent" and re-alert the community for a window that was
  already delivered. The file is disposable and gitignored, so failing
  loudly is cheap; guessing is not.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from rain_alert.adapters.local.in_memory_alert_repository import InMemoryAlertRepository
from rain_alert.adapters.serialization import (
    alert_record_from_dict,
    alert_record_to_dict,
    outage_record_from_dict,
    outage_record_to_dict,
)
from rain_alert.domain.messages import AlertRecord, OutageRecord

DEFAULT_STATE_FILE = Path(".local-state") / "alerts.json"


class JsonFileAlertRepository:
    """`AlertRepository` persisted to one JSON document."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._state: InMemoryAlertRepository | None = None

    # --- community-alert dedup ---

    def alerts_with_window_start_between(
        self, city_slug: str, earliest_start: datetime, latest_start: datetime
    ) -> tuple[AlertRecord, ...]:
        return self._loaded().alerts_with_window_start_between(city_slug, earliest_start, latest_start)

    def record_alert(self, record: AlertRecord) -> None:
        self._mutate(lambda state: state.record_alert(record))

    # --- outage-notice dedup state ---

    def get_active_outage(self, city_slug: str) -> OutageRecord | None:
        return self._loaded().get_active_outage(city_slug)

    def save_active_outage(self, record: OutageRecord) -> None:
        self._mutate(lambda state: state.save_active_outage(record))

    def clear_active_outage(self, city_slug: str) -> None:
        self._mutate(lambda state: state.clear_active_outage(city_slug))

    # --- persistence ---

    def _loaded(self) -> InMemoryAlertRepository:
        if self._state is None:
            self._state = self._read()
        return self._state

    def _mutate(self, change: Callable[[InMemoryAlertRepository], None]) -> None:
        """Apply `change` to a copy, persist it, and only then adopt it.

        Mutating in place first would leave the in-memory state describing an
        alert that was never written if serialization failed — and the next
        read of that state would claim the community had been told.
        """
        alerts, active_outage = self._loaded().snapshot()
        candidate = InMemoryAlertRepository(alerts=alerts, active_outage=active_outage)
        change(candidate)
        self._flush(candidate)
        self._state = candidate

    def _read(self) -> InMemoryAlertRepository:
        if not self._path.exists():
            return InMemoryAlertRepository()
        try:
            document: Any = json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{self._path} is not valid JSON: {exc}") from exc
        if not isinstance(document, dict):
            raise ValueError(f"{self._path} must hold a JSON object, got {type(document).__name__}")

        active = document.get("active_outage")
        return InMemoryAlertRepository(
            alerts=[alert_record_from_dict(entry) for entry in document.get("alerts", ())],
            active_outage=None if active is None else outage_record_from_dict(active),
        )

    def _flush(self, state: InMemoryAlertRepository) -> None:
        # KNOWN, RECORDED: `alerts` grows without bound and the whole document
        # is rewritten on every send, so both the file and the write cost grow
        # forever. Tolerable here — one send per six-hour cycle, only on an
        # escalation, and the file is gitignored and disposable. The proposed
        # retention rule is in tasks.md's Open Items; it is not implemented in
        # this slice because it needs a clock the port does not carry, and
        # change 3's DynamoDB adapter should use a TTL attribute rather than a
        # rewrite, so the mechanism would not be shared anyway.
        alerts, active_outage = state.snapshot()
        document = {
            "alerts": [alert_record_to_dict(record) for record in alerts],
            "active_outage": None if active_outage is None else outage_record_to_dict(active_outage),
        }
        # Serialize before touching the filesystem: a failure here must not
        # have already replaced the previous state.
        payload = json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        self._write_atomically(payload)

    def _write_atomically(self, payload: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Same directory as the target: `os.replace` is only atomic within one
        # filesystem.
        handle, temporary = tempfile.mkstemp(dir=self._path.parent, prefix=self._path.name, suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
