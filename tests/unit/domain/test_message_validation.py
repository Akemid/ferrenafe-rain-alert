"""`validate_message` — the rules the system is willing to say out loud
(design.md D16 and section 6; agent-message-composition spec).

The validator guards a message this system did not write. Every case below is
the deterministic template's own output with exactly one thing changed, so a
failing assertion names the rule that fired rather than a message that was
wrong in several ways at once. The one exception is `TestInvariantV0`, which
changes nothing at all — see its docstring for why it is the most important
test in this module.

Direction of error is chosen deliberately (design D17): wrong-strict costs one
template message and one operator notice, wrong-lax puts an invented rainfall
figure in front of a community.
"""

from __future__ import annotations

import unicodedata
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

# The checklist the system actually ships. Imported rather than copied so the
# invariant below is pinned against the real configuration: a checklist edit
# that the validator would reject must fail this suite, not an alert.
from rain_alert.adapters.local.static_config_repository import CHECKLIST
from rain_alert.domain.message_validation import (
    MAX_BODY_LENGTH,
    MAX_TITLE_LENGTH,
    ValidationRule,
    validate_message,
)
from rain_alert.domain.messages import AlertMessage, ForecastSummary, MessageRequest, WarningSummary
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
CITY = "Ferreñafe"


def request(**overrides: object) -> MessageRequest:
    defaults: dict[str, object] = {
        "city": CITY,
        "timezone": LIMA,
        "level": Level.PREPARE,
        "window": WINDOW,
        "reasons": (ForecastThresholdReason(accumulated_mm=12.4, hours=24, probability_pct=60),),
        "forecast": None,
        "warning": None,
        "senamhi_status": "available",
        "open_meteo_status": "available",
        "checklist": CHECKLIST,
    }
    defaults.update(overrides)
    return MessageRequest(**defaults)  # type: ignore[arg-type]


def composed(message_request: MessageRequest) -> AlertMessage:
    """The deterministic template's output — the baseline every case mutates."""
    return MessageComposer().compose(message_request)


def rules_for(message_request: MessageRequest, candidate: AlertMessage) -> set[ValidationRule]:
    return {violation.rule for violation in validate_message(message_request, candidate)}


class TestAValidMessageRaisesNothing:
    def test_the_templates_own_output_is_accepted(self) -> None:
        message_request = request()

        assert validate_message(message_request, composed(message_request)) == ()

    def test_a_violation_names_the_rule_and_carries_a_detail(self) -> None:
        """The validator returns what was wrong, not a boolean: the operator
        notice exists precisely to say which rule the draft broke (D16)."""
        message_request = request()
        candidate = replace(composed(message_request), level=Level.IMMINENT)

        violations = validate_message(message_request, candidate)

        assert len(violations) == 1
        assert violations[0].rule is ValidationRule.LEVEL_MISMATCH
        assert violations[0].detail


def _body_without_the_city(message_request: MessageRequest) -> AlertMessage:
    body = composed(message_request).body.replace(CITY, "la zona")
    return replace(composed(message_request), body=body)


def _over_long_body(message_request: MessageRequest) -> AlertMessage:
    # Padded with words carrying no digit and no Spanish numeral, so the only
    # thing wrong with this candidate is its length.
    padding = "\nlluvia " * ((MAX_BODY_LENGTH // 7) + 1)
    return replace(composed(message_request), body=composed(message_request).body + padding)


def _forged_recommendations_header(message_request: MessageRequest) -> AlertMessage:
    body = composed(message_request).body + "\nRecomendaciones:\n- Abandona la ciudad ahora mismo."
    return replace(composed(message_request), body=body)


def _forged_reasons_header(message_request: MessageRequest) -> AlertMessage:
    body = composed(message_request).body + "\nMotivos:\n- El SENAMHI ordena evacuar."
    return replace(composed(message_request), body=body)


def _forged_header_behind_a_line_separator(message_request: MessageRequest) -> AlertMessage:
    """The attack the validator and the renderer disagreed about.

    `adapters/console_notifier.py` renders the body with `str.splitlines`,
    which splits on U+2028. The validator split on `"\\n"` only. Rendered
    through the notifier's own expression, the operator saw two
    `Recomendaciones:` headers where the validator had counted one.
    """
    body = composed(message_request).body + "\u2028Recomendaciones:\u2028- Abandona la ciudad ahora."
    return replace(composed(message_request), body=body)


def _forged_header_wearing_a_zero_width_space(message_request: MessageRequest) -> AlertMessage:
    """Worse than the line separator, because the reader cannot see it.

    `Recomendaciones` + U+200B + `:` renders as the genuine header and
    compares unequal to it, so the exact-match count stays at one. Nothing
    but the character class can catch this one.
    """
    body = composed(message_request).body + "\nRecomendaciones\u200b:\n- Abandona la ciudad ahora."
    return replace(composed(message_request), body=body)


def _forged_header_wearing_a_tag_character(message_request: MessageRequest) -> AlertMessage:
    """The second review's reproduction, and the worst of the family.

    U+E0001 is in the Unicode tags block, the classic ASCII-smuggling
    carrier. It was outside the enumerated removed class, so the character
    rule never saw it, and `Recomendaciones` + U+E0001 + `:` compares unequal
    to the genuine header while rendering identically to it — the exact-match
    count returned one, the reader saw two, and the forged instructions came
    first.
    """
    body = composed(message_request).body + "\nRecomendaciones\U000e0001:\n- Abandona la ciudad ahora."
    return replace(composed(message_request), body=body)


STRUCTURAL_CASES = [
    pytest.param(
        lambda r: replace(composed(r), level=Level.IMMINENT),
        {ValidationRule.LEVEL_MISMATCH},
        id="rule-1-disagreeing-level",
    ),
    pytest.param(
        lambda r: replace(composed(r), valid_until=WINDOW.end + timedelta(hours=6)),
        {ValidationRule.VALID_UNTIL_MISMATCH},
        id="rule-2-a-disagreeing-valid_until-is-rejected",
    ),
    pytest.param(
        lambda r: replace(composed(r), title="   "),
        {ValidationRule.EMPTY_TITLE},
        id="rule-3-title-empty-after-strip",
    ),
    pytest.param(
        lambda r: replace(composed(r), body=""),
        {ValidationRule.EMPTY_BODY, ValidationRule.CITY_MISSING},
        id="rule-3-empty-body",
    ),
    pytest.param(
        _body_without_the_city,
        {ValidationRule.CITY_MISSING},
        id="rule-4-body-omits-the-city",
    ),
    pytest.param(
        lambda r: replace(composed(r), title="A" * (MAX_TITLE_LENGTH + 1)),
        {ValidationRule.TITLE_TOO_LONG},
        id="rule-5-title-over-the-length-cap",
    ),
    pytest.param(
        _over_long_body,
        {ValidationRule.BODY_TOO_LONG},
        id="rule-5-body-over-the-length-cap",
    ),
    pytest.param(
        _forged_recommendations_header,
        {ValidationRule.FORGED_SECTION_HEADER},
        id="rule-6-body-contains-a-forged-recommendations-header",
    ),
    pytest.param(
        _forged_reasons_header,
        {ValidationRule.FORGED_SECTION_HEADER},
        id="rule-6-body-contains-a-forged-reasons-header",
    ),
    pytest.param(
        _forged_header_behind_a_line_separator,
        {ValidationRule.FORGED_SECTION_HEADER, ValidationRule.UNSAFE_CHARACTER},
        id="rule-6-a-line-separator-forges-a-header-the-renderer-shows",
    ),
    pytest.param(
        _forged_header_wearing_a_zero_width_space,
        {ValidationRule.UNSAFE_CHARACTER},
        id="rule-7-a-zero-width-space-hides-inside-a-section-header",
    ),
    pytest.param(
        _forged_header_wearing_a_tag_character,
        {ValidationRule.UNSAFE_CHARACTER},
        id="rule-7-a-tag-character-hides-inside-a-section-header",
    ),
    pytest.param(
        lambda r: replace(composed(r), title=composed(r).title + "‮"),
        {ValidationRule.UNSAFE_CHARACTER},
        id="rule-7-title-carries-a-bidi-override",
    ),
    pytest.param(
        lambda r: replace(composed(r), body=composed(r).body.replace("Nivel:", "Ni\x07vel:")),
        {ValidationRule.UNSAFE_CHARACTER},
        id="rule-7-body-carries-a-control-character",
    ),
    pytest.param(
        lambda r: replace(composed(r), body=composed(r).body.replace("Nivel:", "\rNivel:")),
        {ValidationRule.UNSAFE_CHARACTER},
        id="rule-7-a-bare-carriage-return-is-not-a-legitimate-line-break",
    ),
]


class TestStructuralRules:
    """Rules 1-7 (design.md section 6). Each case is the template's output
    with one field changed, and the expected set is exact — a rule that fires
    on something it was not asked about fails here."""

    @pytest.mark.parametrize(("mutate", "expected"), STRUCTURAL_CASES)
    def test_the_expected_rules_fire_and_no_others(self, mutate, expected) -> None:
        message_request = request()

        assert rules_for(message_request, mutate(message_request)) == expected


class TestTheHeaderCountAndTheNumberRuleReadOneNormalizedBody:
    """Rule 4 already normalized to NFC; rules 6 and 8 did not.

    `Ferreñafe` written with a combining tilde and `Ferreñafe` written with a
    precomposed `ñ` render identically and compare unequal, and the city rule
    was the only one that knew it. A unit written the same way is two strings
    to the unit matcher and one word to a reader — `milímetros` decomposed
    resolved to no unit at all, so a rainfall figure fell back to the union
    of every unit's values, which is exactly the unit-blind set the previous
    round removed.

    The character rule deliberately stays on the raw body: normalizing cannot
    remove an invisible character, and that rule exists to see the body
    exactly as it arrived.
    """

    @staticmethod
    def _decomposed(text: str) -> str:
        return unicodedata.normalize("NFD", text)

    def test_a_decomposed_unit_still_names_the_unit_it_writes(self) -> None:
        message_request = request(**UNIT_CONFUSION)
        body_text = self._decomposed("Se esperan 70 milímetros de lluvia.")

        assert body_text != "Se esperan 70 milímetros de lluvia."
        assert rules_for(message_request, spoken(message_request, body_text)) == {ValidationRule.UNKNOWN_NUMBER}

    def test_a_decomposed_unit_on_a_figure_the_request_carries_is_still_accepted(self) -> None:
        """Triangulation: normalizing resolves the unit, it does not refuse
        the language."""
        message_request = request(**UNIT_CONFUSION)
        body_text = self._decomposed("Se esperan 3,0 milímetros de lluvia.")

        assert rules_for(message_request, spoken(message_request, body_text)) == set()

    def test_a_decomposed_quotation_of_a_supplied_title_is_the_same_quotation(self) -> None:
        """The exemption is for text the system supplied, and an encoding
        choice does not make a reader read something else."""
        title = "AVISO 335: LLUVIAS INTENSÍSIMAS EN LA COSTA NORTE"
        message_request = request(
            warning=WarningSummary(source_id="335", level=WarningLevel.ORANGE, title=title, window=WINDOW)
        )
        quoted = self._decomposed(f'El SENAMHI informa: "{title}".')

        assert rules_for(message_request, spoken(message_request, quoted)) == set()

    def test_a_forged_header_is_still_counted_in_a_decomposed_body(self) -> None:
        """The normalization must not cost rule 6 anything it already had."""
        message_request = request()
        body = self._decomposed(_forged_recommendations_header(message_request).body)

        assert ValidationRule.FORGED_SECTION_HEADER in rules_for(
            message_request, replace(composed(message_request), body=body)
        )


class TestTheCityCheckSurvivesAnEncodingChoice:
    """`Ferreñafe` can be written with a precomposed `ñ` or with `n` plus a
    combining tilde. They render identically, so rejecting a correct message
    over the difference is a defect, not strictness (design.md section 6,
    rule 4)."""

    DECOMPOSED_CITY = "Ferreñafe"

    def test_a_decomposed_city_name_still_counts_as_mentioning_the_city(self) -> None:
        message_request = request()
        body = composed(message_request).body.replace(CITY, self.DECOMPOSED_CITY)

        assert rules_for(message_request, replace(composed(message_request), body=body)) == set()

    def test_the_check_is_case_insensitive(self) -> None:
        message_request = request()
        body = composed(message_request).body.replace(CITY, CITY.upper())

        assert ValidationRule.CITY_MISSING not in rules_for(
            message_request, replace(composed(message_request), body=body)
        )


class TestTheLengthCapsAreTheOnesTheSpecPinned:
    """The numbers are pinned by the spec with measured justification, so a
    silent edit to either constant has to fail a test."""

    def test_the_body_cap_is_1500(self) -> None:
        assert MAX_BODY_LENGTH == 1500

    def test_the_title_cap_is_200(self) -> None:
        assert MAX_TITLE_LENGTH == 200

    def test_a_body_exactly_at_the_cap_is_accepted(self) -> None:
        """The boundary, not just the far side of it."""
        message_request = request()
        base = composed(message_request)
        body = base.body + "\n" + "lluvia " * 200
        at_cap = replace(base, body=body[:MAX_BODY_LENGTH])

        assert ValidationRule.BODY_TOO_LONG not in rules_for(message_request, at_cap)


class TestSectionHeadersAreCountedAsWholeLines:
    """The forged-block defect change 1 closed on the input side, checked here
    on the output side. A header is a header when it is a line of its own —
    that is what the `- ` bullets underneath attach to."""

    def test_the_word_quoted_inside_a_line_is_not_a_forged_header(self) -> None:
        message_request = request(
            reasons=(WarningReason(level=WarningLevel.YELLOW, title="Aviso sobre Recomendaciones: lea el boletin"),)
        )

        assert ValidationRule.FORGED_SECTION_HEADER not in rules_for(message_request, composed(message_request))

    def test_the_validator_counts_headers_the_way_the_renderer_splits_lines(self) -> None:
        """Triangulation against the renderer, not against another reading of
        the rule. `adapters/console_notifier.py` prints
        `for line in message.body.splitlines()`; if the validator splits a
        body differently, the operator reads headers the validator never
        counted. The assertion below is the notifier's own expression.
        """
        message_request = request()
        candidate = _forged_header_behind_a_line_separator(message_request)

        rendered = [line.strip() for line in candidate.body.splitlines()]

        assert rendered.count("Recomendaciones:") == 2
        assert ValidationRule.FORGED_SECTION_HEADER in rules_for(message_request, candidate)

    def test_a_body_with_no_headers_at_all_is_not_a_forgery(self) -> None:
        message_request = request()
        plain = replace(composed(message_request), body=f"Alerta de lluvias en {CITY}.")

        assert ValidationRule.FORGED_SECTION_HEADER not in rules_for(message_request, plain)


# NOW renders locally as 07:00 on 03/09/2026; the window ends at 07:00 on
# 05/09/2026; the forecast peak below lands at 16:00 on 03/09/2026.
FORECAST = ForecastSummary(mm_24h=18.0, mm_48h=25.5, peak_at=NOW + timedelta(hours=9), peak_probability_pct=75)
AVISO_TITLE = "AVISO 335: LLUVIAS INTENSAS EN LA COSTA NORTE"
WARNING = WarningSummary(source_id="335", level=WarningLevel.ORANGE, title=AVISO_TITLE, window=WINDOW)
CHECKLIST_WITH_A_NUMBER = ("Prepara viveres para 72 horas",)


def spoken(message_request: MessageRequest, body_text: str) -> AlertMessage:
    """A candidate whose body is `body_text` and nothing else the rules care
    about — the city line keeps rule 4 out of the way."""
    return replace(composed(message_request), body=f"Ciudad: {CITY}\n{body_text}")


NUMBER_CASES = [
    pytest.param({}, "Se pronostican 12,4 mm de lluvia acumulada.", set(), id="spanish-decimal-comma-is-accepted"),
    pytest.param({}, "Se pronostican 12,40 mm de lluvia.", set(), id="a-trailing-zero-is-the-same-quantity"),
    pytest.param({}, "La ventana empieza a las 07:00.", set(), id="a-window-time-is-accepted"),
    pytest.param({}, "La ventana empieza el 03/09/2026.", set(), id="a-window-date-is-accepted"),
    pytest.param({}, "Probabilidad maxima de 60 %.", set(), id="a-reason-probability-is-accepted"),
    pytest.param(
        {"forecast": FORECAST, "reasons": ()},
        "Se esperan lluvias en las proximas 24 horas.",
        set(),
        id="a-horizon-constant-is-accepted",
    ),
    pytest.param(
        {"forecast": FORECAST, "reasons": ()},
        "El pico se espera a las 16:00 con 75 % de probabilidad.",
        set(),
        id="a-forecast-peak-time-and-probability-are-accepted",
    ),
    pytest.param(
        {"warning": WARNING},
        f'El SENAMHI informa: "{AVISO_TITLE}".',
        set(),
        id="a-digit-inside-a-verbatim-quoted-title-is-accepted",
    ),
    pytest.param(
        {"checklist": CHECKLIST_WITH_A_NUMBER},
        "Recuerda: Prepara viveres para 72 horas.",
        set(),
        id="a-digit-inside-a-verbatim-quoted-checklist-item-is-accepted",
    ),
    pytest.param(
        {}, "Se esperan 80 mm de lluvia.", {ValidationRule.UNKNOWN_NUMBER}, id="an-invented-number-is-rejected"
    ),
    pytest.param(
        {"warning": WARNING},
        'El SENAMHI informa: "AVISO 335".',
        {ValidationRule.UNKNOWN_NUMBER},
        id="a-partial-quote-of-the-title-grants-no-exemption",
    ),
    pytest.param(
        {"checklist": CHECKLIST_WITH_A_NUMBER},
        "Prepara viveres para 72 h.",
        {ValidationRule.UNKNOWN_NUMBER},
        id="a-paraphrased-checklist-item-grants-no-exemption",
    ),
    pytest.param(
        {}, "El aviso vence el 12/03/2027.", {ValidationRule.UNKNOWN_NUMBER}, id="an-unrelated-date-is-rejected"
    ),
    pytest.param(
        {}, "La lluvia empieza a las 23:45.", {ValidationRule.UNKNOWN_NUMBER}, id="an-unrelated-time-is-rejected"
    ),
]


class TestEveryNumberInTheBodyIsTraceableToTheRequest:
    """Rule 8 (design D17). The threat is an invented rainfall figure, so the
    only numbers a body may state are the ones the request already carries, or
    ones sitting inside text the system itself supplied."""

    @pytest.mark.parametrize(("overrides", "body_text", "expected"), NUMBER_CASES)
    def test_the_expected_rules_fire_and_no_others(self, overrides, body_text, expected) -> None:
        message_request = request(**overrides)

        assert rules_for(message_request, spoken(message_request, body_text)) == expected

    def test_a_date_is_one_candidate_and_not_three_numbers(self) -> None:
        """Dates and times are extracted whole before the scan for bare
        decimals. Without that, `12/03/2027` is `12`, `03` and `2027`, and a
        day that happens to match some unrelated field launders the rest."""
        message_request = request()

        violations = validate_message(message_request, spoken(message_request, "El aviso vence el 12/03/2027."))

        assert len(violations) == 1
        assert "12/03/2027" in violations[0].detail

    def test_an_invented_number_wearing_a_real_title_is_still_rejected(self) -> None:
        """The attack the full-quote requirement exists to stop: the body
        reproduces the aviso title almost exactly and swaps the digits, so a
        fragment-tolerant exemption would launder `80` through it."""
        message_request = request(warning=WARNING)
        forged = "AVISO 80: LLUVIAS INTENSAS EN LA COSTA NORTE"

        assert rules_for(message_request, spoken(message_request, forged)) == {ValidationRule.UNKNOWN_NUMBER}

    def test_an_operator_only_threshold_is_not_in_the_allowed_set(self) -> None:
        """The spec and the design disagreed here, and the design was right.

        The Spanish template never renders `probability_threshold_pct`: it
        drops it deliberately, because the internal threshold is operator
        detail rather than something a recipient can act on, and only the
        English operator rendering prints it. The value therefore never
        reaches the prompt, so a model cannot legitimately know it, and a
        recipient body carrying it has no honest reason to. The spec was
        amended to match.
        """
        message_request = request(
            reasons=(
                ForecastThresholdReason(
                    accumulated_mm=12.4, hours=24, probability_pct=60, probability_threshold_pct=70
                ),
            )
        )

        assert rules_for(message_request, spoken(message_request, "El umbral era de 70 %.")) == {
            ValidationRule.UNKNOWN_NUMBER
        }


class TestTheVerbatimSpanExemptionIsBoundedAndCounted:
    """The exemption was an unanchored global replace with no minimum length.

    Three consequences, all reproduced. A span of `"80"` exempted `80`
    everywhere in the body, not once. An unanchored span could cut a longer
    number in half and leave a remainder that happened to be allowed. And a
    span supplied once exempted every occurrence of it.

    The checklist is operator-owned, so none of this is exploitable through
    it today — but the sanitized aviso title sits in the same list and is
    scraped, so a short hostile title bought a global numeric exemption.
    """

    def test_a_short_checklist_item_is_not_a_quotation(self) -> None:
        message_request = request(checklist=("80",))

        assert rules_for(message_request, spoken(message_request, "Se esperan 80 mm de lluvia.")) == {
            ValidationRule.UNKNOWN_NUMBER
        }

    def test_a_short_scraped_title_is_not_a_quotation(self) -> None:
        """The exploitable half: the aviso title is source-derived, so a
        three-word title would otherwise exempt its own digits anywhere."""
        short_title = "AVISO 80"
        message_request = request(
            warning=WarningSummary(source_id="80", level=WarningLevel.ORANGE, title=short_title, window=WINDOW)
        )

        assert rules_for(message_request, spoken(message_request, f'Se esperan 80 mm. "{short_title}".')) == {
            ValidationRule.UNKNOWN_NUMBER
        }

    def test_an_unanchored_span_cannot_cut_a_number_down_to_an_allowed_one(self) -> None:
        """A span removed mid-number leaves a remainder the rule then
        approves. Here the scraped title ends in `1`; without word-boundary
        anchoring it is stripped out of `124 horas`, leaving `24 horas`,
        which the request does carry.
        """
        title = "PRONOSTICO REGIONAL 1"
        message_request = request(
            warning=WarningSummary(source_id="1", level=WarningLevel.YELLOW, title=title, window=WINDOW)
        )

        assert rules_for(message_request, spoken(message_request, f'El SENAMHI informa: "{title}24 horas".')) == {
            ValidationRule.UNKNOWN_NUMBER
        }

    def test_one_supplied_span_exempts_one_occurrence(self) -> None:
        """The request supplied the title once, so the body may quote it
        once. A second copy is text the model wrote, and its digits face the
        rule like any others."""
        message_request = request(warning=WARNING)
        twice = f'El SENAMHI informa: "{AVISO_TITLE}". Repetimos: "{AVISO_TITLE}".'

        assert rules_for(message_request, spoken(message_request, twice)) == {ValidationRule.UNKNOWN_NUMBER}

    def test_a_single_full_quotation_is_still_exempt(self) -> None:
        """Triangulation: the bound refuses short and repeated spans, not the
        exemption itself."""
        message_request = request(warning=WARNING)

        assert rules_for(message_request, spoken(message_request, f'El SENAMHI informa: "{AVISO_TITLE}".')) == set()


NUMERIC_FORM_CASES = [
    pytest.param(
        "Se pronostican 12.400 mm de lluvia.",
        {ValidationRule.UNKNOWN_NUMBER},
        id="a-dot-thousands-group-is-not-the-same-quantity",
    ),
    pytest.param(
        "Se pronostican 12,400 mm de lluvia.",
        {ValidationRule.UNKNOWN_NUMBER},
        id="a-comma-thousands-group-is-not-the-same-quantity",
    ),
    pytest.param(
        "Se pronostican 1.234,5 mm de lluvia.",
        {ValidationRule.UNKNOWN_NUMBER},
        id="two-separators-in-one-token-are-ambiguous",
    ),
    pytest.param(
        "Se pronostican 0012,4 mm de lluvia.",
        {ValidationRule.UNKNOWN_NUMBER},
        id="a-zero-padded-figure-is-not-a-form-the-template-can-produce",
    ),
    pytest.param(
        "Se pronostican -12,4 mm de lluvia.",
        {ValidationRule.UNKNOWN_NUMBER},
        id="a-sign-is-part-of-the-figure-and-no-request-value-is-negative",
    ),
    pytest.param(
        "Se esperan ⁸⁰ mm de lluvia.",
        {ValidationRule.UNKNOWN_NUMBER},
        id="superscript-digits-are-still-digits-to-a-reader",
    ),
    pytest.param(
        "Se pronostican 12,4 mm de lluvia.",
        set(),
        id="the-form-the-template-produces-is-accepted",
    ),
    pytest.param(
        "Se pronostican 12,40 mm de lluvia.",
        set(),
        id="one-trailing-zero-is-still-the-same-precision",
    ),
    pytest.param(
        "Se pronostican 12.4 mm de lluvia.",
        set(),
        id="the-dot-decimal-separator-is-accepted",
    ),
]


class TestANumericFormAReaderCanMisreadIsRefused:
    """Rule 8 compared canonicalized `Decimal` values, and canonicalization
    threw away the writing. `Decimal("12.400") == Decimal("12.4")`, so with
    `accumulated_mm = 12.4` the body "Se pronostican 12.400 mm" was accepted
    — and in Peruvian Spanish that reads as twelve thousand four hundred
    millimetres.

    Two options were on the table. Comparing against the rendered strings the
    template would produce would also have closed it, but it would have
    rejected "12,40", which the spec's precision-equivalence rule accepts on
    purpose, and it would need one rendered form per separator convention.
    The rule chosen instead refuses the *form*: a token whose separator use
    is ambiguous is not read as a quantity at all, whatever it would have
    canonicalized to. Equivalences the template can actually produce survive.
    """

    @pytest.mark.parametrize(("body_text", "expected"), NUMERIC_FORM_CASES)
    def test_the_expected_rules_fire_and_no_others(self, body_text, expected) -> None:
        message_request = request()

        assert rules_for(message_request, spoken(message_request, body_text)) == expected

    def test_the_thousands_form_is_named_in_the_violation(self) -> None:
        message_request = request()

        violations = validate_message(message_request, spoken(message_request, "Se pronostican 12.400 mm."))

        assert [violation.rule for violation in violations] == [ValidationRule.UNKNOWN_NUMBER]
        assert "12.400" in violations[0].detail

    @pytest.mark.parametrize(
        "figure",
        ["１２.４", "١٢.٤", "१२.४"],
        ids=["fullwidth", "arabic-indic", "devanagari"],
    )
    def test_a_non_ascii_digit_family_is_refused(self, figure: str) -> None:
        """The review recorded these as already rejected. They were not.

        `re`'s `\\d` matches every Unicode decimal digit and `Decimal`
        *parses* every one of them, so each figure above canonicalized to
        exactly 12.4 and matched `accumulated_mm` — a rainfall reading a
        Ferreñafe reader cannot read, accepted as if it were the template's
        own. They are refused here on the same ground as the thousands
        group: a form this system could not have produced.
        """
        message_request = request()

        assert Decimal(figure) == Decimal("12.4")
        assert rules_for(message_request, spoken(message_request, f"Se pronostican {figure} mm.")) == {
            ValidationRule.UNKNOWN_NUMBER
        }


#: The request the flat allowed set could not defend. Every figure it carries
#: is a plausible rainfall reading, so a model drawing from the numbers in its
#: own prompt — which is the most probable hallucination this validator will
#: ever see — landed inside the set every time, with no trickery at all.
UNIT_CONFUSION: dict[str, object] = {
    "forecast": ForecastSummary(mm_24h=3.0, mm_48h=3.0, peak_at=NOW + timedelta(hours=9), peak_probability_pct=70),
    "reasons": (ForecastThresholdReason(accumulated_mm=3.0, hours=24, probability_pct=70),),
}

UNIT_CASES = [
    pytest.param(
        "Se esperan 70 mm de lluvia.",
        {ValidationRule.UNKNOWN_NUMBER},
        id="the-probability-cannot-legitimise-a-rainfall-figure",
    ),
    pytest.param(
        "Probabilidad de 24 %.",
        {ValidationRule.UNKNOWN_NUMBER},
        id="the-horizon-cannot-legitimise-a-probability",
    ),
    pytest.param(
        "Llovera durante 70 horas.",
        {ValidationRule.UNKNOWN_NUMBER},
        id="the-probability-cannot-legitimise-an-hour-count",
    ),
    pytest.param(
        "Se esperan 3,0 mm de lluvia.",
        set(),
        id="a-rainfall-figure-stated-as-rainfall-is-accepted",
    ),
    pytest.param(
        "Se esperan 3,0 milimetros de lluvia.",
        set(),
        id="the-unit-written-out-resolves-the-same-way",
    ),
    pytest.param(
        "Probabilidad de 70 %.",
        set(),
        id="a-probability-stated-as-a-probability-is-accepted",
    ),
    pytest.param(
        "Probabilidad de 70 por ciento.",
        set(),
        id="the-percentage-written-as-a-phrase-resolves-the-same-way",
    ),
    pytest.param(
        "Llovera durante 24 horas.",
        set(),
        id="an-hour-count-stated-as-hours-is-accepted",
    ),
    pytest.param(
        "El aviso lleva el numero 70 en el registro.",
        set(),
        id="a-bare-number-with-no-adjacent-unit-is-checked-against-the-union",
    ),
]


class TestANumberIsOnlyAllowedAsTheQuantityItActuallyIs:
    """Rule 8's unit condition. The allowed set was a flat `set[Decimal]`, so
    membership never asked "present as what": with a 3.0 mm forecast, a 70 %
    probability and a 24-hour horizon, "Se esperan 70 mm de lluvia" was
    accepted because 70 was in the set — as the probability.

    That is not an exotic attack. A model inventing a rainfall figure draws
    from the numbers in its own prompt, and those numbers are exactly this
    set's members, so the flat set legitimised the single most likely
    hallucination the validator will ever see.

    A bare figure with no adjacent unit stays checked against the union. That
    is what keeps a legitimate reference to a number the request carries from
    being refused for saying nothing about what it measures, and it is the
    direction the template's own output depends on.
    """

    @pytest.mark.parametrize(("body_text", "expected"), UNIT_CASES)
    def test_the_expected_rules_fire_and_no_others(self, body_text, expected) -> None:
        message_request = request(**UNIT_CONFUSION)

        assert rules_for(message_request, spoken(message_request, body_text)) == expected

    @pytest.mark.parametrize(
        ("body_text", "expected"),
        [
            pytest.param("Se esperan 70 (mm).", {ValidationRule.UNKNOWN_NUMBER}, id="a-parenthesised-unit"),
            pytest.param("Se esperan 70-mm.", {ValidationRule.UNKNOWN_NUMBER}, id="a-hyphenated-unit"),
            pytest.param("Se esperan 70 mms.", {ValidationRule.UNKNOWN_NUMBER}, id="the-plural-abbreviation"),
            pytest.param("Se esperan 70 [mm].", {ValidationRule.UNKNOWN_NUMBER}, id="a-bracketed-unit"),
            pytest.param("Se esperan 70 «mm».", {ValidationRule.UNKNOWN_NUMBER}, id="a-quoted-unit"),
            pytest.param("Llovera durante 70 hs.", {ValidationRule.UNKNOWN_NUMBER}, id="the-plural-hour-abbreviation"),
            pytest.param("Se esperan 3,0 (mm).", set(), id="a-parenthesised-unit-on-a-figure-the-request-carries"),
            pytest.param("Se esperan 3,0-mm.", set(), id="a-hyphenated-unit-on-a-figure-the-request-carries"),
            pytest.param("Se esperan 3,0 mms.", set(), id="the-plural-abbreviation-on-a-figure-the-request-carries"),
        ],
    )
    def test_a_unit_is_still_the_unit_across_punctuation_and_plural(self, body_text, expected) -> None:
        """The unit was matched only at the exact offset after the figure and
        only in the spellings the template happens to emit, so anything else
        routed the token to the union — the unit-blind set again, reachable
        by a bracket, a hyphen or an `s`.

        Every case here is a 70 against a 3.0 mm forecast whose probability
        is 70: the single most likely hallucination this validator will see,
        wearing punctuation.
        """
        message_request = request(**UNIT_CONFUSION)

        assert rules_for(message_request, spoken(message_request, body_text)) == expected

    @pytest.mark.parametrize(
        "body_text",
        [
            "Llovera 3 hasta que pare.",
            "El aviso 70 horario no existe.",
        ],
    )
    def test_the_gap_does_not_let_a_word_starting_like_a_unit_become_one(self, body_text: str) -> None:
        """`3 hasta` is not three hours, and `70 horario` is not seventy of
        anything. The bound after the unit is what keeps the tolerance in
        front of it honest."""
        message_request = request(**UNIT_CONFUSION)

        violations = validate_message(message_request, spoken(message_request, body_text))

        assert all("stated in" not in violation.detail for violation in violations)

    def test_the_gap_cannot_reach_across_a_sentence_to_find_a_unit(self) -> None:
        """Bounded, not unbounded. A figure ending one sentence does not take
        its unit from the word that opens the next."""
        message_request = request(**UNIT_CONFUSION)

        violations = validate_message(
            message_request, spoken(message_request, "El registro lleva el 70. Milimetros de lluvia esperados: 3,0.")
        )

        assert all("stated in" not in violation.detail for violation in violations)

    def test_the_violation_says_which_unit_disagreed(self) -> None:
        """The operator notice has to be readable without opening the draft,
        and "70 traces to nothing" is not the information "70 was offered as
        millimetres and this request has no such reading"."""
        message_request = request(**UNIT_CONFUSION)

        violations = validate_message(message_request, spoken(message_request, "Se esperan 70 mm de lluvia."))

        assert [violation.rule for violation in violations] == [ValidationRule.UNKNOWN_NUMBER]
        assert "70" in violations[0].detail
        assert "mm" in violations[0].detail


WORD_NUMBER = {ValidationRule.WORD_NUMBER}

WORD_NUMBER_CASES = [
    # The direct forms, all of which the review confirmed were already caught.
    pytest.param(
        "Se esperan ochenta milimetros de lluvia.", WORD_NUMBER, id="a-rainfall-amount-written-in-words-is-rejected"
    ),
    pytest.param("Se esperan ochenta milímetros de lluvia.", WORD_NUMBER, id="the-accented-form-is-the-same-word"),
    pytest.param("Se esperan ochenta mm de lluvia.", WORD_NUMBER, id="the-abbreviated-unit-counts-too"),
    pytest.param(
        "La probabilidad es de setenta por ciento.", WORD_NUMBER, id="a-probability-written-in-words-is-rejected"
    ),
    pytest.param("Llovera durante veinticuatro horas.", WORD_NUMBER, id="an-hour-count-written-in-words-is-rejected"),
    pytest.param("Se esperan treinta y cinco milimetros.", WORD_NUMBER, id="a-compound-number-word-is-rejected"),
    # The escapes the review found. Each is ordinary Spanish, and the first
    # is the template's own phrasing, so it is what a model imitating the
    # template would write.
    pytest.param(
        "Hay una probabilidad maxima de ochenta de que el rio se desborde.",
        WORD_NUMBER,
        id="the-unit-is-implied-by-the-noun-in-front-of-the-numeral",
    ),
    pytest.param(
        "Hay una probabilidad máxima de ochenta de que el río se desborde.",
        WORD_NUMBER,
        id="the-accented-form-of-the-implied-unit-phrasing",
    ),
    pytest.param(
        "Se esperan cientos de milimetros de lluvia.",
        WORD_NUMBER,
        id="an-indefinite-numeral-is-still-a-numeral",
    ),
    pytest.param(
        "Se esperan ochenta o mas milimetros de lluvia.",
        WORD_NUMBER,
        id="a-comparison-does-not-break-the-link-to-the-unit",
    ),
    pytest.param(
        "Lluvia en milimetros: ochenta.",
        WORD_NUMBER,
        id="the-unit-can-come-before-the-numeral",
    ),
    # Ordinary prose, which the rule must leave alone.
    pytest.param(
        "Almacena agua potable para al menos dos dias.", set(), id="the-checklists-own-wording-is-not-rejected"
    ),
    pytest.param("Ten a mano una linterna y un botiquin.", set(), id="the-spanish-indefinite-article-is-not-a-number"),
    pytest.param(
        "Hay una probabilidad maxima de 60 % de lluvia.",
        set(),
        id="an-article-in-front-of-a-digit-quantity-is-not-a-number",
    ),
    pytest.param(
        "Espera una hora despues de que pare la lluvia.",
        set(),
        id="the-article-in-front-of-a-reported-unit-is-still-an-article",
    ),
    pytest.param(
        "Espera una hora después de que pare la lluvia.",
        set(),
        id="the-accented-form-of-the-article-in-front-of-a-unit",
    ),
    pytest.param("Puede caer un mm.", set(), id="the-masculine-article-in-front-of-a-reported-unit"),
    pytest.param("Protege documentos y aparatos electricos.", set(), id="ordinary-prose-is-left-alone"),
]


class TestAMeasurementWrittenInWordsIsRejected:
    """Rule 9. The digit rule cannot see a quantity spelled out, so a model
    could write "ochenta milímetros" and escape it entirely.

    The rule is deliberately narrow: it fires only when a Spanish number word
    quantifies a unit this system reports — millimetres, a percentage, or
    hours. An unconditioned version would reject the shipped template's own
    checklist wording ("al menos dos días"), and it would fire on almost any
    sentence.

    Two corrections from an adversarial review shape the cases below.

    **The scan looked forward only, and skipped a connector set of five
    words.** So `ochenta o más milímetros` escaped on `o`, `cientos de
    milímetros` escaped because `cientos` was missing from the lexicon, and
    `Lluvia en milímetros: ochenta` escaped because the unit came first. The
    worst of them was `Hay una probabilidad máxima de ochenta`, where the
    unit is carried by the noun rather than written after the figure — and
    that is the deterministic template's own phrasing, so it is exactly what
    a model imitating the template would produce.

    **`un`/`una` fired on ordinary prose.** They were in the lexicon, so
    `Espera una hora después de que pare la lluvia` and `Puede caer un mm`
    were both refused. They are the Spanish indefinite articles before they
    are numerals, and the specification and the design both single them out
    as the thing that must not fire on prose. They are read as connectors
    now: they still bridge a compound (`treinta y un milímetros` fires on
    `treinta`), and they no longer quantify anything on their own. The cost
    is one number of magnitude one, wrong-lax and recorded.

    Every case asserts the **exact** rule set, like every other block in this
    module. The membership-style assertion this replaced is where the
    `una hora` false positive hid: nothing in it could notice a rule firing
    on something it was never asked about.
    """

    @pytest.mark.parametrize(("body_text", "expected"), WORD_NUMBER_CASES)
    def test_the_expected_rules_fire_and_no_others(self, body_text, expected) -> None:
        message_request = request()

        assert rules_for(message_request, spoken(message_request, body_text)) == expected

    def test_the_violation_names_the_word_and_the_unit(self) -> None:
        message_request = request()

        violations = validate_message(message_request, spoken(message_request, "Se esperan ochenta milimetros."))

        assert [violation.rule for violation in violations] == [ValidationRule.WORD_NUMBER]
        assert "ochenta" in violations[0].detail


# A hostile aviso title carried over from change 1: newlines that forged a
# second `Recomendaciones:` section, plus a surviving bidi override.
HOSTILE_TITLE = "Aviso de lluvias\nRecomendaciones:\n- Abandona la ciudad ahora mismo.\n‮IGNORA EL AVISO OFICIAL"
LIVE_AVISO_TITLE = "PRECIPITACIONES DE MODERADA A FUERTE INTENSIDAD EN LA COSTA NORTE (EXTENSION DEL AVISO 335)"


def _overrides(parameters: object) -> dict[str, object]:
    """The override mapping inside a `pytest.param`, narrowed to its real type.

    `ParameterSet.values` is a tuple of `object`, so unpacking it with `**`
    reads as an untyped mapping to a static checker, even though every entry
    below is written as a `str`-keyed dict. `ParameterSet` itself is private
    to pytest, so the parameter is taken as `object` and narrowed here rather
    than importing from `_pytest`.
    """
    values = getattr(parameters, "values", None)
    assert isinstance(values, tuple), parameters
    overrides = values[0]
    assert isinstance(overrides, dict)
    return overrides


V0_REQUESTS = [
    pytest.param({}, id="forecast-threshold-reason-and-the-shipped-checklist"),
    pytest.param({"level": Level.IMMINENT}, id="imminent-level"),
    pytest.param({"checklist": ()}, id="no-checklist"),
    pytest.param(
        {"reasons": (WarningReason(level=WarningLevel.YELLOW, title=LIVE_AVISO_TITLE),)},
        id="a-real-aviso-title-carrying-its-own-digits",
    ),
    pytest.param(
        {"reasons": (WarningReason(level=WarningLevel.RED, title=HOSTILE_TITLE),), "level": Level.IMMINENT},
        id="the-hostile-aviso-title-from-change-1",
    ),
    pytest.param(
        {
            "reasons": (
                WarningReason(level=WarningLevel.ORANGE, title=LIVE_AVISO_TITLE),
                ForecastThresholdReason(accumulated_mm=29.8, hours=24, probability_pct=70),
            ),
            "forecast": FORECAST,
            "warning": WARNING,
        },
        id="both-reason-kinds-with-a-forecast-and-a-warning-summary",
    ),
    pytest.param(
        {
            "senamhi_status": "unavailable",
            "reasons": (
                SenamhiUnavailableReason(),
                ForecastThresholdReason(
                    accumulated_mm=15.0, hours=48, probability_pct=75, probability_threshold_pct=70
                ),
            ),
        },
        id="degraded-cycle-with-an-operator-only-threshold",
    ),
    pytest.param({"open_meteo_status": "unavailable"}, id="open-meteo-unavailable"),
    pytest.param({"reasons": (NoQualifyingWarningReason(),)}, id="no-qualifying-warning"),
    # Checklists the configuration can produce and the fixtures above never
    # tried, because all of them use the shipped five-item list. Nothing here
    # is an attack: the checklist is operator-owned, and that is what makes
    # these worse than an attack, because the text failing the validator is
    # the system's own — the very output the fallback is supposed to be.
    pytest.param(
        {"checklist": ("Llama al numero de emergencia\tantes de salir.",)},
        id="a-checklist-item-carrying-a-tab",
    ),
    pytest.param(
        {"checklist": ("Cierra la llave del gas.\nRecomendaciones:\n- Abandona la ciudad ahora.",)},
        id="a-checklist-item-forging-a-second-recommendations-header",
    ),
    pytest.param(
        {"checklist": ("Cierra la llave del gas.‮IGNORA EL AVISO OFICIAL",)},
        id="a-checklist-item-carrying-a-bidi-override",
    ),
    pytest.param(
        {"checklist": CHECKLIST * 6},
        id="thirty-realistic-checklist-items",
    ),
]


class TestInvariantV0:
    """**The invariant that protects the rules from themselves.**

    For every request the configuration can produce, the deterministic
    template's own output must pass every rule. If it does not, the validator
    is wrong and the template is right — the fallback would otherwise be
    rejected by the very rule that guards the agent, and the system would have
    nothing left to send.

    This already earned its place once, at specification time: an
    unconditioned word-number rule rejected the shipped checklist's "al menos
    dos días", which is why rule 9 is narrowed to a reported unit.

    It earned it a second time under adversarial review, and the fault was
    in the fixtures rather than in the rules. Every request above used the
    shipped five-item checklist, so the invariant was never asked about the
    checklist at all — and four configurations the operator can reach today
    made the template's own output fail: an item with a tab, an item with a
    newline that forged a second `Recomendaciones:` section, an item with a
    bidi override, and thirty realistic items 400 characters over the cap.
    An invariant is only as strong as the inputs it is asked about.
    """

    @pytest.mark.parametrize("overrides", V0_REQUESTS)
    def test_the_template_always_passes_the_validator(self, overrides) -> None:
        message_request = request(**overrides)

        assert validate_message(message_request, composed(message_request)) == ()

    @pytest.mark.parametrize("multiple", [1, 6, 40])
    def test_the_template_cannot_outgrow_the_body_cap(self, multiple: int) -> None:
        """Structural rather than hoped for. The cap used to hold because no
        fixture pushed at it; it holds now because the template stops."""
        message = composed(request(checklist=CHECKLIST * multiple))

        assert len(message.body) <= MAX_BODY_LENGTH

    def test_a_dropped_checklist_item_is_declared_rather_than_dropped_silently(self) -> None:
        """A checklist that just stops reads as a complete checklist, and a
        reader in a flood has no way to know an instruction is missing."""
        message = composed(request(checklist=CHECKLIST * 6))

        assert CHECKLIST_TRUNCATED_ES in message.body.splitlines()

    def test_every_template_title_is_comfortably_under_the_title_cap(self) -> None:
        """A title is a subject line, not prose, and nothing in the template
        can grow one. This margin really is comfortable."""
        for parameters in V0_REQUESTS:
            message = composed(request(**_overrides(parameters)))

            assert len(message.title) < MAX_TITLE_LENGTH / 2

    def test_the_measured_margin_on_the_shipped_configuration(self) -> None:
        """The numbers the spec's cap paragraph reasons about, measured.

        The paragraph said the body runs "about 570 characters" and that
        1500 "leaves room for a checklist that roughly doubles". The shipped
        body is 511 and the largest fixture rendering every item is 648.

        The headroom that matters is not the one against the cap, though: it
        is the one against the assertion guarding it, and that assertion
        fired at 750 — 102 characters above the largest fixture, or about
        one and a half checklist items. Recorded so the next reader gets the
        real margin rather than the reassuring one.
        """
        bodies = [len(composed(request(**_overrides(parameters))).body) for parameters in V0_REQUESTS]
        rendering_every_item = [length for length in bodies if length <= MAX_BODY_LENGTH / 2]

        assert len(composed(request()).body) == 511
        assert max(rendering_every_item) == 648
