"""Domain value-object behaviour: `is_escalation` ranking, `Coordinates`
range checks, and the `TimeWindow` invariants and sort key (design.md section
3, D1, D7).

The per-enum "these are the literal values" tests were pruned as
tautological: they restated the enum definition without asserting behaviour,
and `mypy --strict` already catches a renamed member.
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from rain_alert.domain.values import Coordinates, Level, TimeWindow, is_escalation

LIMA_OFFSET = timezone(timedelta(hours=-5))


def _utc(hour: int) -> datetime:
    return datetime(2026, 9, 3, 0, tzinfo=UTC) + timedelta(hours=hour)


class TestIsEscalation:
    @pytest.mark.parametrize(
        ("previous", "candidate", "expected"),
        [
            (Level.NONE, Level.PREPARE, True),
            (Level.NONE, Level.IMMINENT, True),
            (Level.PREPARE, Level.IMMINENT, True),
            (Level.PREPARE, Level.PREPARE, False),
            (Level.IMMINENT, Level.PREPARE, False),
            (Level.IMMINENT, Level.NONE, False),
            (Level.NONE, Level.NONE, False),
        ],
        ids=[
            "none-to-prepare-is-escalation",
            "none-to-imminent-is-escalation",
            "prepare-to-imminent-is-escalation",
            "same-level-prepare-is-not-escalation",
            "imminent-to-prepare-is-not-escalation",
            "imminent-to-none-is-not-escalation",
            "same-level-none-is-not-escalation",
        ],
    )
    def test_is_escalation(self, previous: Level, candidate: Level, expected: bool) -> None:
        assert is_escalation(previous, candidate) is expected


class TestCoordinates:
    def test_valid_coordinates_construct(self) -> None:
        coords = Coordinates(latitude=-6.636005, longitude=-79.789860)
        assert coords.latitude == -6.636005
        assert coords.longitude == -79.789860

    @pytest.mark.parametrize(
        "latitude",
        [90.1, -90.1],
        ids=["latitude-above-90", "latitude-below-minus-90"],
    )
    def test_out_of_range_latitude_raises(self, latitude: float) -> None:
        with pytest.raises(ValueError, match="latitude"):
            Coordinates(latitude=latitude, longitude=0.0)

    @pytest.mark.parametrize(
        "longitude",
        [180.1, -180.1],
        ids=["longitude-above-180", "longitude-below-minus-180"],
    )
    def test_out_of_range_longitude_raises(self, longitude: float) -> None:
        with pytest.raises(ValueError, match="longitude"):
            Coordinates(latitude=0.0, longitude=longitude)


class TestTimeWindow:
    def test_valid_window_constructs(self) -> None:
        window = TimeWindow(start=_utc(0), end=_utc(1))
        assert window.start == _utc(0)
        assert window.end == _utc(1)

    def test_end_before_start_raises(self) -> None:
        with pytest.raises(ValueError, match="end"):
            TimeWindow(start=_utc(1), end=_utc(0))

    def test_equal_start_and_end_raises(self) -> None:
        with pytest.raises(ValueError, match="end"):
            TimeWindow(start=_utc(0), end=_utc(0))

    def test_naive_start_raises(self) -> None:
        with pytest.raises(ValueError, match="timezone"):
            TimeWindow(start=datetime(2026, 9, 3, 0), end=_utc(1))

    def test_naive_end_raises(self) -> None:
        with pytest.raises(ValueError, match="timezone"):
            TimeWindow(start=_utc(0), end=datetime(2026, 9, 3, 1))

    @pytest.mark.parametrize(
        ("start", "end"),
        [
            (datetime(2026, 9, 3, 0, tzinfo=LIMA_OFFSET), _utc(1)),
            (_utc(0), datetime(2026, 9, 3, 1, tzinfo=LIMA_OFFSET)),
        ],
        ids=["non-utc-start", "non-utc-end"],
    )
    def test_non_utc_offset_raises(self, start: datetime, end: datetime) -> None:
        """D7 requires UTC. A non-zero offset would make `key()` emit a value
        that sorts wrongly as the DynamoDB sort key in change 3."""
        with pytest.raises(ValueError, match="UTC"):
            TimeWindow(start=start, end=end)

    def test_key_sorts_lexicographically_in_chronological_order(self) -> None:
        """`key()` is the dedup sort key, so lexical order must equal
        chronological order for the key range query to be correct."""
        keys = [TimeWindow(start=_utc(hour), end=_utc(hour + 1)).key() for hour in range(0, 23, 3)]

        assert keys == sorted(keys)

    def test_overlapping_windows_overlap(self) -> None:
        a = TimeWindow(start=_utc(0), end=_utc(2))
        b = TimeWindow(start=_utc(1), end=_utc(3))
        assert a.overlaps(b)
        assert b.overlaps(a)

    def test_adjacent_windows_do_not_overlap(self) -> None:
        a = TimeWindow(start=_utc(0), end=_utc(1))
        b = TimeWindow(start=_utc(1), end=_utc(2))
        assert not a.overlaps(b)

    def test_disjoint_windows_do_not_overlap(self) -> None:
        a = TimeWindow(start=_utc(0), end=_utc(1))
        b = TimeWindow(start=_utc(5), end=_utc(6))
        assert not a.overlaps(b)

    def test_key_is_start_iso8601(self) -> None:
        window = TimeWindow(start=_utc(0), end=_utc(1))
        assert window.key() == _utc(0).isoformat()
