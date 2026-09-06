"""In-memory `AlertRepository` (design.md 8.3).

The whole query rule lives here, and `JsonFileAlertRepository` composes this
class rather than reimplementing it — two copies of "filter by city, filter
by window-start range, sort ascending" would be exactly the kind of
duplication that diverges silently and re-alerts a community.

The store stays deliberately dumb: overlap and escalation are *rules* and
belong in `AlertPolicy` / `should_send`, so this is a plain key-range read
(design.md 4.1). That is both the cheapest DynamoDB access pattern in change
3 and trivially checkable here.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from rain_alert.domain.messages import AlertRecord, OutageRecord


class InMemoryAlertRepository:
    """`AlertRepository` with no I/O. Loses everything on exit, which is why
    the CLI uses the file-backed adapter instead."""

    def __init__(
        self,
        alerts: Iterable[AlertRecord] = (),
        active_outage: OutageRecord | None = None,
    ) -> None:
        self._alerts: list[AlertRecord] = list(alerts)
        self._active_outage = active_outage

    # --- community-alert dedup ---

    def alerts_with_window_start_between(
        self, city_slug: str, earliest_start: datetime, latest_start: datetime
    ) -> tuple[AlertRecord, ...]:
        """Sent alerts for `city_slug` whose window starts in the inclusive
        range, ascending by window start."""
        matches = [
            record
            for record in self._alerts
            if record.city_slug == city_slug and earliest_start <= record.window.start <= latest_start
        ]
        return tuple(sorted(matches, key=lambda record: record.window.start))

    def record_alert(self, record: AlertRecord) -> None:
        self._alerts.append(record)

    # --- outage-notice dedup state ---

    def get_active_outage(self, city_slug: str) -> OutageRecord | None:
        if self._active_outage is not None and self._active_outage.city_slug == city_slug:
            return self._active_outage
        return None

    def save_active_outage(self, record: OutageRecord) -> None:
        """Upsert: at most one active outage per city (design.md 4.1)."""
        self._active_outage = record

    def clear_active_outage(self, city_slug: str) -> None:
        if self._active_outage is not None and self._active_outage.city_slug == city_slug:
            self._active_outage = None

    # --- state access for the file-backed adapter ---

    def snapshot(self) -> tuple[tuple[AlertRecord, ...], OutageRecord | None]:
        """Everything held, for a caller that needs to persist it."""
        return tuple(self._alerts), self._active_outage
