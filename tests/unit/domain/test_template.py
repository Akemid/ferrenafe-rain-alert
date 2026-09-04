"""MessageComposer — deterministic template (design.md D11; alert-cycle
spec). Recipient-facing text is neutral, professional Spanish by product
decision (design spec 7.4) — the one exception to this codebase's
English-artifact default.

The evaluator's reasons are structured values, so the operator audit trail
(English, `domain/reasons.py`) and the community body (Spanish, here) render
the same decision for two different audiences instead of leaking one into
the other.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from rain_alert.domain.messages import MessageRequest
from rain_alert.domain.reasons import (
    ForecastThresholdReason,
    NoQualifyingWarningReason,
    SenamhiUnavailableReason,
    WarningReason,
)
from rain_alert.domain.template import MessageComposer
from rain_alert.domain.values import Level, TimeWindow, WarningLevel

NOW = datetime(2026, 9, 3, 12, tzinfo=UTC)
WINDOW = TimeWindow(start=NOW, end=NOW + timedelta(hours=48))
LIMA = "America/Lima"

# Phrases that only ever belong to the operator audit trail. If any of these
# reach the community body, the recipient is reading internal English
# threshold debug output instead of a Spanish alert (design spec 7.4).
OPERATOR_ONLY_PHRASES = (
    "max probability",
    "unavailable",
    "warning",
    "threshold",
    "forecast-only",
    "mm / ",
)


def _request(**overrides: object) -> MessageRequest:
    defaults: dict[str, object] = {
        "city": "Ferreñafe",
        "timezone": LIMA,
        "level": Level.PREPARE,
        "window": WINDOW,
        "reasons": (ForecastThresholdReason(accumulated_mm=10.0, hours=48, probability_pct=60),),
        "forecast": None,
        "warning": None,
        "senamhi_status": "available",
        "open_meteo_status": "available",
        "checklist": ("Guarda agua potable", "Asegura objetos sueltos"),
    }
    defaults.update(overrides)
    return MessageRequest(**defaults)  # type: ignore[arg-type]


class TestTemplateContent:
    def test_message_includes_city_level_and_every_reason_in_spanish(self) -> None:
        request = _request(
            reasons=(
                WarningReason(level=WarningLevel.YELLOW, title="Aviso de lluvias"),
                ForecastThresholdReason(accumulated_mm=10.0, hours=48, probability_pct=60),
            )
        )

        message = MessageComposer().compose(request)

        assert "Ferreñafe" in message.body
        assert "prepárate" in message.body.lower()
        assert "Aviso oficial del SENAMHI" in message.body
        assert "amarillo" in message.body
        assert "Aviso de lluvias" in message.body
        assert "10.0 mm" in message.body
        assert "48 horas" in message.body
        assert "60" in message.body

    def test_message_level_matches_the_request_level(self) -> None:
        request = _request(level=Level.IMMINENT)

        message = MessageComposer().compose(request)

        assert message.level == Level.IMMINENT

    def test_message_valid_until_is_the_window_end(self) -> None:
        request = _request()

        message = MessageComposer().compose(request)

        assert message.valid_until == WINDOW.end

    def test_checklist_items_are_listed(self) -> None:
        request = _request(checklist=("Guarda agua potable",))

        message = MessageComposer().compose(request)

        assert "Guarda agua potable" in message.body


class TestLocalTimeDisplay:
    def test_window_is_rendered_in_the_configured_timezone_not_raw_utc(self) -> None:
        """D7: domain datetimes are UTC, but a community reader must not be
        handed an ISO-8601 string with a `+00:00` offset."""
        request = _request()

        message = MessageComposer().compose(request)

        local_start = WINDOW.start.astimezone(ZoneInfo(LIMA))
        assert local_start.strftime("%d/%m/%Y %H:%M") in message.body
        assert "+00:00" not in message.body
        assert WINDOW.start.isoformat() not in message.body

    def test_timezone_name_is_disclosed_so_the_hour_is_unambiguous(self) -> None:
        message = MessageComposer().compose(_request())

        assert LIMA in message.body


class TestNoOperatorAuditTextReachesRecipients:
    def test_body_of_a_normal_alert_carries_no_english_audit_phrases(self) -> None:
        request = _request(
            reasons=(
                WarningReason(level=WarningLevel.ORANGE, title="Aviso de lluvias intensas"),
                ForecastThresholdReason(accumulated_mm=29.8, hours=24, probability_pct=70),
            )
        )

        body = MessageComposer().compose(request).body.lower()

        for phrase in OPERATOR_ONLY_PHRASES:
            assert phrase not in body, f"operator-only phrase leaked into the community body: {phrase!r}"

    def test_body_of_a_degraded_alert_carries_no_english_audit_phrases(self) -> None:
        request = _request(
            senamhi_status="unavailable",
            reasons=(
                SenamhiUnavailableReason(),
                ForecastThresholdReason(
                    accumulated_mm=15.0, hours=48, probability_pct=75, probability_threshold_pct=70
                ),
            ),
        )

        body = MessageComposer().compose(request).body.lower()

        for phrase in OPERATOR_ONLY_PHRASES:
            assert phrase not in body, f"operator-only phrase leaked into the community body: {phrase!r}"


class TestDegradedDisclosure:
    def test_senamhi_unavailable_is_disclosed_in_the_message(self) -> None:
        request = _request(senamhi_status="unavailable", reasons=(SenamhiUnavailableReason(),))

        message = MessageComposer().compose(request)

        assert "SENAMHI" in message.body
        assert "no estuvo disponible" in message.body

    def test_the_outage_is_stated_exactly_once(self) -> None:
        """The reason value and the degraded sentence used to state the same
        fact twice, in two different languages."""
        request = _request(
            senamhi_status="unavailable",
            reasons=(
                SenamhiUnavailableReason(),
                ForecastThresholdReason(
                    accumulated_mm=15.0, hours=48, probability_pct=75, probability_threshold_pct=70
                ),
            ),
        )

        message = MessageComposer().compose(request)

        assert message.body.count("no estuvo disponible") == 1

    def test_available_senamhi_is_not_disclosed_as_unavailable(self) -> None:
        request = _request(senamhi_status="available")

        message = MessageComposer().compose(request)

        assert "no estuvo disponible" not in message.body

    def test_a_silent_senamhi_is_disclosed_as_having_no_official_warning(self) -> None:
        request = _request(reasons=(NoQualifyingWarningReason(),))

        message = MessageComposer().compose(request)

        assert "no hay un aviso oficial" in message.body.lower()
        assert "no estuvo disponible" not in message.body
