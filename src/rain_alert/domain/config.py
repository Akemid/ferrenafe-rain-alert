"""Configuration types (design.md section 5). `RiskEvaluator` is
constructor-injected with `RiskThresholds`; no threshold, coordinate or
probability literal appears anywhere in `domain/risk.py`."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from rain_alert.domain.values import Coordinates, WarningLevel


class CoordinatesSource(StrEnum):
    """Where the monitored coordinates came from.

    An enum rather than a boolean because the useful question is not "are these
    provisional" but "how were they obtained", and because the answer has more
    than two states already: a point can come from a public reference, from an
    operator who supplied it, or from a reading taken on the ground. Each
    deserves different trust, and collapsing them loses the distinction exactly
    when an operator needs it.
    """

    #: Taken from published references. Good enough to calibrate against.
    PUBLIC_REFERENCE = "public_reference"
    #: Supplied by an operator through configuration. They are asserting it.
    OPERATOR_SUPPLIED = "operator_supplied"
    #: Confirmed by a reading taken at the location. Nothing sets this yet.
    SURVEYED = "surveyed"


@dataclass(frozen=True, slots=True)
class RiskThresholds:
    """Numeric and categorical thresholds that drive `RiskEvaluator`."""

    prepare_mm_48h: float  # 9.5 — Reque p95 boundary
    prepare_probability_pct: int  # 60
    prepare_probability_pct_degraded: int  # 70 — SENAMHI unavailable
    prepare_warning_levels: frozenset[WarningLevel]  # {yellow, orange, red}
    imminent_mm_24h: float  # 20.0
    imminent_probability_pct: int  # 70
    imminent_warning_levels: frozenset[WarningLevel]  # {orange, red}


@dataclass(frozen=True, slots=True)
class AlertConfig:
    """The full configuration for one city's alert cycle."""

    city: str
    city_slug: str
    coordinates: Coordinates
    #: How the monitored point was obtained. Not a confidence score and not a
    #: boolean: an operator reading output during a real event has to be able to
    #: tell a point taken from a public reference from one confirmed on the
    #: ground, and the cloud deployment has to carry that distinction forward
    #: rather than rediscover it. `Coordinates` cannot hold this, because the
    #: same pair means different things depending on where it came from.
    coordinates_source: CoordinatesSource
    timezone: str
    region: str
    thresholds: RiskThresholds
    checklist: tuple[str, ...]
    active_channel: str
    forecast_hours: int  # 48
    dedup_lookback_hours: int  # 72
