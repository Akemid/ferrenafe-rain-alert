"""Domain entities (design.md section 3, D10 — probability aggregation is max
over the evaluated horizon, not the mean)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from rain_alert.domain.values import Coordinates, Level, TimeWindow, WarningLevel

_SAMPLE_DURATION = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class Warning:
    """A normalized SENAMHI warning entry."""

    source_id: str
    title: str
    level: WarningLevel
    region: str
    window: TimeWindow
    emitted_at: datetime


@dataclass(frozen=True, slots=True)
class HourlyPoint:
    """One hourly forecast sample, describing the hour that starts at `at`."""

    at: datetime
    precipitation_mm: float
    probability_pct: int


@dataclass(frozen=True, slots=True)
class Forecast:
    """A non-empty, contiguous run of hourly forecast points for one location.

    Every accessor takes the number of hours to read and refuses a horizon
    this forecast cannot answer: a 24-point forecast must not be used to
    answer a 48-hour question, because the answer would be reported under an
    untruthful horizon. Callers that can tolerate a shorter horizon clamp
    their request against `horizon_hours` first (see `domain/risk.py`).
    """

    location: Coordinates
    points: tuple[HourlyPoint, ...]

    def __post_init__(self) -> None:
        if not self.points:
            raise ValueError("Forecast.points must not be empty")
        for previous, current in zip(self.points, self.points[1:], strict=False):
            if current.at - previous.at != _SAMPLE_DURATION:
                raise ValueError("Forecast.points must be contiguous hourly samples")

    @property
    def horizon_hours(self) -> int:
        """How many hours of data this forecast actually carries."""
        return len(self.points)

    def accumulated_mm(self, hours: int) -> float:
        """Total precipitation (mm) over the first `hours` points."""
        return sum(point.precipitation_mm for point in self._slice(hours))

    def max_probability_pct(self, hours: int) -> int:
        """Maximum hourly probability (%) over the first `hours` points (D10)."""
        return max(point.probability_pct for point in self._slice(hours))

    def peak_hour(self, hours: int) -> HourlyPoint:
        """The point with the highest precipitation within the first `hours` points."""
        return max(self._slice(hours), key=lambda point: point.precipitation_mm)

    def precipitation_window(self, hours: int) -> TimeWindow:
        """The `TimeWindow` covering the period the first `hours` points describe.

        The window ends one hour after the last sample's timestamp, because an
        hourly sample stamped 14:00 describes 14:00-15:00. Ending at the last
        sample's own timestamp would under-report coverage by one hour and make
        `AlertMessage.valid_until` expire before the data runs out.
        """
        window = self._slice(hours)
        return TimeWindow(start=window[0].at, end=window[-1].at + _SAMPLE_DURATION)

    def _slice(self, hours: int) -> tuple[HourlyPoint, ...]:
        if not 1 <= hours <= self.horizon_hours:
            raise ValueError(f"hours must be in 1..{self.horizon_hours} for this forecast, got {hours}")
        return self.points[:hours]


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    """The evaluator's verdict for one cycle."""

    level: Level
    window: TimeWindow
    reasons: tuple[str, ...]
    senamhi_status: str
    open_meteo_status: str
    degraded: bool


@dataclass(frozen=True, slots=True)
class Contact:
    """A community-alert recipient."""

    contact_id: str
    channel: str
    handle: str
    consent_at: datetime | None
