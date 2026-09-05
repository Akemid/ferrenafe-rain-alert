"""Fixture-backed source adapters for `--offline-fixtures` (design.md 9).

These swap the **fetch leaf only**. The real SENAMHI scraper and the real
Open-Meteo payload parser still do the work, so the offline path cannot
quietly diverge from the live one — which is the whole reason it is
trustworthy for calibration. A separate "offline parser" would be a second
implementation to keep in step, and the first bug would be invisible.

A missing or unreadable fixture is reported as `FetchError`, exactly like a
transport failure, so the cycle degrades the same way it would live.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from rain_alert.adapters.http import FetchError
from rain_alert.adapters.open_meteo import parse_forecast_payload
from rain_alert.domain.entities import Forecast
from rain_alert.domain.sources import Available, SourceResult, Unavailable
from rain_alert.domain.values import Coordinates, SourceName, UnavailableReason


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FetchError(UnavailableReason.TRANSPORT_ERROR, f"cannot read fixture {path}: {exc}") from exc


class FileHtmlFetcher:
    """`HtmlFetcher` that serves a pinned HTML file and ignores the URL."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def fetch(self, url: str) -> str:
        return _read(self._path)


class OfflineOpenMeteoProvider:
    """`ForecastProvider` that parses a recorded payload from disk."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def fetch_forecast(self, location: Coordinates, hours: int, now: datetime) -> SourceResult[Forecast]:
        """Parse the recorded payload, reporting failure as `Unavailable`.

        The same "no exception escapes" rule the live provider owes. A
        recorded payload is a captured live response, so it carries the same
        anomalies — including the bare `NaN` that `json.loads` accepts and
        that used to abort the run — and `RunAlertCycle` has no handler.

        `json.JSONDecodeError` is caught before the catch-all because it is a
        `ValueError` subclass and deserves its own reason and its own detail
        naming the file.
        """
        try:
            payload: Any = json.loads(_read(self._path))
            return Available(data=parse_forecast_payload(payload, location, hours, now), fetched_at=now)
        except json.JSONDecodeError as exc:
            return Unavailable(
                source=SourceName.OPEN_METEO,
                reason=UnavailableReason.MALFORMED_PAYLOAD,
                detail=f"{self._path} is not JSON: {exc}",
                observed_at=now,
            )
        except FetchError as exc:
            return Unavailable(source=SourceName.OPEN_METEO, reason=exc.reason, detail=exc.detail, observed_at=now)
        except Exception as exc:  # noqa: BLE001 - a parser bug must degrade the cycle, not abort it
            return Unavailable(
                source=SourceName.OPEN_METEO,
                reason=UnavailableReason.TRANSPORT_ERROR,
                detail=f"unexpected {type(exc).__name__}: {exc}",
                observed_at=now,
            )
