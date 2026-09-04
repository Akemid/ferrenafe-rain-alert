"""should_send — pure alert-dedup rule (alert-dedup spec; design.md section 6)."""

from datetime import UTC, datetime, timedelta

from rain_alert.domain.dedup import should_send
from rain_alert.domain.entities import RiskAssessment
from rain_alert.domain.messages import AlertMessage, AlertRecord
from rain_alert.domain.values import Level, TimeWindow

NOW = datetime(2026, 9, 3, 12, tzinfo=UTC)
WINDOW = TimeWindow(start=NOW, end=NOW + timedelta(hours=48))


def _assessment(level: Level) -> RiskAssessment:
    return RiskAssessment(
        level=level,
        window=WINDOW,
        reasons=(),
        senamhi_status="available",
        open_meteo_status="available",
        degraded=False,
    )


def _record(level: Level, window: TimeWindow = WINDOW) -> AlertRecord:
    message = AlertMessage(title="Alerta", body="Cuerpo", level=level, valid_until=window.end)
    return AlertRecord(
        city_slug="ferrenafe",
        level=level,
        window=window,
        sent_at=NOW,
        message=message,
        reasons=(),
        senamhi_status="available",
        open_meteo_status="available",
        composer="template",
    )


def test_first_alert_for_a_window_is_authorized() -> None:
    decision = should_send(_assessment(Level.PREPARE), prior=())

    assert decision.send is True


def test_prepare_escalates_to_imminent_is_authorized() -> None:
    prior = (_record(Level.PREPARE),)

    decision = should_send(_assessment(Level.IMMINENT), prior=prior)

    assert decision.send is True


def test_repeat_level_is_not_authorized() -> None:
    prior = (_record(Level.PREPARE),)

    decision = should_send(_assessment(Level.PREPARE), prior=prior)

    assert decision.send is False


def test_imminent_de_escalates_to_prepare_is_not_authorized() -> None:
    prior = (_record(Level.IMMINENT),)

    decision = should_send(_assessment(Level.PREPARE), prior=prior)

    assert decision.send is False


def test_de_escalation_to_none_is_not_authorized() -> None:
    prior = (_record(Level.PREPARE),)

    decision = should_send(_assessment(Level.NONE), prior=prior)

    assert decision.send is False


def test_non_overlapping_prior_record_does_not_block_a_new_window() -> None:
    earlier_window = TimeWindow(start=NOW - timedelta(hours=96), end=NOW - timedelta(hours=48))
    prior = (_record(Level.IMMINENT, window=earlier_window),)

    decision = should_send(_assessment(Level.PREPARE), prior=prior)

    assert decision.send is True
