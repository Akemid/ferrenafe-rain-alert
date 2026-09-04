"""`OutageRecord.is_dual` — the only behaviour in `domain/messages.py`
(design.md section 3, mirrors design spec 7.2/7.4).

Every other type in that module is a frozen dataclass with no behaviour, so
the field-presence tests that used to live here were pruned as tautological:
they only asserted that a frozen dataclass returns what was passed in, which
`mypy --strict` already covers for field renames.
"""

from datetime import UTC, datetime, timedelta

from rain_alert.domain.messages import OutageRecord
from rain_alert.domain.values import SourceName


def _utc(hour: int) -> datetime:
    return datetime(2026, 9, 3, 0, tzinfo=UTC) + timedelta(hours=hour)


class TestOutageRecord:
    def test_is_dual_true_when_two_sources_unavailable(self) -> None:
        record = OutageRecord(
            city_slug="ferrenafe",
            unavailable_sources=frozenset({SourceName.SENAMHI, SourceName.OPEN_METEO}),
            opened_at=_utc(0),
            notified_at=_utc(0),
        )
        assert record.is_dual() is True

    def test_is_dual_false_when_one_source_unavailable(self) -> None:
        record = OutageRecord(
            city_slug="ferrenafe",
            unavailable_sources=frozenset({SourceName.SENAMHI}),
            opened_at=_utc(0),
            notified_at=None,
        )
        assert record.is_dual() is False
