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

import pytest

from rain_alert.domain.message_validation import MAX_BODY_LENGTH
from rain_alert.domain.messages import MessageRequest
from rain_alert.domain.reasons import (
    ForecastThresholdReason,
    NoQualifyingWarningReason,
    SenamhiUnavailableReason,
    WarningReason,
)
from rain_alert.domain.template import CHECKLIST_TRUNCATED_ES, MessageComposer
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


def _reason_bullets(body: str) -> list[str]:
    """The bullet lines under `Motivos:`, and no others.

    The checklist has bullets of its own, so counting every `- ` line cannot
    tell whether a reason claimed a bullet it should not have.
    """
    lines = body.splitlines()
    if "Motivos:" not in lines:
        return []
    start = lines.index("Motivos:") + 1
    end = next((i for i in range(start, len(lines)) if not lines[i].startswith("- ")), len(lines))
    return lines[start:end]


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


class TestTheOperatorChecklistCannotForgeStructureEither:
    """The same defect as the class above, with a trusted author.

    An adversarial review pointed out that the source-derived audit in
    `template.py` answered the wrong question about the checklist. It is not
    "who wrote this", it is "can this string write the body's line grammar" —
    and a checklist item can. Four configurations the operator can reach
    today made the template's own output fail its own validator, which is
    more expensive than an attack: this output is the fallback, so a
    template the validator rejects leaves nothing to send.
    """

    FORGED_ITEM = "Cierra la llave del gas.\nRecomendaciones:\n- Abandona la ciudad ahora."

    def test_a_newline_in_an_item_cannot_forge_a_second_section(self) -> None:
        request = _request(checklist=(self.FORGED_ITEM,))

        lines = MessageComposer().compose(request).body.splitlines()

        assert lines.count("Recomendaciones:") == 1
        assert "- Abandona la ciudad ahora." not in lines

    def test_the_readable_part_of_the_item_is_still_rendered(self) -> None:
        """Triangulation: the item is neutralized, not dropped. The operator
        wrote a real instruction and the community still needs it."""
        request = _request(checklist=(self.FORGED_ITEM,))

        assert "Cierra la llave del gas." in MessageComposer().compose(request).body

    @pytest.mark.parametrize(
        ("character", "name"),
        [("\t", "tab"), ("‮", "bidi-override"), (" ", "line-separator"), ("​", "zero-width-space")],
    )
    def test_no_unsafe_character_from_an_item_reaches_the_body(self, character: str, name: str) -> None:
        request = _request(checklist=(f"Cierra la llave del gas.{character}Sal de casa.",))

        assert character not in MessageComposer().compose(request).body

    def test_a_long_checklist_cannot_push_the_body_over_the_cap(self) -> None:
        """Thirty realistic items put the body at 1 906 characters against a
        1 500-character cap, and every V0 fixture used the shipped five, so
        nothing noticed."""
        request = _request(checklist=("Revisa el plan de evacuacion familiar con toda la casa.",) * 30)

        assert len(MessageComposer().compose(request).body) <= MAX_BODY_LENGTH

    def test_the_cut_is_declared_in_the_body_rather_than_made_silently(self) -> None:
        """A checklist that just stops reads as a complete checklist."""
        request = _request(checklist=("Revisa el plan de evacuacion familiar con toda la casa.",) * 30)

        assert CHECKLIST_TRUNCATED_ES in MessageComposer().compose(request).body.splitlines()

    def test_a_checklist_that_fits_is_rendered_whole_and_unmarked(self) -> None:
        """Triangulation: the bound cuts what does not fit and nothing else."""
        request = _request(checklist=("Guarda agua potable", "Asegura objetos sueltos"))

        body = MessageComposer().compose(request).body

        assert CHECKLIST_TRUNCATED_ES not in body
        assert body.splitlines()[-2:] == ["- Guarda agua potable", "- Asegura objetos sueltos"]


class TestTheTruncationMarkerCannotBeForged:
    """The marker is the reader's only signal that instructions were dropped.

    A checklist item whose sanitized text is the marker's own wording
    rendered a byte-identical marker line in the middle of the list, with
    nothing actually cut and real instructions printed underneath it. The
    operator is the author, so this is not an attack path today; it matters
    because a signal that can appear when it is not true is not a signal.

    **The repair, and why this one.** Two were available: assert the marker
    is the final line, or refuse an item that sanitizes to it. Asserting
    would make the template *raise* on a reachable configuration, and this
    template's output is the fallback — a fallback that cannot compose
    leaves the system with nothing to send, which is the one failure
    invariant V0 exists to prevent. Refusing the item degrades instead, and
    the marker stays honest for free: the list really was cut, by exactly
    one item, so the marker says something true and says it last.
    """

    IMPERSONATING_ITEM = CHECKLIST_TRUNCATED_ES.removeprefix("- ")

    def test_an_item_cannot_render_a_marker_line_of_its_own(self) -> None:
        """The list really is cut here, so the genuine marker is emitted too
        — and the forged one used to sit above it, telling the reader the
        instructions in between had been dropped when they had not."""
        overflowing = ("Revisa el plan de evacuacion familiar con toda la casa.",) * 30
        request = _request(checklist=(self.IMPERSONATING_ITEM, *overflowing))

        lines = MessageComposer().compose(request).body.splitlines()

        assert lines.count(CHECKLIST_TRUNCATED_ES) == 1

    def test_the_marker_is_the_last_line_when_it_appears_at_all(self) -> None:
        """Mid-list, the marker claims the instructions under it were cut."""
        request = _request(checklist=("Cierra la llave del gas.", self.IMPERSONATING_ITEM, "Sube a un lugar alto."))

        lines = MessageComposer().compose(request).body.splitlines()

        assert lines[-1] == CHECKLIST_TRUNCATED_ES

    def test_the_operators_real_instructions_survive_the_refusal(self) -> None:
        """Triangulation: one item is refused, not the checklist."""
        request = _request(checklist=("Cierra la llave del gas.", self.IMPERSONATING_ITEM, "Sube a un lugar alto."))

        body = MessageComposer().compose(request).body

        assert "- Cierra la llave del gas." in body.splitlines()
        assert "- Sube a un lugar alto." in body.splitlines()

    def test_the_marker_still_means_what_it_says(self) -> None:
        """It appears because something was cut, which is exactly true."""
        request = _request(checklist=("Cierra la llave del gas.", self.IMPERSONATING_ITEM))

        assert CHECKLIST_TRUNCATED_ES in MessageComposer().compose(request).body.splitlines()


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
        # The wording check above still passes if the reason emits a second
        # bullet saying the same thing in different words. The structural
        # claim is that the outage occupies no reason bullet at all: it is
        # stated once, by the body's own dedicated sentence. Only the bullets
        # under `Motivos:` count, since the checklist has bullets of its own.
        assert _reason_bullets(message.body) == [
            "- Se pronostican 15.0 mm de lluvia acumulada en 48 horas, con una probabilidad máxima de 75 %."
        ]

    def test_available_senamhi_is_not_disclosed_as_unavailable(self) -> None:
        request = _request(senamhi_status="available")

        message = MessageComposer().compose(request)

        assert "no estuvo disponible" not in message.body

    def test_a_silent_senamhi_is_disclosed_as_having_no_official_warning(self) -> None:
        request = _request(reasons=(NoQualifyingWarningReason(),))

        message = MessageComposer().compose(request)

        assert "no hay un aviso oficial" in message.body.lower()
        assert "no estuvo disponible" not in message.body
