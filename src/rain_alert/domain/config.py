"""Configuration types (design.md section 5). `RiskEvaluator` is
constructor-injected with `RiskThresholds`; no threshold, coordinate or
probability literal appears anywhere in `domain/risk.py`."""

from __future__ import annotations

from dataclasses import dataclass

from rain_alert.domain.values import Coordinates, WarningLevel


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
    timezone: str
    region: str
    thresholds: RiskThresholds
    checklist: tuple[str, ...]
    active_channel: str
    forecast_hours: int  # 48
    dedup_lookback_hours: int  # 72
    coordinates_are_placeholder: bool  # True until design spec 12 is closed
