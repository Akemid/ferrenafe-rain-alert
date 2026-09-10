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
    has_unsafe_characters,
    sanitize_source_text,
)

BIDI_CONTROLS = ("‪", "‫", "‬", "‭", "‮", "⁦", "⁧", "⁨", "⁩")

#: Invisible to a reader and load-bearing to a parser. Two families:
#: the Unicode line and paragraph separators, which `str.splitlines` treats
#: as line breaks and `"\n".split` does not; and the zero-width/format class,
#: which renders as nothing at all while breaking any exact-match comparison
#: the text is subjected to.
LINE_SEPARATORS = (" ", " ")
ZERO_WIDTH_AND_FORMAT = (
    "­",  # soft hyphen
    "؜",  # Arabic letter mark
    "​",  # zero width space
    "‌",  # zero width non-joiner
    "‍",  # zero width joiner
    "‎",  # left-to-right mark
    "‏",  # right-to-left mark
    "⁠",  # word joiner
    "⁤",  # invisible plus
    "﻿",  # zero width no-break space (BOM)
    "￻",  # interlinear annotation terminator
)


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


class TestTheInvisibleCharactersAreRemovedToo:
    """The gap an adversarial review found: `\\s` does not match the
    zero-width and format class, so U+200B, U+200C, U+FEFF, U+061C and U+200E
    passed through `sanitize_source_text` untouched and a hostile aviso title
    could carry one into the prompt for the model to copy.

    U+2028 and U+2029 were collapsed to a space by the whitespace rule but
    were absent from the removed class, so `has_unsafe_characters` called
    them safe — while `str.splitlines`, which every renderer in this codebase
    uses, treats U+2028 as a line break.
    """

    @pytest.mark.parametrize(
        "character",
        LINE_SEPARATORS + ZERO_WIDTH_AND_FORMAT,
        ids=[f"U+{ord(c):04X}" for c in LINE_SEPARATORS + ZERO_WIDTH_AND_FORMAT],
    )
    def test_it_is_removed_by_the_sanitizer(self, character: str) -> None:
        assert character not in sanitize_source_text(f"Aviso{character}de lluvias")

    @pytest.mark.parametrize(
        "character",
        LINE_SEPARATORS + ZERO_WIDTH_AND_FORMAT,
        ids=[f"U+{ord(c):04X}" for c in LINE_SEPARATORS + ZERO_WIDTH_AND_FORMAT],
    )
    def test_the_predicate_calls_it_unsafe(self, character: str) -> None:
        assert has_unsafe_characters(f"Aviso{character}de lluvias") is True

    def test_a_line_separator_cannot_survive_into_a_rendered_line(self) -> None:
        """The concrete defect: the sanitized text is rendered with
        `str.splitlines`, which splits on U+2028. If one survived, a title
        would write the body's line grammar again."""
        forged = "Aviso Recomendaciones: - Abandona la ciudad ahora mismo."

        assert len(sanitize_source_text(forged).splitlines()) == 1

    def test_a_zero_width_space_cannot_hide_inside_a_section_header(self) -> None:
        """Invisible to the reader, fatal to an exact-match comparison: the
        header below reads as `Recomendaciones:` on screen but compares
        unequal to it."""
        assert sanitize_source_text("Recomendaciones​:") == "Recomendaciones:"


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


class TestTheUnsafeCharacterPredicate:
    """`has_unsafe_characters` answers "would the sanitizer have to remove
    something from this?" without removing it. The message validator needs the
    question, not the transformation: an agent-composed body carrying a control
    character is rejected outright, never quietly cleaned up.

    It exposes the *existing* `_REMOVED` class rather than letting a second
    module copy the regex. Two copies of that class is how a bidi override
    eventually gets through one of them (design.md section 6, rule 7).
    """

    def test_ordinary_accented_spanish_is_not_unsafe(self) -> None:
        assert has_unsafe_characters("Alerta de lluvias — Ferreñafe — prepárate") is False

    def test_an_empty_text_is_not_unsafe(self) -> None:
        assert has_unsafe_characters("") is False

    def test_the_escape_byte_of_an_ansi_sequence_is_unsafe(self) -> None:
        assert has_unsafe_characters("Aviso \x1b[31mde lluvias\x1b[0m") is True

    @pytest.mark.parametrize("control", ["\x00", "\x07", "\n", "\r", "\t", "\x7f", "\x9f"])
    def test_every_control_character_is_unsafe(self, control: str) -> None:
        """Newline included: the predicate reports the character class, and it
        is the caller that knows whether a line break is legitimate where it
        is looking. A title has no legitimate line break; a body does."""
        assert has_unsafe_characters(f"Aviso{control}de lluvias") is True

    @pytest.mark.parametrize("control", BIDI_CONTROLS)
    def test_every_bidi_control_is_unsafe(self, control: str) -> None:
        assert has_unsafe_characters(f"Aviso {control}de lluvias") is True

    def test_it_agrees_with_the_sanitizer_on_what_it_would_remove(self) -> None:
        """Triangulation against the transformation the predicate mirrors:
        anything the predicate calls safe survives sanitizing unchanged, apart
        from the whitespace collapsing and trimming the predicate never claims
        to cover."""
        safe = "PRECIPITACIONES DE MODERADA A FUERTE INTENSIDAD (EXTENSION DEL AVISO 335)"

        assert has_unsafe_characters(safe) is False
        assert sanitize_source_text(safe) == safe

    def test_an_empty_text_stays_empty(self) -> None:
        assert sanitize_source_text("") == ""
