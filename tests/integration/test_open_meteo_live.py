"""Live Open-Meteo check — **opt-in** (design.md 10).

Deselected from the default suite by `addopts = "-m 'not integration'"`, so
the offline suite stays offline and fast. Run deliberately:

    uv run pytest -m integration

This is a canary, not a unit test. It exists to catch the API changing under
us — a renamed parameter, a dropped hourly variable, a changed timezone
convention — which no fixture can detect by construction.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rain_alert.adapters.open_meteo import FORECAST_DAYS, OpenMeteoForecastProvider
from rain_alert.domain.sources import Available
from rain_alert.domain.values import Coordinates

LOCATION = Coordinates(latitude=-6.64, longitude=-79.79)
REQUIRED_FORWARD_HOURS = 48

pytestmark = pytest.mark.integration


def test_the_live_api_returns_a_usable_48_hour_forecast() -> None:
    """weather-sources: Successful fetch, against the real endpoint."""
    now = datetime.now(UTC)

    result = OpenMeteoForecastProvider().fetch_forecast(LOCATION, 48, now)

    assert isinstance(result, Available), getattr(result, "detail", result)
    forecast = result.data
    assert forecast.horizon_hours == 48
    assert forecast.points[0].at == now.replace(minute=0, second=0, microsecond=0)
    assert forecast.accumulated_mm(48) >= 0.0
    assert 0 <= forecast.max_probability_pct(48) <= 100


def test_the_requested_days_cover_48_forward_hours_at_this_time_of_day() -> None:
    """The reason `forecast_days` is not `2`: the API returns whole days from
    today, so two days stop short of 48 forward hours after 00:00 UTC. If
    this ever fails, the slice arithmetic in the adapter is wrong for the
    current hour, which a fixture-based test cannot notice."""
    now = datetime.now(UTC)
    hours_already_past = now.hour

    assert FORECAST_DAYS * 24 - hours_already_past >= REQUIRED_FORWARD_HOURS
