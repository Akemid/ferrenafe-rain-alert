"""RunAlertCycle — orchestration order and the dual-outage hard guard
(alert-cycle spec; source-outage-notices spec; design.md section 7).

Uses `build_fake_deps()` and hand-written recording spies (design.md
section 10) — no `unittest.mock`.
"""

from datetime import datetime, timedelta

from rain_alert.application.run_alert_cycle import RunAlertCycle
from rain_alert.domain.entities import Forecast, HourlyPoint
from rain_alert.domain.messages import AlertMessage, AlertRecord
from rain_alert.domain.sources import Available, Unavailable
from rain_alert.domain.values import Level, NoticeKind, SourceName, TimeWindow, UnavailableReason
from tests.support.wiring import DEFAULT_CONFIG, DEFAULT_NOW, build_fake_deps

NOW = DEFAULT_NOW


def _forecast(hours: int, *, mm_total: float, probability_pct: int, now: datetime = NOW) -> Available[Forecast]:
    mm_per_hour = mm_total / hours
    points = tuple(
        HourlyPoint(at=now + timedelta(hours=h), precipitation_mm=mm_per_hour, probability_pct=probability_pct)
        for h in range(hours)
    )
    return Available(data=Forecast(location=DEFAULT_CONFIG.coordinates, points=points), fetched_at=now)


def _unavailable(source: SourceName, *, now: datetime = NOW) -> Unavailable:
    return Unavailable(source=source, reason=UnavailableReason.TIMEOUT, detail="timed out", observed_at=now)


def _record(level: Level, window: TimeWindow) -> AlertRecord:
    message = AlertMessage(title="Alerta", body="Cuerpo", level=level, valid_until=window.end)
    return AlertRecord(
        city_slug=DEFAULT_CONFIG.city_slug,
        level=level,
        window=window,
        sent_at=NOW,
        message=message,
        reasons=(),
        senamhi_status="available",
        open_meteo_status="available",
        composer="template",
    )


class TestAuthorizedSendRunsTheFullChain:
    def test_composer_notifier_and_repository_are_all_invoked(self) -> None:
        forecast = _forecast(24, mm_total=29.8, probability_pct=70)
        deps = build_fake_deps(forecast=forecast)

        result = RunAlertCycle(deps).execute()

        assert result.assessment.level == Level.IMMINENT
        assert result.decision.send is True
        assert result.sent is True
        assert len(deps.composer.calls) == 1
        assert len(deps.notifier.alert_calls) == 1
        assert len(deps.alerts.record_calls) == 1

    def test_message_is_populated_on_the_result(self) -> None:
        forecast = _forecast(24, mm_total=29.8, probability_pct=70)
        deps = build_fake_deps(forecast=forecast)

        result = RunAlertCycle(deps).execute()

        assert result.message is not None
        assert result.message.level == Level.IMMINENT


class TestRecordAfterNotify:
    def test_recorded_alert_carries_level_window_and_message(self) -> None:
        forecast = _forecast(24, mm_total=29.8, probability_pct=70)
        deps = build_fake_deps(forecast=forecast)

        result = RunAlertCycle(deps).execute()

        assert len(deps.alerts.record_calls) == 1
        recorded = deps.alerts.record_calls[0]
        assert recorded.level == result.assessment.level
        assert recorded.window == result.assessment.window
        assert recorded.message == result.message


class TestShortHorizonForecastsDoNotCrashTheCycle:
    def test_single_hour_cloudburst_reaches_imminent(self) -> None:
        """C2: one point at 25 mm / 80% used to abort the cycle with
        `TimeWindow.end must be strictly after start`. A cloudburst is the
        exact event this system exists to warn about."""
        points = (HourlyPoint(at=NOW, precipitation_mm=25.0, probability_pct=80),)
        forecast = Available(data=Forecast(location=DEFAULT_CONFIG.coordinates, points=points), fetched_at=NOW)
        deps = build_fake_deps(forecast=forecast)

        result = RunAlertCycle(deps).execute()

        assert result.assessment.level == Level.IMMINENT
        assert result.sent is True


class TestDedupShortCircuitsTheCycle:
    def test_neither_composer_nor_notifier_is_invoked_and_nothing_is_recorded(self) -> None:
        forecast = _forecast(24, mm_total=29.8, probability_pct=70)
        same_window = forecast.data.precipitation_window(24)
        prior = [_record(Level.IMMINENT, same_window)]
        deps = build_fake_deps(forecast=forecast, prior_alerts=prior)

        result = RunAlertCycle(deps).execute()

        assert result.decision.send is False
        assert result.sent is False
        assert deps.composer.calls == []
        assert deps.notifier.alert_calls == []
        # only the pre-seeded prior alert exists; nothing new was recorded
        assert deps.alerts.record_calls == []

    def test_message_request_is_still_populated_on_a_deduplicated_cycle(self) -> None:
        """D11: CycleResult.message_request is always populated, even when
        nothing was sent — the CLI renders the PREVIEW from it."""
        forecast = _forecast(24, mm_total=29.8, probability_pct=70)
        same_window = forecast.data.precipitation_window(24)
        prior = [_record(Level.IMMINENT, same_window)]
        deps = build_fake_deps(forecast=forecast, prior_alerts=prior)

        result = RunAlertCycle(deps).execute()

        assert result.message_request is not None
        assert result.message is None


class TestDualSourceOutageSuppressesCommunityAlerts:
    def test_three_consecutive_dual_outage_cycles_send_zero_community_alerts_and_one_notice(self) -> None:
        deps = build_fake_deps(warnings=_unavailable(SourceName.SENAMHI), forecast=_unavailable(SourceName.OPEN_METEO))

        results = [RunAlertCycle(deps).execute() for _ in range(3)]

        for result in results:
            assert result.assessment.level == Level.NONE
            assert result.sent is False
        assert deps.composer.calls == []
        assert deps.notifier.alert_calls == []
        assert deps.alerts.record_calls == []
        assert len(deps.notifier.notice_calls) == 1

    def test_recovery_after_dual_outage_emits_exactly_one_recovery_notice(self) -> None:
        deps = build_fake_deps(warnings=_unavailable(SourceName.SENAMHI), forecast=_unavailable(SourceName.OPEN_METEO))
        for _ in range(3):
            RunAlertCycle(deps).execute()
        assert len(deps.notifier.notice_calls) == 1

        # Open-Meteo returns; SENAMHI remains unavailable. Design row 7
        # (narrow with something still down) emits both a recovery notice
        # for Open-Meteo and a fresh unavailable notice for the still-down
        # SENAMHI — the spec only guarantees exactly one recovery notice.
        deps.forecast.result = Available(data=_forecast(48, mm_total=0.0, probability_pct=0).data, fetched_at=NOW)
        RunAlertCycle(deps).execute()

        recovery_notices = [n for n in deps.notifier.notice_calls if n.kind == NoticeKind.SOURCES_RECOVERED]
        assert len(recovery_notices) == 1
        assert recovery_notices[0].sources == frozenset({SourceName.OPEN_METEO})
        assert deps.notifier.notice_calls[1].sources == frozenset({SourceName.OPEN_METEO})


class TestDegradedModeWiring:
    def test_degraded_assessment_flows_through_to_the_composed_message(self) -> None:
        forecast = _forecast(48, mm_total=15.0, probability_pct=75)
        deps = build_fake_deps(warnings=_unavailable(SourceName.SENAMHI), forecast=forecast)

        result = RunAlertCycle(deps).execute()

        assert result.assessment.degraded is True
        assert result.assessment.level == Level.PREPARE
        assert result.sent is True
        assert result.message is not None
        assert "SENAMHI" in result.message.body
        assert "no estuvo disponible" in result.message.body
