"""The Open-Meteo forecast adapter (weather-sources spec; design.md 8.1).

Endpoint, parameter names and response shape were verified against the live
API on 2026-09-04:

    GET https://api.open-meteo.com/v1/forecast
        ?latitude=..&longitude=..
        &hourly=precipitation,precipitation_probability
        &forecast_days=4&timezone=UTC

`forecast_days` is deliberately not `2`: the API returns *whole days from
today*, so a request made at 12:41 UTC with `forecast_days=2` carries only 34
hours of data still in the future, and the cycle would degrade to
SENAMHI-only for most of every day.

It is `4` rather than `3` because three days have a **cliff at the end of the
day**: at 23:00 UTC they leave 3*24-23 = 49 forward points for a 48-hour
need, a margin of one hour. Any clock skew, a late model publication, or a
run that starts a minute before midnight and reaches the API a minute after
turns that into `INSUFFICIENT_HORIZON`. A fourth day leaves a whole spare day
at every hour, costs nothing — one page, once — and removes the cliff. The
adapter still slices `[current hour, +hours)`, so the extra day is never
evaluated. `timezone=UTC` keeps window arithmetic unambiguous; local time is
a display concern only (D7).

The parse is a pure function so it is testable without HTTP and reusable by
the CLI's `--offline-fixtures` mode. It never returns a partially-filled
`Forecast`: a gap in the series is a parse failure, not silent interpolation.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from typing import Any

import httpx

from rain_alert.adapters.http import DEFAULT_TIMEOUT, FetchError, fetch_text
from rain_alert.domain.entities import Forecast, HourlyPoint
from rain_alert.domain.sources import Available, SourceResult, Unavailable
from rain_alert.domain.values import Coordinates, SourceName, UnavailableReason

API_URL = "https://api.open-meteo.com/v1/forecast"
HOURLY_VARIABLES = "precipitation,precipitation_probability"
FORECAST_DAYS = 4


def build_query(location: Coordinates) -> dict[str, str | int | float]:
    """The verified query string for one location."""
    return {
        "latitude": location.latitude,
        "longitude": location.longitude,
        "hourly": HOURLY_VARIABLES,
        "forecast_days": FORECAST_DAYS,
        "timezone": "UTC",
    }


def _series(hourly: Any, name: str) -> list[Any]:
    values = hourly.get(name)
    if not isinstance(values, list):
        raise FetchError(UnavailableReason.MALFORMED_PAYLOAD, f"hourly.{name} is missing or not a list")
    return values


def _millimetres(value: Any, index: int) -> float:
    """One `hourly.precipitation` entry as finite, non-negative millimetres.

    `json.loads` accepts bare `NaN` and `Infinity`, so both reach here from a
    well-formed HTTP 200. `Infinity` used to parse as a valid reading and
    accumulate to an infinite rainfall total, which clears `imminent_mm_24h`
    unconditionally. Rejecting at parse time keeps the anomaly a declared
    outage rather than an unconditional alert.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise FetchError(
            UnavailableReason.MALFORMED_PAYLOAD, f"hourly.precipitation[{index}] is not a number: {value!r}"
        )
    if not math.isfinite(value):
        raise FetchError(UnavailableReason.MALFORMED_PAYLOAD, f"hourly.precipitation[{index}] is not finite: {value!r}")
    if value < 0:
        raise FetchError(UnavailableReason.MALFORMED_PAYLOAD, f"hourly.precipitation[{index}] is negative: {value!r}")
    return float(value)


def _percent(value: Any, index: int) -> int:
    """One `hourly.precipitation_probability` entry as a 0-100 percentage.

    The finiteness check has to come before `int(value)`: `int(float("nan"))`
    raises `ValueError`, which is not a `FetchError`, so it escaped the
    adapter's only `except` clause and killed the whole cycle with a
    traceback — no alert and no operator notice, which is strictly worse than
    the tested unavailable path.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise FetchError(
            UnavailableReason.MALFORMED_PAYLOAD,
            f"hourly.precipitation_probability[{index}] is not a number: {value!r}",
        )
    if not math.isfinite(value):
        raise FetchError(
            UnavailableReason.MALFORMED_PAYLOAD,
            f"hourly.precipitation_probability[{index}] is not finite: {value!r}",
        )
    percent = int(value)
    if not 0 <= percent <= 100:
        raise FetchError(
            UnavailableReason.MALFORMED_PAYLOAD,
            f"hourly.precipitation_probability[{index}] is outside 0..100: {value!r}",
        )
    return percent


def _timestamp(value: Any, index: int) -> datetime:
    """One `hourly.time` entry as a UTC datetime.

    The request pins `timezone=UTC`, so the API returns naive strings like
    `2026-09-04T00:00`; the zone is attached here rather than assumed later.
    """
    if not isinstance(value, str):
        raise FetchError(UnavailableReason.MALFORMED_PAYLOAD, f"hourly.time[{index}] is not a string: {value!r}")
    try:
        moment = datetime.fromisoformat(value)
    except ValueError as exc:
        raise FetchError(
            UnavailableReason.MALFORMED_PAYLOAD, f"hourly.time[{index}] is unparseable: {value!r}"
        ) from exc
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def parse_forecast_payload(payload: Any, location: Coordinates, hours: int, now: datetime) -> Forecast:
    """Turn a decoded Open-Meteo response into a `Forecast` of `hours` points.

    Args:
        payload: The decoded JSON body.
        location: The coordinates the request was made for.
        hours: How many forward hours the caller needs.
        now: The cycle clock; the series is sliced from the hour containing it.

    Returns:
        A `Forecast` of exactly `hours` contiguous points starting at the
        current hour.

    Raises:
        FetchError: `MALFORMED_PAYLOAD` when the body is not the documented
            shape, or `INSUFFICIENT_HORIZON` when too few points remain ahead
            of `now`.
    """
    if not isinstance(payload, dict):
        raise FetchError(UnavailableReason.MALFORMED_PAYLOAD, f"body is {type(payload).__name__}, expected an object")
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict):
        raise FetchError(UnavailableReason.MALFORMED_PAYLOAD, "hourly block is missing or not an object")

    stamps = _series(hourly, "time")
    millimetres = _series(hourly, "precipitation")
    percentages = _series(hourly, "precipitation_probability")
    if not len(stamps) == len(millimetres) == len(percentages):
        raise FetchError(
            UnavailableReason.MALFORMED_PAYLOAD,
            f"hourly series lengths disagree: time={len(stamps)}, "
            f"precipitation={len(millimetres)}, precipitation_probability={len(percentages)}",
        )

    current_hour = now.replace(minute=0, second=0, microsecond=0)
    points: list[HourlyPoint] = []
    for index, stamp in enumerate(stamps):
        at = _timestamp(stamp, index)
        if at < current_hour:
            continue  # the API returns whole days; hours already past are dropped
        try:
            point = HourlyPoint(
                at=at,
                precipitation_mm=_millimetres(millimetres[index], index),
                probability_pct=_percent(percentages[index], index),
            )
        except ValueError as exc:
            # `HourlyPoint` defends the same ranges the helpers above check.
            # It is the second line, not the first: a future caller that skips
            # the helpers must not be able to build an unusable point, and its
            # `ValueError` must still leave here as a `FetchError`.
            raise FetchError(
                UnavailableReason.MALFORMED_PAYLOAD, f"hourly sample {index} is not a usable reading: {exc}"
            ) from exc
        points.append(point)
    if len(points) < hours:
        raise FetchError(
            UnavailableReason.INSUFFICIENT_HORIZON,
            f"{len(points)} hourly points at or after {current_hour.isoformat()}, need {hours}",
        )

    try:
        return Forecast(location=location, points=tuple(points[:hours]))
    except ValueError as exc:
        # `Forecast` enforces contiguity, so a missing hour lands here rather
        # than being interpolated away.
        raise FetchError(UnavailableReason.MALFORMED_PAYLOAD, f"series is not a usable forecast: {exc}") from exc


class OpenMeteoForecastProvider:
    """`ForecastProvider` over the live Open-Meteo API.

    The transport is injectable so this exact class is exercised offline
    through `httpx.MockTransport` (D4).
    """

    def __init__(
        self,
        transport: httpx.BaseTransport | None = None,
        url: str = API_URL,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
    ) -> None:
        self._transport = transport
        self._url = url
        self._timeout = timeout

    def fetch_forecast(self, location: Coordinates, hours: int, now: datetime) -> SourceResult[Forecast]:
        """Fetch and parse the forecast, reporting failure as `Unavailable`.

        No exception escapes: a transport failure, a bad status, a malformed
        body and too short a horizon all become `Unavailable` with a
        machine-readable reason (D12 — no retries; the cycle repeats).

        "No exception escapes" now includes the ones nobody predicted. The
        `FetchError` clause alone was a claim, not a guarantee: `_percent`
        called `int(value)` on a value `json.loads` was happy to produce, and
        the resulting `ValueError` aborted the whole run with a traceback,
        because `RunAlertCycle` has no exception handler either. The sibling
        `SenamhiWarningScraper` already carried this catch-all with the same
        reasoning; this adapter was not following its own codebase's rule.

        The catch-all reports `TRANSPORT_ERROR` and names the exception type,
        exactly as the scraper does. The reason code does not change any
        routing — only `Available` versus `Unavailable` does — so the value of
        matching the sibling is that one unfamiliar detail string reads the
        same wherever an operator meets it.
        """
        try:
            body = self._fetch(location)
            return Available(data=parse_forecast_payload(body, location, hours, now), fetched_at=now)
        except FetchError as exc:
            return Unavailable(source=SourceName.OPEN_METEO, reason=exc.reason, detail=exc.detail, observed_at=now)
        except Exception as exc:  # noqa: BLE001 - a parser bug must degrade the cycle, not abort it
            return Unavailable(
                source=SourceName.OPEN_METEO,
                reason=UnavailableReason.TRANSPORT_ERROR,
                detail=f"unexpected {type(exc).__name__}: {exc}",
                observed_at=now,
            )

    def _fetch(self, location: Coordinates) -> Any:
        with httpx.Client(transport=self._transport, timeout=self._timeout) as client:
            text = fetch_text(client, self._url, params=build_query(location))
        try:
            return json.loads(text)
        except ValueError as exc:
            raise FetchError(UnavailableReason.MALFORMED_PAYLOAD, f"body is not JSON: {exc}") from exc
