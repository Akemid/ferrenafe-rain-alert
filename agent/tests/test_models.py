"""The output contract this deployment unit promises (design.md 7.4, D22).

The contract is duplicated on purpose and cannot be shared as code: the
application keeps it as a frozen dataclass in `domain/messages.py`, this unit
keeps it as a Pydantic model, and neither tree is importable from the other
after deploy. What these tests pin is the half that lives here — that a draft
missing a field, or carrying a `valid_until` the application could not read,
never leaves the entrypoint at all.

`adapters/agent_composer.py::parse_candidate` is the reader on the other side.
Every rejection below mirrors a `return None` there: a missing field, a level
outside the enum, a `valid_until` that is not an instant, and a naive one —
which the application refuses because comparing it with the window's aware end
raises, and a validator that raises is a validator that sends nothing.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from models import CompositionOutput
from pydantic import ValidationError

COMPLETE = {
    "title": "Alerta de lluvias — Ferreñafe — riesgo inminente",
    "body": "Ciudad: Ferreñafe\nNivel: riesgo inminente",
    "level": "imminent",
    "valid_until": "2026-09-04T12:00:00+00:00",
}


def test_a_complete_payload_is_accepted() -> None:
    output = CompositionOutput.model_validate(COMPLETE)

    assert output.title == COMPLETE["title"]
    assert output.level == "imminent"
    assert output.valid_until == datetime(2026, 9, 4, 12, tzinfo=UTC)


@pytest.mark.parametrize("missing", ["title", "body", "level", "valid_until"])
def test_a_payload_missing_a_required_field_is_rejected(missing: str) -> None:
    payload = {key: value for key, value in COMPLETE.items() if key != missing}

    with pytest.raises(ValidationError):
        CompositionOutput.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("level", "catastrophic"),
        ("valid_until", "tomorrow"),
        ("valid_until", "2026-09-04T12:00:00"),
    ],
    ids=["level outside the enum", "valid_until is not an instant", "valid_until is naive"],
)
def test_a_value_the_application_could_not_read_is_rejected(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        CompositionOutput.model_validate({**COMPLETE, field: value})


def test_an_unrequested_field_is_rejected() -> None:
    """The output contract is closed. A model volunteering `recipients` or
    `send` would have it silently dropped otherwise, and the drop is the only
    evidence anyone would ever get that it tried."""
    with pytest.raises(ValidationError):
        CompositionOutput.model_validate({**COMPLETE, "send": True})


def test_the_dumped_output_is_json_serializable_and_carries_exactly_the_contract() -> None:
    """What the entrypoint returns has to survive the runtime's JSON encoding,
    so `valid_until` must come back out as a string rather than a `datetime`."""
    dumped = CompositionOutput.model_validate(COMPLETE).model_dump(mode="json")

    assert set(dumped) == set(COMPLETE)
    assert json.loads(json.dumps(dumped)) == dumped
    assert datetime.fromisoformat(dumped["valid_until"]) == datetime(2026, 9, 4, 12, tzinfo=UTC)
