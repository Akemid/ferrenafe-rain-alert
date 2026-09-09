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
from rain_alert.domain.messages import AlertMessage, MessageRequest
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
