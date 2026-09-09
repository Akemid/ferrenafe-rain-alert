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

from dataclasses import replace
from datetime import UTC, datetime, timedelta

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
from rain_alert.domain.reasons import ForecastThresholdReason, WarningReason
from rain_alert.domain.template import MessageComposer
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

    def test_an_operator_only_threshold_is_in_the_allowed_set(self) -> None:
        """The spec lists `probability_threshold_pct` among the allowed values
        and the spec is the authority here. Design.md section 6 rule 8 argues
        the opposite — it wants the value excluded because it is operator-only
        and never enters the prompt. Recorded as a contradiction in
        apply-progress rather than resolved silently in either direction.
        """
        message_request = request(
            reasons=(
                ForecastThresholdReason(
                    accumulated_mm=12.4, hours=24, probability_pct=60, probability_threshold_pct=70
                ),
            )
        )

        assert rules_for(message_request, spoken(message_request, "El umbral era de 70 %.")) == set()


WORD_NUMBER_CASES = [
    pytest.param("Se esperan ochenta milimetros de lluvia.", True, id="a-rainfall-amount-written-in-words-is-rejected"),
    pytest.param("Se esperan ochenta milímetros de lluvia.", True, id="the-accented-form-is-the-same-word"),
    pytest.param("Se esperan ochenta mm de lluvia.", True, id="the-abbreviated-unit-counts-too"),
    pytest.param("La probabilidad es de setenta por ciento.", True, id="a-probability-written-in-words-is-rejected"),
    pytest.param("Llovera durante veinticuatro horas.", True, id="an-hour-count-written-in-words-is-rejected"),
    pytest.param("Se esperan treinta y cinco milimetros.", True, id="a-compound-number-word-is-rejected"),
    pytest.param(
        "Almacena agua potable para al menos dos dias.", False, id="the-checklists-own-wording-is-not-rejected"
    ),
    pytest.param("Ten a mano una linterna y un botiquin.", False, id="the-spanish-indefinite-article-is-not-a-number"),
    pytest.param(
        "Hay una probabilidad maxima de 60 % de lluvia.",
        False,
        id="an-article-in-front-of-a-digit-quantity-is-not-a-number",
    ),
    pytest.param("Protege documentos y aparatos electricos.", False, id="ordinary-prose-is-left-alone"),
]


class TestAMeasurementWrittenInWordsIsRejected:
    """Rule 9. The digit rule cannot see a quantity spelled out, so a model
    could write "ochenta milímetros" and escape it entirely.

    The rule is deliberately narrow: it fires only when a Spanish number word
    directly quantifies a unit this system reports — millimetres, a
    percentage, or hours. An unconditioned version would reject the shipped
    template's own checklist wording ("al menos dos días", "un botiquín",
    "una linterna"), and `un`/`una` are the ordinary indefinite articles, so
    it would fire on almost any sentence.
    """

    @pytest.mark.parametrize(("body_text", "rejected"), WORD_NUMBER_CASES)
    def test_only_a_word_number_quantifying_a_reported_unit_is_rejected(self, body_text, rejected) -> None:
        message_request = request()

        fired = ValidationRule.WORD_NUMBER in rules_for(message_request, spoken(message_request, body_text))

        assert fired is rejected

    def test_the_violation_names_the_word_and_the_unit(self) -> None:
        message_request = request()

        violations = validate_message(message_request, spoken(message_request, "Se esperan ochenta milimetros."))

        assert [violation.rule for violation in violations] == [ValidationRule.WORD_NUMBER]
        assert "ochenta" in violations[0].detail
