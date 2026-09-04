"""AlertPolicy — the port-bound shell over the pure `should_send` rule
(alert-dedup spec "Query before decision"; design.md section 6).

The pure rule itself is covered by `tests/unit/domain/test_dedup.py`. What
is tested here is the only thing the shell adds: the key range it asks
`AlertRepository` for. Getting that range wrong silently hides a prior
alert, and the community gets re-alerted every six hours.
"""

from datetime import UTC, datetime, timedelta

from rain_alert.application.policies import AlertPolicy
from rain_alert.domain.entities import RiskAssessment
from rain_alert.domain.messages import AlertMessage, AlertRecord
from rain_alert.domain.values import Level, TimeWindow
from tests.support.fakes import FakeAlertRepository

NOW = datetime(2026, 9, 3, 12, tzinfo=UTC)
CITY_SLUG = "ferrenafe"
LOOKBACK_HOURS = 72


def _assessment(level: Level, window: TimeWindow) -> RiskAssessment:
    return RiskAssessment(
        level=level,
        window=window,
        reasons=(),
        senamhi_status="available",
        open_meteo_status="available",
        degraded=False,
    )


def _record(level: Level, window: TimeWindow) -> AlertRecord:
    message = AlertMessage(title="Alerta", body="Cuerpo", level=level, valid_until=window.end)
    return AlertRecord(
        city_slug=CITY_SLUG,
        level=level,
        window=window,
        sent_at=window.start,
        message=message,
        reasons=(),
        senamhi_status="available",
        open_meteo_status="available",
        composer="template",
    )


class TestLookbackRange:
    def test_a_long_aviso_starting_before_the_lookback_is_not_re_alerted(self) -> None:
        """W2: imminent windows come from the union of SENAMHI warning
        windows and can start before `now - lookback`. Querying only from
        `now - lookback` made the prior alert invisible, so the community was
        re-alerted every cycle for the whole duration of a long aviso."""
        long_window = TimeWindow(start=NOW - timedelta(hours=100), end=NOW + timedelta(hours=6))
        alerts = FakeAlertRepository(alerts=[_record(Level.IMMINENT, long_window)])
        policy = AlertPolicy(alerts, LOOKBACK_HOURS)

        decision = policy.decide(CITY_SLUG, _assessment(Level.IMMINENT, long_window), NOW)

        assert decision.send is False

    def test_the_query_range_starts_no_later_than_the_assessment_window(self) -> None:
        long_window = TimeWindow(start=NOW - timedelta(hours=100), end=NOW + timedelta(hours=6))
        alerts = FakeAlertRepository()
        policy = AlertPolicy(alerts, LOOKBACK_HOURS)

        policy.decide(CITY_SLUG, _assessment(Level.IMMINENT, long_window), NOW)

        (_, earliest_start, latest_start) = alerts.query_calls[0]
        assert earliest_start <= long_window.start
        assert latest_start == long_window.end

    def test_the_lookback_still_governs_a_window_that_starts_inside_it(self) -> None:
        """The clamp must widen the range, never narrow it: a short window
        must still see alerts from the full lookback period."""
        recent_window = TimeWindow(start=NOW, end=NOW + timedelta(hours=48))
        alerts = FakeAlertRepository()
        policy = AlertPolicy(alerts, LOOKBACK_HOURS)

        policy.decide(CITY_SLUG, _assessment(Level.PREPARE, recent_window), NOW)

        (_, earliest_start, _) = alerts.query_calls[0]
        assert earliest_start == NOW - timedelta(hours=LOOKBACK_HOURS)

    def test_a_prior_alert_inside_the_lookback_still_blocks_a_repeat(self) -> None:
        window = TimeWindow(start=NOW, end=NOW + timedelta(hours=48))
        alerts = FakeAlertRepository(alerts=[_record(Level.PREPARE, window)])
        policy = AlertPolicy(alerts, LOOKBACK_HOURS)

        decision = policy.decide(CITY_SLUG, _assessment(Level.PREPARE, window), NOW)

        assert decision.send is False
