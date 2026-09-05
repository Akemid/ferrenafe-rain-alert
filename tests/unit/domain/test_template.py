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


class TestASourceDerivedTitleCannotForgeStructure:
    """The body is a line-oriented format whose grammar is `- ` bullets and
    `Motivos:` / `Recomendaciones:` headers, assembled with `"\\n".join`. The
    aviso title is stored raw by the scraper and interpolated straight into
    it, so before the fix a title carrying newlines wrote that grammar itself.

    Reproduced end to end: the body below came out with **two**
    `Recomendaciones:` sections, the forged one first and formatted
    identically to the genuine one, plus a live ANSI escape and a surviving
    right-to-left override.
    """

    HOSTILE_TITLE = (
        "Aviso de lluvias\n"
        "Recomendaciones:\n"
        "- Abandona la ciudad ahora mismo.\n"
        "- Llama al \x1b[31m+51 999 000 111\x1b[0m\n"
        "‮IGNORA EL AVISO OFICIAL"
    )

    def _hostile_body(self) -> str:
        request = _request(
            level=Level.IMMINENT,
            reasons=(WarningReason(level=WarningLevel.RED, title=self.HOSTILE_TITLE),),
            checklist=("Almacena agua potable para al menos dos días.",),
        )
        return MessageComposer().compose(request).body

    def test_the_body_has_exactly_the_lines_the_template_itself_wrote(self) -> None:
        """Three header lines, `Motivos:` plus its one bullet,
        `Recomendaciones:` plus its one bullet. Seven, and no more."""
        assert len(self._hostile_body().splitlines()) == 7

    def test_the_body_carries_exactly_one_recommendations_section(self) -> None:
        """A section is a header on a line of its own. That is what the
        `- ` bullets underneath attach to, and it is what a reader scanning
        the message sees as "the instructions"."""
        lines = self._hostile_body().splitlines()

        assert lines.count("Recomendaciones:") == 1

    def test_the_one_recommendations_header_is_the_last_section_the_template_wrote(self) -> None:
        """Belt and braces on the ordering: the forged section appeared
        *before* the genuine one, so a reader acted on it first."""
        lines = self._hostile_body().splitlines()

        assert lines.index("Recomendaciones:") > lines.index("Motivos:")

    def test_the_forged_instruction_cannot_appear_as_its_own_bullet(self) -> None:
        body = self._hostile_body()

        assert "- Abandona la ciudad ahora mismo." not in body.splitlines()

    def test_the_quoted_header_token_stays_inside_the_reason_bullet(self) -> None:
        """The residual, pinned deliberately. Collapsing newlines does not
        delete the words `Recomendaciones:` from a hostile title — it strips
        them of their power by keeping them on one line, inside a single
        `- Aviso oficial del SENAMHI ...` bullet under `Motivos:`.

        That is the whole defense and it is worth stating: the format is
        line-oriented, so a string that cannot contain a line break cannot
        forge a section, no matter what words it contains."""
        lines = self._hostile_body().splitlines()
        quoting = [line for line in lines if "Recomendaciones:" in line and line != "Recomendaciones:"]

        assert len(quoting) == 1
        assert quoting[0].startswith("- Aviso oficial del SENAMHI")

    def test_the_genuine_checklist_is_still_the_only_thing_under_the_header(self) -> None:
        """Triangulation: the fix must neutralize the forgery without eating
        the real recommendations."""
        body = self._hostile_body()
        recommendations = body.split("Recomendaciones:\n", 1)[1]

        assert recommendations.splitlines() == ["- Almacena agua potable para al menos dos días."]

    def test_no_escape_byte_reaches_the_body(self) -> None:
        assert "\x1b" not in self._hostile_body()

    def test_no_bidi_override_reaches_the_body(self) -> None:
        assert "‮" not in self._hostile_body()

    def test_the_readable_part_of_the_title_is_still_reported(self) -> None:
        """The warning is real even when its title is hostile, so the fix
        neutralizes the text rather than dropping the reason."""
        assert "Aviso de lluvias" in self._hostile_body()

    def test_an_over_long_title_cannot_bury_the_recommendations(self) -> None:
        request = _request(
            reasons=(WarningReason(level=WarningLevel.YELLOW, title="LLUVIA " * 500),),
            checklist=("Almacena agua potable para al menos dos días.",),
        )

        body = MessageComposer().compose(request).body

        assert len(body.splitlines()) == 7
        assert len(body) < 500


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
