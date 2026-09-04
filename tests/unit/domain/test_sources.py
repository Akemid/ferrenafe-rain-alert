"""SourceResult tagged union (design.md section 3, D2)."""

from datetime import UTC, datetime

from rain_alert.domain.sources import Available, Unavailable, status_of
from rain_alert.domain.values import SourceName, UnavailableReason


def test_available_carries_data_and_fetched_at() -> None:
    fetched_at = datetime(2026, 9, 3, 14, tzinfo=UTC)
    result = Available(data=(1, 2, 3), fetched_at=fetched_at)
    assert result.data == (1, 2, 3)
    assert result.fetched_at == fetched_at


def test_unavailable_has_no_data_attribute() -> None:
    result = Unavailable(
        source=SourceName.SENAMHI,
        reason=UnavailableReason.TIMEOUT,
        detail="connect timed out",
        observed_at=datetime(2026, 9, 3, 14, tzinfo=UTC),
    )
    assert not hasattr(result, "data")


def test_status_of_available_is_available() -> None:
    result = Available(data=None, fetched_at=datetime(2026, 9, 3, 14, tzinfo=UTC))
    assert status_of(result) == "available"


def test_status_of_unavailable_is_unavailable() -> None:
    result = Unavailable(
        source=SourceName.OPEN_METEO,
        reason=UnavailableReason.TRANSPORT_ERROR,
        detail="connection refused",
        observed_at=datetime(2026, 9, 3, 14, tzinfo=UTC),
    )
    assert status_of(result) == "unavailable"
