"""MessageComposer — deterministic template (design.md D11; alert-cycle
spec). Recipient-facing text is neutral, professional Spanish by product
decision — the one exception to this codebase's English-artifact default.
"""

from datetime import UTC, datetime, timedelta

from rain_alert.domain.messages import MessageRequest
from rain_alert.domain.template import MessageComposer
from rain_alert.domain.values import Level, TimeWindow

NOW = datetime(2026, 9, 3, 12, tzinfo=UTC)
WINDOW = TimeWindow(start=NOW, end=NOW + timedelta(hours=48))


def _request(**overrides: object) -> MessageRequest:
    defaults: dict[str, object] = {
        "city": "Ferreñafe",
        "timezone": "America/Lima",
        "level": Level.PREPARE,
        "window": WINDOW,
        "reasons": ("10.0 mm / 48 h at 60% max probability",),
        "forecast": None,
        "warning": None,
        "senamhi_status": "available",
        "open_meteo_status": "available",
        "checklist": ("Store water", "Secure loose objects"),
    }
    defaults.update(overrides)
    return MessageRequest(**defaults)  # type: ignore[arg-type]


class TestTemplateContent:
    def test_message_includes_city_level_window_and_each_reason(self) -> None:
        request = _request(reasons=("reason one", "reason two"))

        message = MessageComposer().compose(request)

        assert "Ferreñafe" in message.body
        assert "prepárate" in message.body.lower()
        assert WINDOW.start.isoformat() in message.body
        assert WINDOW.end.isoformat() in message.body
        assert "reason one" in message.body
        assert "reason two" in message.body

    def test_message_level_matches_the_request_level(self) -> None:
        request = _request(level=Level.IMMINENT)

        message = MessageComposer().compose(request)

        assert message.level == Level.IMMINENT

    def test_message_valid_until_is_the_window_end(self) -> None:
        request = _request()

        message = MessageComposer().compose(request)

        assert message.valid_until == WINDOW.end


class TestDegradedDisclosure:
    def test_senamhi_unavailable_is_disclosed_in_the_message(self) -> None:
        request = _request(senamhi_status="unavailable")

        message = MessageComposer().compose(request)

        assert "SENAMHI" in message.body
        assert "no estuvo disponible" in message.body

    def test_available_senamhi_is_not_disclosed_as_unavailable(self) -> None:
        request = _request(senamhi_status="available")

        message = MessageComposer().compose(request)

        assert "no estuvo disponible" not in message.body
