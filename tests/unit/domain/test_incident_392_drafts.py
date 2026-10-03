"""Characterization of the 2026-10 incident drafts against the UNCHANGED validator.

The agent was invoked on aviso 392 (window 2026-10-02T05:00Z to
2026-10-05T05:00Z, orange) and its drafts were rejected, so the community got
the template. Each shape below is a draft the model really wrote. These tests
pass at once: they pin what `validate_message` does TODAY, so the change that
moves the fixed rules into the system prompt (agent-system-prompt) can be held
to "the validator never moves; the prompt learns its shape".

If one of these ever fails, the validator changed. That is a decision for the
owner, not a test to fix.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from rain_alert.adapters.local.static_config_repository import CHECKLIST
from rain_alert.domain.message_validation import ValidationRule, validate_message
from rain_alert.domain.messages import AlertMessage, MessageRequest, WarningSummary
from rain_alert.domain.reasons import WarningReason
from rain_alert.domain.template import MessageComposer
from rain_alert.domain.values import Level, TimeWindow, WarningLevel

CITY = "Ferreñafe"
TITLE = "AVISO METEOROLOGICO DE LLUVIAS INTENSAS"
WINDOW = TimeWindow(
    start=datetime(2026, 10, 2, 5, tzinfo=UTC),
    end=datetime(2026, 10, 5, 5, tzinfo=UTC),
)


def _incident_request() -> MessageRequest:
    return MessageRequest(
        city=CITY,
        timezone="America/Lima",
        level=Level.PREPARE,
        window=WINDOW,
        reasons=(WarningReason(level=WarningLevel.ORANGE, title=TITLE),),
        forecast=None,
        warning=WarningSummary(source_id="392", level=WarningLevel.ORANGE, title=TITLE, window=WINDOW),
        senamhi_status="available",
        open_meteo_status="available",
        checklist=CHECKLIST,
    )


def _draft(body: str, *, title: str | None = None) -> AlertMessage:
    """A candidate with the request's own level and `valid_until`, so only the
    rule under test can fire."""
    base = MessageComposer().compose(_incident_request())
    return replace(base, title=title if title is not None else base.title, body=body)


def _rules(candidate: AlertMessage) -> set[ValidationRule]:
    return {violation.rule for violation in validate_message(_incident_request(), candidate)}


def _checklist_block() -> str:
    return "Recomendaciones:\n" + "\n".join(f"- {item}" for item in CHECKLIST)


def test_the_template_baseline_is_accepted() -> None:
    """Without this, a rejection below might be the fixture's fault."""
    assert _rules(MessageComposer().compose(_incident_request())) == set()


def test_a_period_written_as_prose_days_is_an_unknown_number() -> None:
    body = f"Ferreñafe tiene un aviso oficial naranja de lluvias intensas entre el 2 y el 5 de octubre.\n\n{_checklist_block()}"

    assert _rules(_draft(body)) == {ValidationRule.UNKNOWN_NUMBER}


def test_a_validity_end_written_with_a_month_name_and_a_year_is_an_unknown_number() -> None:
    body = f"Ferreñafe: aviso naranja vigente hasta el 5 de octubre de 2026.\n\n{_checklist_block()}"

    assert _rules(_draft(body)) == {ValidationRule.UNKNOWN_NUMBER}


def test_the_city_only_in_the_title_is_a_missing_city() -> None:
    body = f"Hay un aviso oficial naranja de lluvias intensas en tu zona.\n\n{_checklist_block()}"

    assert _rules(_draft(body, title=f"Alerta de lluvias — {CITY} — prepárate")) == {ValidationRule.CITY_MISSING}


def test_a_body_obeying_the_fixed_rules_is_accepted() -> None:
    """City in the body, dates copied as DD/MM/AAAA HH:MM, checklist verbatim."""
    body = (
        f"Ciudad: {CITY}\n"
        "Ventana: del 02/10/2026 00:00 al 05/10/2026 00:00.\n"
        f"Aviso oficial del SENAMHI, nivel naranja: {TITLE}.\n\n"
        f"{_checklist_block()}"
    )

    assert _rules(_draft(body)) == set()
