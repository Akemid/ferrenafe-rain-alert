"""`sanitize_source_text` — the single chokepoint every source-derived free
text passes through before it is rendered for a human (design.md 3.2).

The threat is concrete and was reproduced end to end: a SENAMHI aviso title
containing newlines forged a second `Recomendaciones:` section inside the
Spanish community body, ahead of the genuine one and formatted identically,
carrying a live ANSI escape and a surviving bidi override. A reader in a
flood cannot tell a forged instruction from the system's own.
"""

from __future__ import annotations

import pytest

from rain_alert.domain.sanitize import (
    MAX_SOURCE_TEXT_LENGTH,
    TRUNCATION_MARKER,
    sanitize_source_text,
)

BIDI_CONTROLS = ("‪", "‫", "‬", "‭", "‮", "⁦", "⁧", "⁨", "⁩")


class TestStructureCannotBeForged:
    """The body is a line-oriented format whose grammar is `- ` bullets and
    `Motivos:` / `Recomendaciones:` headers. A source-derived string that can
    contain a newline can therefore write that grammar itself."""

    def test_a_newline_becomes_a_single_space(self) -> None:
        assert sanitize_source_text("Aviso\nRecomendaciones:") == "Aviso Recomendaciones:"

    def test_a_carriage_return_line_feed_becomes_a_single_space(self) -> None:
        assert sanitize_source_text("Aviso\r\nde lluvias") == "Aviso de lluvias"

    def test_a_tab_becomes_a_single_space(self) -> None:
        assert sanitize_source_text("Aviso\tde lluvias") == "Aviso de lluvias"

    def test_a_run_of_mixed_whitespace_collapses_to_one_space(self) -> None:
        assert sanitize_source_text("Aviso \n\n\t  de lluvias") == "Aviso de lluvias"

    def test_leading_and_trailing_whitespace_is_trimmed(self) -> None:
        assert sanitize_source_text("  \n Aviso de lluvias \t ") == "Aviso de lluvias"

    def test_the_result_is_always_one_line(self) -> None:
        forged = "Aviso\nRecomendaciones:\n- Abandona la ciudad ahora mismo."

        assert len(sanitize_source_text(forged).splitlines()) == 1


class TestControlCharactersAreRemoved:
    def test_the_escape_byte_of_an_ansi_sequence_is_removed(self) -> None:
        """The operator reads this in a terminal, so a live escape sequence is
        the operator's problem as much as the recipient's."""
        cleaned = sanitize_source_text("Aviso \x1b[31mrojo\x1b[0m")

        assert "\x1b" not in cleaned
        assert "rojo" in cleaned

    @pytest.mark.parametrize("control", ["\x00", "\x07", "\x08", "\x1b", "\x7f", "\x80", "\x9f"])
    def test_c0_and_c1_control_characters_are_removed(self, control: str) -> None:
        assert control not in sanitize_source_text(f"Aviso{control}de lluvias")

    def test_a_null_byte_does_not_truncate_the_remaining_text(self) -> None:
        assert sanitize_source_text("Aviso\x00de lluvias") == "Avisode lluvias"


class TestBidiOverridesAreRemoved:
    """A right-to-left override reverses the display order of everything after
    it, so a title can make the rest of the line read backwards on screen
    while the bytes say something else."""

    @pytest.mark.parametrize("control", BIDI_CONTROLS, ids=[f"U+{ord(c):04X}" for c in BIDI_CONTROLS])
    def test_every_bidi_control_is_removed(self, control: str) -> None:
        assert control not in sanitize_source_text(f"Aviso{control}de lluvias")

    def test_ordinary_accented_spanish_survives_untouched(self) -> None:
        """Triangulation: the filter removes controls, not the language. A
        rule that stripped non-ASCII would mangle every real aviso title."""
        assert sanitize_source_text("PRECIPITACIÓN EN LA SIERRA — ampliación") == (
            "PRECIPITACIÓN EN LA SIERRA — ampliación"
        )


class TestLengthIsCapped:
    def test_a_realistic_live_title_is_returned_whole(self) -> None:
        """The longest live SENAMHI title observed on the 2026-09-04 harvest of
        all 12 regional pages is well under 150 characters, suffix included."""
        title = "PRECIPITACIONES DE MODERADA A FUERTE INTENSIDAD EN LA COSTA NORTE Y SIERRA (EXTENSION DEL AVISO 335)"

        assert len(title) < 150
        assert sanitize_source_text(title) == title

    def test_an_over_long_text_is_truncated_to_the_cap(self) -> None:
        assert len(sanitize_source_text("A" * 5_000)) == MAX_SOURCE_TEXT_LENGTH

    def test_truncation_is_visible_rather_than_silent(self) -> None:
        """A silently clipped title reads as a real title that happens to end
        oddly. The operator must be able to tell the system cut it."""
        truncated = sanitize_source_text("A" * 5_000)

        assert truncated.endswith(TRUNCATION_MARKER)

    def test_the_cap_is_measured_after_the_controls_are_removed(self) -> None:
        """Padding with control characters must not be a way to push real text
        past the cap."""
        padded = "\x00" * 5_000 + "Aviso de lluvias"

        assert sanitize_source_text(padded) == "Aviso de lluvias"


class TestTheFunctionIsSafeToApplyTwice:
    def test_sanitizing_an_already_sanitized_text_changes_nothing(self) -> None:
        """It is applied at more than one rendering boundary, so it has to be
        idempotent or the boundaries would have to know about each other."""
        once = sanitize_source_text("Aviso\n\x1b[31m‮de lluvias" + "B" * 5_000)

        assert sanitize_source_text(once) == once

    def test_an_empty_text_stays_empty(self) -> None:
        assert sanitize_source_text("") == ""
