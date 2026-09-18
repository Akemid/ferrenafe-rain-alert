"""The AgentCore entrypoint: one structured call, no tools, no side effect.

**Nothing here calls a model or needs a credential.** The model is faked by a
hand-written spy exposing the single method the entrypoint uses,
`structured_output(output_model, prompt)` — no `unittest.mock`, per the
standing rule in `tests/support/fakes.py`. The two tests that touch the real
`BedrockModel` and `Agent` only *construct* them, which reaches no network:
`BedrockModel` builds no client until it is invoked, so the assertions below
hold with every AWS environment variable unset.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

import app as app_module
import pytest
from app import AGENT, MODEL_ID, PROMPT_KEY, build_model, compose, compose_message
from models import CompositionOutput

DRAFT = CompositionOutput(
    title="Alerta de lluvias — Ferreñafe — riesgo inminente",
    body="Ciudad: Ferreñafe\nLas lluvias son inminentes.",
    level="imminent",
    valid_until=datetime(2026, 9, 4, 12, tzinfo=UTC),
)


class FakeStructuredModel:
    """Returns a scripted `CompositionOutput` and records what it was asked."""

    def __init__(self, output: CompositionOutput) -> None:
        self._output = output
        self.calls: list[tuple[type[CompositionOutput], str]] = []

    def structured_output(self, output_model: type[CompositionOutput], prompt: str) -> CompositionOutput:
        self.calls.append((output_model, prompt))
        return self._output


def test_the_entrypoint_returns_the_output_contract_as_a_json_serializable_dict() -> None:
    """What crosses back is a dict the runtime can encode, not a Pydantic
    object and not prose. `parse_candidate` on the other side reads exactly
    these four keys."""
    result = compose({PROMPT_KEY: "<prompt>"}, FakeStructuredModel(DRAFT))

    assert set(result) == {"title", "body", "level", "valid_until"}
    assert json.loads(json.dumps(result)) == result
    assert result["valid_until"] == "2026-09-04T12:00:00Z"


def test_the_prompt_reaches_the_model_unchanged_and_is_asked_for_the_output_contract() -> None:
    """The prompt is built on the application's side of the wire and arrives
    here already sanitized and already fenced. This unit adds nothing to it and
    takes nothing away: a second opinion about the wording here is a second
    place the two could disagree."""
    model = FakeStructuredModel(DRAFT)

    compose({PROMPT_KEY: "Escribe el mensaje.\n<datos>{}</datos>"}, model)

    assert model.calls == [(CompositionOutput, "Escribe el mensaje.\n<datos>{}</datos>")]


@pytest.mark.parametrize(
    "payload",
    [{}, {"prompt": ""}, {"prompt": "   "}, {"prompt": None}, {"prompt": ["a"]}, {"promt": "typo"}],
    ids=["empty payload", "empty prompt", "blank prompt", "null prompt", "list prompt", "misspelled key"],
)
def test_a_payload_carrying_no_usable_prompt_is_refused_before_the_model_is_called(payload: dict[str, Any]) -> None:
    """Refused rather than passed on. An empty prompt is a paid invocation
    returning whatever the model invents unprompted, and the application would
    then validate an answer to no question."""
    model = FakeStructuredModel(DRAFT)

    with pytest.raises(ValueError, match=PROMPT_KEY):
        compose(payload, model)

    assert model.calls == []


@pytest.mark.parametrize(
    "payload",
    [None, [], ["prompt"], "prompt", 123, True],
    ids=["null body", "empty list", "list body", "string body", "number body", "boolean body"],
)
def test_a_payload_that_is_not_a_mapping_at_all_is_refused_the_same_way(payload: object) -> None:
    """The runtime hands the entrypoint `await request.json()` **unchanged**
    (`bedrock_agentcore/runtime/app.py`), and every JSON document is valid
    there — `null`, a list, a bare string, a number. Nothing on the wire
    promises a mapping, which is why `compose` takes `object` and narrows.

    Without the guard these raise `AttributeError: 'NoneType' object has no
    attribute 'get'` — a message about Python internals rather than about the
    payload, reaching the invoker as the body of a 500. The runtime's outer
    handler turned either one into a 500, so the failure was always closed;
    what this pins is that every bad shape earns the *same* refusal, and that
    the annotation stops being a promise no caller can keep.
    """
    model = FakeStructuredModel(DRAFT)

    with pytest.raises(ValueError, match=PROMPT_KEY):
        compose(payload, model)

    assert model.calls == []


def test_the_entrypoint_composes_with_the_agent_built_once_at_import(monkeypatch: pytest.MonkeyPatch) -> None:
    """`AGENT` exists so a deploy that cannot construct the model fails at
    startup rather than on the first alert of the season. That property only
    holds if the entrypoint actually *uses* it: rewriting `compose_message` to
    call `build_agent()` per invocation keeps every other test green and moves
    the failure back to the first alert, which is exactly what the constant was
    written to prevent.
    """
    spy = FakeStructuredModel(DRAFT)
    monkeypatch.setattr(app_module, "AGENT", spy)

    result = compose_message({PROMPT_KEY: "<prompt>"})

    assert spy.calls == [(CompositionOutput, "<prompt>")]
    assert result == DRAFT.model_dump(mode="json")


def test_the_agent_has_no_tool_registered() -> None:
    """Non-capability 5: the agent reaches no side effect. Both tools the
    design sketched were dropped by owner decision, so this is a single
    structured-output call and zero registered tools is the mechanism — not a
    sentence in the prompt."""
    assert AGENT.tool_names == []


def test_the_model_is_the_configured_haiku_with_the_configured_sampling() -> None:
    configured = build_model().get_config()

    assert configured["model_id"] == MODEL_ID
    assert configured["temperature"] == pytest.approx(0.2)
    assert configured["top_p"] == pytest.approx(0.9)


def test_the_model_id_is_a_bedrock_id_and_carries_no_date_suffix() -> None:
    """Resolved against live AWS documentation on 2026-09-15, and pinned as a
    shape because both plausible wrong guesses have one: a first-party id with
    no `anthropic.` prefix, and a date-suffixed variant that does not exist for
    this model. A regional inference-profile prefix (`us.`) may still be
    required depending on the account's region; see the constant's comment."""
    assert MODEL_ID.startswith("anthropic.")
    assert re.search(r"-\d{6,8}$", MODEL_ID) is None


def test_the_entrypoint_is_the_function_the_runtime_calls() -> None:
    """`@app.entrypoint` returns the function itself, so the decorated name is
    callable here. That is what makes the contract above testable at all."""
    assert callable(compose_message)
