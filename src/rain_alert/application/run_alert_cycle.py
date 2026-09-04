"""RunAlertCycle — the use case orchestrating the cycle (alert-cycle spec;
design.md section 7). The LLM composer invariant (design spec 3.2) is
preserved structurally: the composer is invoked only after `AlertPolicy` has
already authorized the send, and its return value never feeds back into any
decision.

Steps: load config -> fetch warnings -> fetch forecast -> evaluate outage
(persist state, send notices) -> evaluate risk -> apply dedup policy ->
(only if authorized and not suppressed) compose, notify, record.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from rain_alert.application.dependencies import CycleDependencies
from rain_alert.application.policies import AlertPolicy
from rain_alert.domain.config import AlertConfig
from rain_alert.domain.dedup import SendDecision
from rain_alert.domain.entities import Forecast, RiskAssessment, Warning
from rain_alert.domain.messages import (
    AlertMessage,
    AlertRecord,
    ForecastSummary,
    MessageRequest,
    OperatorNotice,
    WarningSummary,
)
from rain_alert.domain.outage import evaluate_outage
from rain_alert.domain.risk import IMMINENT_HORIZON_HOURS, evaluated_horizon
from rain_alert.domain.sources import Available, SourceResult
from rain_alert.domain.values import WarningLevel

_WARNING_SEVERITY_ORDER: Mapping[WarningLevel, int] = {
    WarningLevel.YELLOW: 1,
    WarningLevel.ORANGE: 2,
    WarningLevel.RED: 3,
}


@dataclass(frozen=True, slots=True)
class CycleResult:
    """Everything the CLI (and change 3's Lambda entrypoint) needs to
    report on one cycle, whether or not a send was authorized."""

    config_city: str
    senamhi: SourceResult[tuple[Warning, ...]]
    open_meteo: SourceResult[Forecast]
    assessment: RiskAssessment
    decision: SendDecision
    message_request: MessageRequest  # ALWAYS populated — D11
    message: AlertMessage | None  # only when composed
    sent: bool
    recipients_count: int
    notices: tuple[OperatorNotice, ...]


def _most_severe_warning(warnings: tuple[Warning, ...]) -> Warning | None:
    if not warnings:
        return None
    return max(warnings, key=lambda warning: _WARNING_SEVERITY_ORDER[warning.level])


def _build_message_request(
    config: AlertConfig,
    assessment: RiskAssessment,
    warnings: SourceResult[tuple[Warning, ...]],
    forecast: SourceResult[Forecast],
) -> MessageRequest:
    forecast_summary: ForecastSummary | None = None
    if isinstance(forecast, Available):
        data = forecast.data
        # Horizons come from the config knob and the domain constant, and are
        # clamped to what the forecast actually covers so a short series
        # summarizes truthfully instead of raising (C2/W1).
        short_hours = evaluated_horizon(data, IMMINENT_HORIZON_HOURS)
        full_hours = evaluated_horizon(data, config.forecast_hours)
        peak = data.peak_hour(full_hours)
        forecast_summary = ForecastSummary(
            mm_24h=data.accumulated_mm(short_hours),
            mm_48h=data.accumulated_mm(full_hours),
            peak_at=peak.at,
            peak_probability_pct=peak.probability_pct,
        )

    warning_summary: WarningSummary | None = None
    if isinstance(warnings, Available):
        chosen = _most_severe_warning(warnings.data)
        if chosen is not None:
            warning_summary = WarningSummary(
                source_id=chosen.source_id, level=chosen.level, title=chosen.title, window=chosen.window
            )

    return MessageRequest(
        city=config.city,
        timezone=config.timezone,
        level=assessment.level,
        window=assessment.window,
        reasons=assessment.reasons,
        forecast=forecast_summary,
        warning=warning_summary,
        senamhi_status=assessment.senamhi_status,
        open_meteo_status=assessment.open_meteo_status,
        checklist=config.checklist,
    )


class RunAlertCycle:
    """Orchestrates one full alert cycle from injected `CycleDependencies`."""

    def __init__(self, deps: CycleDependencies) -> None:
        self._deps = deps

    def execute(self) -> CycleResult:
        deps = self._deps
        now = deps.now()
        config = deps.config.load()

        warnings = deps.warnings.fetch_current_warnings(config.region, now)
        forecast = deps.forecast.fetch_forecast(config.coordinates, config.forecast_hours, now)

        active_outage = deps.alerts.get_active_outage(config.city_slug)
        outage_decision = evaluate_outage(warnings, forecast, active_outage, config.city_slug, now)
        for notice in outage_decision.notices:
            deps.notifier.send_operator_notice(notice)
        if outage_decision.next_state is None:
            deps.alerts.clear_active_outage(config.city_slug)
        else:
            deps.alerts.save_active_outage(outage_decision.next_state)

        evaluator = deps.evaluator_factory(config.thresholds)
        assessment = evaluator.evaluate(warnings, forecast, now)

        policy = AlertPolicy(deps.alerts, config.dedup_lookback_hours)
        decision = policy.decide(config.city_slug, assessment, now)

        message_request = _build_message_request(config, assessment, warnings, forecast)

        message: AlertMessage | None = None
        sent = False
        recipients_count = 0
        if decision.send and not outage_decision.suppress_community_alert:
            message = deps.composer.compose(message_request)
            recipients = deps.contacts.list_active(config.active_channel)
            deps.notifier.send_alert(message, recipients)
            record = AlertRecord(
                city_slug=config.city_slug,
                level=assessment.level,
                window=assessment.window,
                sent_at=now,
                message=message,
                reasons=assessment.reasons,
                senamhi_status=assessment.senamhi_status,
                open_meteo_status=assessment.open_meteo_status,
                composer="template",
            )
            deps.alerts.record_alert(record)
            sent = True
            recipients_count = len(recipients)

        return CycleResult(
            config_city=config.city,
            senamhi=warnings,
            open_meteo=forecast,
            assessment=assessment,
            decision=decision,
            message_request=message_request,
            message=message,
            sent=sent,
            recipients_count=recipients_count,
            notices=outage_decision.notices,
        )
