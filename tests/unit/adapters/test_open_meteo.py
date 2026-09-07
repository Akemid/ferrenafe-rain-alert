"""The Open-Meteo forecast adapter (weather-sources spec "Open-Meteo forecast
acquisition" and "Network failure"; design.md 8.1).

Every case runs offline through `httpx.MockTransport`. The payload shape
mirrored here was captured from the live endpoint on 2026-09-04:
`hourly.time` (naive ISO strings, because the request pins `timezone=UTC`),
`hourly.precipitation` in mm and `hourly.precipitation_probability` in %.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from rain_alert.adapters.open_meteo import API_URL, FORECAST_DAYS, OpenMeteoForecastProvider
from rain_alert.domain.sources import Available, Unavailable
from rain_alert.domain.values import Coordinates, SourceName, UnavailableReason
from tests.support.fixtures import OPEN_METEO_PAYLOAD

LOCATION = Coordinates(latitude=-6.636005, longitude=-79.789860)
DAY_START = datetime(2026, 9, 4, 0, tzinfo=UTC)


def _payload(
    *,
    hours: int = 72,
    start: datetime = DAY_START,
    precipitation: list[Any] | None = None,
    probability: list[Any] | None = None,
    times: list[str] | None = None,
) -> dict[str, Any]:
    """A live-shaped Open-Meteo response, overridable field by field."""
    stamps = (
        times if times is not None else [(start + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(hours)]
    )
    return {
        "latitude": LOCATION.latitude,
        "longitude": LOCATION.longitude,
        "utc_offset_seconds": 0,
        "timezone": "GMT",
        "hourly_units": {"time": "iso8601", "precipitation": "mm", "precipitation_probability": "%"},
        "hourly": {
            "time": stamps,
            "precipitation": precipitation if precipitation is not None else [0.0] * len(stamps),
            "precipitation_probability": probability if probability is not None else [0] * len(stamps),
        },
    }


def _provider(handler) -> OpenMeteoForecastProvider:
    return OpenMeteoForecastProvider(transport=httpx.MockTransport(handler))


def _json_provider(payload: dict[str, Any]) -> OpenMeteoForecastProvider:
    return _provider(lambda request: httpx.Response(200, json=payload))


def _raw_provider(payload: dict[str, Any]) -> OpenMeteoForecastProvider:
    """Serves the payload as the *wire* would, bare `NaN`/`Infinity` included.

    `httpx.Response(json=...)` encodes with `allow_nan=False` and refuses
    them, so it cannot express this case at all. Python's `json.dumps` emits
    the bare tokens by default and `json.loads` accepts them by default —
    which is precisely why they reach the parser from a well-formed HTTP 200.
    """
    return _provider(lambda request: httpx.Response(200, text=json.dumps(payload)))


class TestSuccessfulFetch:
    def test_a_valid_response_yields_48_available_hourly_points(self) -> None:
        """weather-sources: Successful fetch."""
        precipitation = [1.5] + [0.0] * 71
        probability = [80] + [10] * 71
        provider = _json_provider(_payload(precipitation=precipitation, probability=probability))

        result = provider.fetch_forecast(LOCATION, 48, DAY_START)

        assert isinstance(result, Available)
        forecast = result.data
        assert forecast.horizon_hours == 48
        assert forecast.accumulated_mm(48) == pytest.approx(1.5)
        assert forecast.max_probability_pct(48) == 80
        assert forecast.points[0].at == DAY_START
        assert forecast.location == LOCATION
        assert result.fetched_at == DAY_START

    def test_the_series_starts_at_the_current_hour_not_the_start_of_the_day(self) -> None:
        """The API returns whole days, so a mid-day run must drop the hours
        that have already passed — otherwise a 48-hour window would reach only
        34 hours into the future (design.md 8.1)."""
        # 12 mm fell before the run; 30 mm is forecast for the hour it starts in.
        precipitation = [1.0] * 12 + [30.0] + [0.0] * 59
        provider = _json_provider(_payload(precipitation=precipitation))

        result = provider.fetch_forecast(LOCATION, 48, datetime(2026, 9, 4, 12, 41, tzinfo=UTC))

        assert isinstance(result, Available)
        assert result.data.points[0].at == datetime(2026, 9, 4, 12, tzinfo=UTC)
        assert result.data.horizon_hours == 48
        assert result.data.accumulated_mm(48) == pytest.approx(30.0)

    def test_the_request_asks_for_four_days_of_hourly_utc_data(self) -> None:
        """Four days, because 48 *forward* hours are needed from any hour of
        the day and three leave only one spare hour at 23:00 UTC;
        `timezone=UTC` because window arithmetic must be unambiguous (D7)."""
        seen: list[httpx.URL] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url)
            return httpx.Response(200, json=_payload())

        _provider(handler).fetch_forecast(LOCATION, 48, DAY_START)

        assert len(seen) == 1
        assert str(seen[0]).startswith(API_URL)
        query = parse_qs(urlparse(str(seen[0])).query)
        assert query == {
            "latitude": ["-6.636005"],
            "longitude": ["-79.78986"],
            "hourly": ["precipitation,precipitation_probability"],
            "forecast_days": ["4"],
            "timezone": ["UTC"],
        }


class TestUnavailableResults:
    """design.md 8.1: never a partially-filled `Forecast`, never a raised
    exception — always an `Unavailable` carrying a machine-readable reason."""

    def test_a_connection_failure_is_reported_as_a_transport_error(self) -> None:
        """weather-sources: Network failure."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host", request=request)

        result = _provider(handler).fetch_forecast(LOCATION, 48, DAY_START)

        assert isinstance(result, Unavailable)
        assert result.reason is UnavailableReason.TRANSPORT_ERROR
        assert result.source is SourceName.OPEN_METEO
        assert result.observed_at == DAY_START
        assert "no route to host" in result.detail

    def test_a_read_timeout_is_reported_as_a_timeout(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("too slow", request=request)

        result = _provider(handler).fetch_forecast(LOCATION, 48, DAY_START)

        assert isinstance(result, Unavailable)
        assert result.reason is UnavailableReason.TIMEOUT

    def test_a_non_success_status_is_reported_as_a_bad_status(self) -> None:
        result = _provider(lambda request: httpx.Response(503, text="unavailable")).fetch_forecast(
            LOCATION, 48, DAY_START
        )

        assert isinstance(result, Unavailable)
        assert result.reason is UnavailableReason.BAD_STATUS
        assert "503" in result.detail

    def test_a_body_that_is_not_json_is_reported_as_a_malformed_payload(self) -> None:
        result = _provider(lambda request: httpx.Response(200, text="<html>maintenance</html>")).fetch_forecast(
            LOCATION, 48, DAY_START
        )

        assert isinstance(result, Unavailable)
        assert result.reason is UnavailableReason.MALFORMED_PAYLOAD

    @pytest.mark.parametrize(
        ("mutate", "case"),
        [
            (lambda p: p.pop("hourly"), "hourly block missing"),
            (lambda p: p["hourly"].pop("precipitation_probability"), "probability series missing"),
            (lambda p: p["hourly"].pop("time"), "timestamps missing"),
            (lambda p: p["hourly"].__setitem__("precipitation", [0.0] * 10), "series lengths disagree"),
            (lambda p: p["hourly"]["precipitation"].__setitem__(3, None), "null precipitation reading"),
            (lambda p: p["hourly"]["precipitation_probability"].__setitem__(3, "high"), "probability is not a number"),
            (lambda p: p["hourly"]["time"].__setitem__(3, "not-a-timestamp"), "timestamp is unparseable"),
            (lambda p: p["hourly"].__setitem__("time", "2026-09-04T00:00"), "series is not a list"),
        ],
        ids=lambda value: value if isinstance(value, str) else "",
    )
    def test_a_structurally_wrong_payload_is_reported_as_a_malformed_payload(self, mutate, case: str) -> None:
        payload = _payload()
        mutate(payload)

        result = _json_provider(payload).fetch_forecast(LOCATION, 48, DAY_START)

        assert isinstance(result, Unavailable), case
        assert result.reason is UnavailableReason.MALFORMED_PAYLOAD, case

    @pytest.mark.parametrize(
        ("mutate", "case"),
        [
            (lambda p: p["hourly"]["precipitation_probability"].__setitem__(3, float("nan")), "NaN probability"),
            (lambda p: p["hourly"]["precipitation_probability"].__setitem__(3, float("inf")), "Infinity probability"),
            (lambda p: p["hourly"]["precipitation"].__setitem__(3, float("nan")), "NaN precipitation"),
            (lambda p: p["hourly"]["precipitation"].__setitem__(3, float("inf")), "Infinity precipitation"),
            (lambda p: p["hourly"]["precipitation"].__setitem__(3, float("-inf")), "-Infinity precipitation"),
        ],
        ids=lambda value: value if isinstance(value, str) else "",
    )
    def test_a_non_finite_reading_is_reported_as_a_malformed_payload(self, mutate, case: str) -> None:
        """`json.loads` accepts bare `NaN` and `Infinity` by default, and
        `int(float("nan"))` raises `ValueError`, which is not a `FetchError`.
        Before the fix that killed the whole cycle with a traceback: no alert
        and no operator notice, which is worse than the tested unavailable
        path — and anomalous upstream values are most likely during exactly
        the extreme weather this tool exists for.

        `Infinity` millimetres was worse still: it parsed as a valid reading,
        accumulated to an infinite rainfall total and cleared
        `imminent_mm_24h` unconditionally."""
        payload = _payload()
        mutate(payload)

        result = _raw_provider(payload).fetch_forecast(LOCATION, 48, DAY_START)

        assert isinstance(result, Unavailable), case
        assert result.reason is UnavailableReason.MALFORMED_PAYLOAD, case

    def test_the_bare_non_finite_token_really_does_reach_the_parser(self) -> None:
        """Guards the test above from becoming vacuous. If a future httpx
        started rejecting these on decode, the case would still pass while no
        longer exercising the defect it was written for."""
        payload = _payload()
        payload["hourly"]["precipitation"][3] = float("inf")

        wire = json.dumps(payload)

        assert "Infinity" in wire
        assert json.loads(wire)["hourly"]["precipitation"][3] == float("inf")

    @pytest.mark.parametrize(
        ("mutate", "case"),
        [
            (lambda p: p["hourly"]["precipitation_probability"].__setitem__(3, 101), "probability above 100"),
            (lambda p: p["hourly"]["precipitation_probability"].__setitem__(3, -1), "probability below zero"),
            (lambda p: p["hourly"]["precipitation"].__setitem__(3, -0.5), "negative precipitation"),
        ],
        ids=lambda value: value if isinstance(value, str) else "",
    )
    def test_an_out_of_range_reading_is_reported_as_a_malformed_payload(self, mutate, case: str) -> None:
        """A percentage is 0 to 100 and rainfall is not negative. Both are
        claimed by `HourlyPoint`, and an upstream value that violates them
        would otherwise flow into a threshold comparison."""
        payload = _payload()
        mutate(payload)

        result = _json_provider(payload).fetch_forecast(LOCATION, 48, DAY_START)

        assert isinstance(result, Unavailable), case
        assert result.reason is UnavailableReason.MALFORMED_PAYLOAD, case

    def test_the_documented_range_boundaries_are_still_accepted(self) -> None:
        """Triangulation: the range check must not reject real readings. 0%
        and 100% both occur in live payloads."""
        probability = [0] + [100] * 71

        result = _json_provider(_payload(probability=probability)).fetch_forecast(LOCATION, 48, DAY_START)

        assert isinstance(result, Available)
        assert result.data.max_probability_pct(48) == 100

    def test_an_unexpected_exception_degrades_the_cycle_instead_of_aborting_it(self) -> None:
        """The sibling scraper already carries a catch-all with exactly this
        comment: a parser bug must degrade the cycle, not abort it, because
        `RunAlertCycle` has a fully tested degraded path and no exception
        handler at all. This adapter did not follow its own codebase's rule."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise RuntimeError("an httpx internal that is not an HTTPError")

        result = _provider(handler).fetch_forecast(LOCATION, 48, DAY_START)

        assert isinstance(result, Unavailable)
        assert result.source is SourceName.OPEN_METEO
        assert result.reason in set(UnavailableReason)
        assert "RuntimeError" in result.detail

    def test_an_unexpected_parser_bug_is_also_reported_rather_than_raised(self, monkeypatch) -> None:
        def boom(*args, **kwargs):
            raise KeyError("a field the parser assumed was there")

        monkeypatch.setattr("rain_alert.adapters.open_meteo.parse_forecast_payload", boom)

        result = _json_provider(_payload()).fetch_forecast(LOCATION, 48, DAY_START)

        assert isinstance(result, Unavailable)
        assert "KeyError" in result.detail

    def test_a_gap_in_the_hourly_series_is_a_parse_failure_not_silent_interpolation(self) -> None:
        """`Forecast` requires contiguous hourly points, so a missing hour must
        fail loudly rather than be interpolated away (design.md 8.1)."""
        times = [(DAY_START + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(72) if h != 5]

        result = _json_provider(_payload(times=times)).fetch_forecast(LOCATION, 48, DAY_START)

        assert isinstance(result, Unavailable)
        assert result.reason is UnavailableReason.MALFORMED_PAYLOAD

    def test_too_few_forward_hours_is_reported_as_an_insufficient_horizon(self) -> None:
        # Only 24 of the 72 returned hours are still in the future.
        result = _json_provider(_payload()).fetch_forecast(LOCATION, 48, datetime(2026, 9, 6, 0, tzinfo=UTC))

        assert isinstance(result, Unavailable)
        assert result.reason is UnavailableReason.INSUFFICIENT_HORIZON
        assert "24" in result.detail

    def test_a_series_entirely_in_the_past_is_reported_as_an_insufficient_horizon(self) -> None:
        result = _json_provider(_payload()).fetch_forecast(LOCATION, 48, datetime(2026, 9, 30, 0, tzinfo=UTC))

        assert isinstance(result, Unavailable)
        assert result.reason is UnavailableReason.INSUFFICIENT_HORIZON

    def test_a_shorter_horizon_request_is_satisfied_from_the_same_payload(self) -> None:
        """Triangulation on the horizon check: the same payload that is too
        short for 48 hours does answer a 24-hour question."""
        result = _json_provider(_payload()).fetch_forecast(LOCATION, 24, datetime(2026, 9, 6, 0, tzinfo=UTC))

        assert isinstance(result, Available)
        assert result.data.horizon_hours == 24


def test_the_committed_payload_fixture_still_parses() -> None:
    """The fixture the CLI's `--offline-fixtures` mode reads is a real recorded
    response; if its shape drifts from the parser, the offline CLI path breaks
    silently."""
    payload = json.loads(OPEN_METEO_PAYLOAD.read_text(encoding="utf-8"))
    first_hour = datetime.fromisoformat(payload["hourly"]["time"][0]).replace(tzinfo=UTC)

    result = _json_provider(payload).fetch_forecast(LOCATION, 48, first_hour)

    assert isinstance(result, Available)
    assert result.data.horizon_hours == 48


@pytest.mark.parametrize("hour", list(range(24)), ids=[f"{hour:02d}Z" for hour in range(24)])
def test_the_requested_days_leave_a_full_48_hour_horizon_at_every_hour(hour: int) -> None:
    """W4: `forecast_days=3` has a cliff at the end of the day.

    The API returns whole days from today, so a request at 23:00 UTC leaves
    3*24 - 23 = 49 forward points for a 48-hour need — one spare. Any clock
    skew, a late model publication, or a run that starts a minute before
    midnight and reaches the API a minute after, drops the cycle to
    `INSUFFICIENT_HORIZON` and degrades the system to SENAMHI-only. A fourth
    day costs nothing and removes the cliff, so the margin is asserted at
    every hour rather than at the hour the suite happens to run.
    """
    forward_hours = FORECAST_DAYS * 24 - hour

    assert forward_hours >= 48 + 24, (
        f"at {hour:02d}:00 UTC only {forward_hours} forward hours are requested for a 48-hour need; "
        "a whole spare day is the margin that removes the end-of-day cliff"
    )
