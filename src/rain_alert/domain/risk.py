"""RiskEvaluator — turns per-source availability and data into a
RiskAssessment (risk-evaluation spec; design.md section 5). Zero
AWS/HTTP/HTML/LLM imports.

Deliberately not built as a rule registry, predicate table or threshold DSL:
two levels and six branches do not clear the rule of three; explicit
ordered branches are the readable, debuggable form. Each branch's condition
lives in a small named helper returning a verdict or `None`, so the reasons
tuple is produced by the same code that made the decision — the audit trail
cannot drift from the verdict.

Branch numbers in this module are design.md section 5's, and there is exactly
one numbering: 1 imminent/official, 2 imminent/forecast, 3 prepare/combined,
4 prepare/forecast-only, 5 prepare/degraded, 6 none. Six branches means five
named helpers plus the fallthrough at the end of `evaluate`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from rain_alert.domain.config import RiskThresholds
from rain_alert.domain.entities import Forecast, RiskAssessment, Warning
from rain_alert.domain.reasons import (
    ForecastThresholdReason,
    NoQualifyingWarningReason,
    Reason,
    SenamhiUnavailableReason,
    WarningReason,
)
from rain_alert.domain.sources import Available, SourceResult, status_of
from rain_alert.domain.values import Level, TimeWindow

IMMINENT_HORIZON_HOURS = 24
PREPARE_HORIZON_HOURS = 48

_Verdict = tuple[Level, TimeWindow, tuple[Reason, ...]]


def _union_window(windows: Sequence[TimeWindow]) -> TimeWindow:
    """The smallest window covering every window in `windows`."""
    return TimeWindow(start=min(w.start for w in windows), end=max(w.end for w in windows))


def evaluated_horizon(forecast: Forecast, requested_hours: int) -> int:
    """The horizon actually evaluated: never more hours than the data covers.

    A short forecast is answered over the hours it does carry rather than
    rejected, and the reported horizon says so. Doing this is conservative in
    the safe direction: an accumulation reached within fewer hours also
    satisfies the same threshold over a longer horizon, and the maximum
    probability over fewer hours can only be lower or equal. So a truncated
    forecast can never make the system warn where a full one would not.
    """
    return min(requested_hours, forecast.horizon_hours)


def _warning_reason(warning: Warning) -> WarningReason:
    return WarningReason(level=warning.level, title=warning.title)


def _forecast_reason(
    accumulated: float, hours: int, probability: int, *, probability_threshold: int | None = None
) -> ForecastThresholdReason:
    """One reason value shared by every branch that cites an
    accumulated-mm/max-probability forecast reading, so the numbers reported
    cannot drift between branches. `probability_threshold` is set only when a
    stricter, forecast-only threshold applied."""
    return ForecastThresholdReason(
        accumulated_mm=accumulated,
        hours=hours,
        probability_pct=probability,
        probability_threshold_pct=probability_threshold,
    )


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
            self._prepare_forecast_only,
            self._prepare_degraded,
        ):
            verdict = branch(warnings, forecast)
            if verdict is not None:
                level, window, reasons = verdict
                return RiskAssessment(level, window, reasons, senamhi_status, open_meteo_status, degraded)

        window = TimeWindow(start=now, end=now + timedelta(hours=PREPARE_HORIZON_HOURS))
        return RiskAssessment(Level.NONE, window, (), senamhi_status, open_meteo_status, degraded)

    def _imminent_from_official(
        self,
        warnings: SourceResult[tuple[Warning, ...]],
        _forecast: SourceResult[Forecast],
    ) -> _Verdict | None:
        if not isinstance(warnings, Available):
            return None
        # `may_raise_imminent` bars a highlands-only rainfall warning from
        # this branch alone. It is upstream of Ferrenafe by hours of river
        # routing, so it belongs in `_prepare_combined`, which still counts
        # it. See `domain/hazards.may_raise_imminent` for the hydrology.
        matches = [
            w for w in warnings.data if w.level in self.thresholds.imminent_warning_levels and w.may_raise_imminent
        ]
        if not matches:
            return None
        window = _union_window([w.window for w in matches])
        reasons: tuple[Reason, ...] = tuple(_warning_reason(w) for w in matches)
        return Level.IMMINENT, window, reasons

    def _imminent_from_forecast(
        self,
        _warnings: SourceResult[tuple[Warning, ...]],
        forecast: SourceResult[Forecast],
    ) -> _Verdict | None:
        if not isinstance(forecast, Available):
            return None
        data = forecast.data
        hours = evaluated_horizon(data, IMMINENT_HORIZON_HOURS)
        accumulated = data.accumulated_mm(hours)
        probability = data.max_probability_pct(hours)
        if accumulated < self.thresholds.imminent_mm_24h or probability < self.thresholds.imminent_probability_pct:
            return None
        window = data.precipitation_window(hours)
        reason = _forecast_reason(accumulated, hours, probability)
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
        hours = evaluated_horizon(data, PREPARE_HORIZON_HOURS)
        accumulated = data.accumulated_mm(hours)
        probability = data.max_probability_pct(hours)
        if accumulated < self.thresholds.prepare_mm_48h or probability < self.thresholds.prepare_probability_pct:
            return None
        window = _union_window([*(w.window for w in matches), data.precipitation_window(hours)])
        combined_reasons: tuple[Reason, ...] = (
            *(_warning_reason(w) for w in matches),
            _forecast_reason(accumulated, hours, probability),
        )
        return Level.PREPARE, window, combined_reasons

    def _prepare_forecast_only(
        self,
        warnings: SourceResult[tuple[Warning, ...]],
        forecast: SourceResult[Forecast],
    ) -> _Verdict | None:
        """Branch 4: SENAMHI is available but published no qualifying warning.

        Without this branch, breaking the SENAMHI scraper made the system more
        likely to warn than a healthy one: the same forecast yielded `none`
        with a healthy, silent SENAMHI but `prepare` with an unavailable one
        (branch 5). A broken source must never buy extra sensitivity, so the
        forecast alone may fire `prepare` here too — at the same stricter
        probability threshold branch 5 uses, never at the combined-rule 60%,
        because there is no official confirmation to combine with.
        """
        if not isinstance(warnings, Available) or not isinstance(forecast, Available):
            return None
        if any(w.level in self.thresholds.prepare_warning_levels for w in warnings.data):
            return None  # a qualifying warning exists: branch 3 owns that case
        data = forecast.data
        hours = evaluated_horizon(data, PREPARE_HORIZON_HOURS)
        accumulated = data.accumulated_mm(hours)
        probability = data.max_probability_pct(hours)
        if (
            accumulated < self.thresholds.prepare_mm_48h
            or probability < self.thresholds.prepare_probability_pct_degraded
        ):
            return None
        window = data.precipitation_window(hours)
        reasons: tuple[Reason, ...] = (
            NoQualifyingWarningReason(),
            _forecast_reason(
                accumulated,
                hours,
                probability,
                probability_threshold=self.thresholds.prepare_probability_pct_degraded,
            ),
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
        hours = evaluated_horizon(data, PREPARE_HORIZON_HOURS)
        accumulated = data.accumulated_mm(hours)
        probability = data.max_probability_pct(hours)
        if (
            accumulated < self.thresholds.prepare_mm_48h
            or probability < self.thresholds.prepare_probability_pct_degraded
        ):
            return None
        window = data.precipitation_window(hours)
        reasons: tuple[Reason, ...] = (
            SenamhiUnavailableReason(),
            _forecast_reason(
                accumulated,
                hours,
                probability,
                probability_threshold=self.thresholds.prepare_probability_pct_degraded,
            ),
        )
        return Level.PREPARE, window, reasons
