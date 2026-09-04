"""RiskEvaluator — turns per-source availability and data into a
RiskAssessment (risk-evaluation spec; design.md section 5). Zero
AWS/HTTP/HTML/LLM imports.

Deliberately not built as a rule registry, predicate table or threshold DSL:
two levels and five branches do not clear the rule of three; explicit
ordered branches are the readable, debuggable form. Each branch's condition
lives in a small named helper returning a verdict or `None`, so the reasons
tuple is produced by the same code that made the decision — the audit trail
cannot drift from the verdict.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from rain_alert.domain.config import RiskThresholds
from rain_alert.domain.entities import Forecast, RiskAssessment, Warning
from rain_alert.domain.sources import Available, SourceResult, status_of
from rain_alert.domain.values import Level, TimeWindow

_IMMINENT_HOURS = 24
_PREPARE_HOURS = 48

_Verdict = tuple[Level, TimeWindow, tuple[str, ...]]


def _union_window(windows: Sequence[TimeWindow]) -> TimeWindow:
    """The smallest window covering every window in `windows`."""
    return TimeWindow(start=min(w.start for w in windows), end=max(w.end for w in windows))


def _warning_reason(warning: Warning) -> str:
    return f"official SENAMHI warning: {warning.level.value} — {warning.title}"


def _forecast_reason(accumulated: float, hours: int, probability: int, *, degraded_threshold: int | None = None) -> str:
    """One reason string shared by all three branches that cite an
    accumulated-mm/max-probability forecast reading, so the wording (and any
    future change to it) cannot drift between branches."""
    reason = f"{accumulated:.1f} mm / {hours} h at {probability}% max probability"
    if degraded_threshold is not None:
        reason += f" (degraded threshold {degraded_threshold}%)"
    return reason


@dataclass(frozen=True, slots=True)
class RiskEvaluator:
    """Constructor-injected with `RiskThresholds` loaded from `ConfigRepository`
    by the use case. No threshold, coordinate or probability literal appears
    anywhere else in this module."""

    thresholds: RiskThresholds

    def evaluate(
        self,
        warnings: SourceResult[tuple[Warning, ...]],
        forecast: SourceResult[Forecast],
        now: datetime,
    ) -> RiskAssessment:
        """Evaluate, most severe branch first (design.md section 5)."""
        senamhi_status = status_of(warnings)
        open_meteo_status = status_of(forecast)
        degraded = senamhi_status == "unavailable" or open_meteo_status == "unavailable"

        for branch in (
            self._imminent_from_official,
            self._imminent_from_forecast,
            self._prepare_combined,
            self._prepare_degraded,
        ):
            verdict = branch(warnings, forecast)
            if verdict is not None:
                level, window, reasons = verdict
                return RiskAssessment(level, window, reasons, senamhi_status, open_meteo_status, degraded)

        window = TimeWindow(start=now, end=now + timedelta(hours=_PREPARE_HOURS))
        return RiskAssessment(Level.NONE, window, (), senamhi_status, open_meteo_status, degraded)

    def _imminent_from_official(
        self,
        warnings: SourceResult[tuple[Warning, ...]],
        _forecast: SourceResult[Forecast],
    ) -> _Verdict | None:
        if not isinstance(warnings, Available):
            return None
        matches = [w for w in warnings.data if w.level in self.thresholds.imminent_warning_levels]
        if not matches:
            return None
        window = _union_window([w.window for w in matches])
        reasons = tuple(_warning_reason(w) for w in matches)
        return Level.IMMINENT, window, reasons

    def _imminent_from_forecast(
        self,
        _warnings: SourceResult[tuple[Warning, ...]],
        forecast: SourceResult[Forecast],
    ) -> _Verdict | None:
        if not isinstance(forecast, Available):
            return None
        data = forecast.data
        accumulated = data.accumulated_mm(_IMMINENT_HOURS)
        probability = data.max_probability_pct(_IMMINENT_HOURS)
        if accumulated < self.thresholds.imminent_mm_24h or probability < self.thresholds.imminent_probability_pct:
            return None
        window = data.precipitation_window(_IMMINENT_HOURS)
        reason = _forecast_reason(accumulated, _IMMINENT_HOURS, probability)
        return Level.IMMINENT, window, (reason,)

    def _prepare_combined(
        self,
        warnings: SourceResult[tuple[Warning, ...]],
        forecast: SourceResult[Forecast],
    ) -> _Verdict | None:
        if not isinstance(warnings, Available) or not isinstance(forecast, Available):
            return None
        matches = [w for w in warnings.data if w.level in self.thresholds.prepare_warning_levels]
        if not matches:
            return None
        data = forecast.data
        accumulated = data.accumulated_mm(_PREPARE_HOURS)
        probability = data.max_probability_pct(_PREPARE_HOURS)
        if accumulated < self.thresholds.prepare_mm_48h or probability < self.thresholds.prepare_probability_pct:
            return None
        window = _union_window([*(w.window for w in matches), data.precipitation_window(_PREPARE_HOURS)])
        reasons = (
            *(_warning_reason(w) for w in matches),
            _forecast_reason(accumulated, _PREPARE_HOURS, probability),
        )
        return Level.PREPARE, window, reasons

    def _prepare_degraded(
        self,
        warnings: SourceResult[tuple[Warning, ...]],
        forecast: SourceResult[Forecast],
    ) -> _Verdict | None:
        if isinstance(warnings, Available) or not isinstance(forecast, Available):
            return None
        data = forecast.data
        accumulated = data.accumulated_mm(_PREPARE_HOURS)
        probability = data.max_probability_pct(_PREPARE_HOURS)
        if (
            accumulated < self.thresholds.prepare_mm_48h
            or probability < self.thresholds.prepare_probability_pct_degraded
        ):
            return None
        window = data.precipitation_window(_PREPARE_HOURS)
        reasons = (
            "official SENAMHI source unavailable; forecast-only evaluation",
            _forecast_reason(
                accumulated,
                _PREPARE_HOURS,
                probability,
                degraded_threshold=self.thresholds.prepare_probability_pct_degraded,
            ),
        )
        return Level.PREPARE, window, reasons
