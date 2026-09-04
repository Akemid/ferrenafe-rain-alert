"""Domain entities (design.md section 3, D10 — probability aggregation is max
over the evaluated horizon, not the mean)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from rain_alert.domain.values import Coordinates, Level, TimeWindow, WarningLevel


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
    """One hourly forecast sample."""

    at: datetime
    precipitation_mm: float
    probability_pct: int


@dataclass(frozen=True, slots=True)
class Forecast:
    """A contiguous run of hourly forecast points for one location."""

    location: Coordinates
    points: tuple[HourlyPoint, ...]

    def __post_init__(self) -> None:
        for previous, current in zip(self.points, self.points[1:], strict=False):
            if (current.at - previous.at).total_seconds() != 3600:
                raise ValueError("Forecast.points must be contiguous hourly samples")

    def accumulated_mm(self, hours: int) -> float:
        """Total precipitation (mm) over the first `hours` points."""
        return sum(point.precipitation_mm for point in self.points[:hours])

    def max_probability_pct(self, hours: int) -> int:
        """Maximum hourly probability (%) over the first `hours` points (D10)."""
        window = self.points[:hours]
        return max((point.probability_pct for point in window), default=0)

    def peak_hour(self, hours: int) -> HourlyPoint:
        """The point with the highest precipitation within the first `hours` points."""
        window = self.points[:hours]
        return max(window, key=lambda point: point.precipitation_mm)

    def precipitation_window(self, hours: int) -> TimeWindow:
        """The `TimeWindow` spanning the first `hours` points."""
        window = self.points[:hours]
        return TimeWindow(start=window[0].at, end=window[-1].at)


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
