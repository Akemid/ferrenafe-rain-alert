"""The AgentCore Runtime entrypoint (design.md 7.1, 7.4, D22; recent-alerts D5).

One structured-output call, **one read-only tool**, no side effect. The tool,
`get_recent_alerts` (`history.py`), can only query the alerts table and returns
facts computed in code; there is no tool that sends, notifies or records, and
the entrypoint's answer decides nothing — `RunAlertCycle` had already decided
to alert before this unit was invoked, and validates every word of the answer
before a recipient sees it. (The two tools design 7.3 originally sketched were
dropped by owner decision; the continuity tool reopened exactly one of those
questions, on the evidence that the alert store is now durable and shared.)

**A new `Agent` per request.** `Agent.__call__` appends to `agent.messages`, so
a module-level agent would leak one alert's prompt, tool result and answer into
the next on a warm runtime. Only the `MODEL` is built once, at import, so a
deploy that cannot construct it still fails at startup instead of on the first
alert of the season. The tool is closed over the request's history binding, so
the model chooses a count and never a place (`history.py`).

**The structured output is requested through the agent loop**:
`agent(prompt, structured_output_model=CompositionOutput)`. That path lets the
model call tools before the structured output (strands 1.55.1,
`event_loop.py:369-389`); the legacy `Agent.structured_output()` bypasses tool
execution and must not be used for composition. The prompt instructs the model
to call the tool first, but the loop does not enforce it: a model that emits the
structured output directly skips the tool, and the only loss is continuity.

**The prompt arrives built, and that is where the sanitizer boundary is
resolved.** The one string on the request an adversary controls is the scraped
aviso title. It passes `rain_alert.domain.sanitize.sanitize_source_text` on
the application's side of the wire, inside `adapters/agent_prompt.py`, before
it is ever encoded into the payload — so the sanitizer is used where it lives,
and this unit neither imports it across a deployment boundary that does not
exist at runtime nor keeps a second copy of the character class it would
inevitably drift from. What crosses the wire is already-sanitized, already-
fenced text. This module adds nothing to it and takes nothing away.

**Prompt wording is not enforcement, and this file is not where the message is
made safe.** The fixed rules live in `prompts.SYSTEM_PROMPT`, passed to the
`Agent` as its system prompt; the user turn carries the fenced data. Those rules ask the model to keep
every actionable sentence to the operator's own checklist, to write every
quantity in digits, and to treat the fenced block as data. A model can ignore
all three. What actually holds is `domain/message_validation.py`, on the other
side, and the fallback that sends the deterministic template whenever it
objects. One rule the owner has required is **not yet written there**: nothing
refuses an instruction the model originated, or a contact channel inside a
quoted span. That is a blocking condition on the pull request that first wires
this unit's output into a live cycle, and it is recorded here so that PR does
not read the prompt's own wording as the control it is not.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from history import AlertSource, DynamoAlertReader, HistoryBinding, build_tool, parse_binding
from models import CompositionOutput
from prompts import SYSTEM_PROMPT
from strands import Agent
from strands.models import BedrockModel, Model

#: Claude Haiku 4.5 on Bedrock, **resolved against the live account**
#: 2026-09-20. The previous value here was guessed from documentation and was
#: wrong in both halves; `docs/evidence/2026-09-20-preflight.md` carries the
#: commands and their output.
#:
#: **The profile prefix is mandatory, not regional.**
#: `aws bedrock get-foundation-model` reports
#: `inferenceTypesSupported: ['INFERENCE_PROFILE']` — there is no `ON_DEMAND`
#: for this model, so the bare foundation-model id cannot be invoked anywhere.
#: The earlier comment treated the prefix as a maybe that depended on the
#: account's region. It does not depend on anything.
#:
#: **The date and version suffix is real.** The earlier comment asserted that
#: a date-suffixed variant "is a different model or no model at all", and a
#: test pinned that absence. Both were backwards.
#:
#: `us.` over `global.`: at four invocations a day the ~10% saving `global.`
#: offers is about one cent a month, and the 10 s read deadline has room to
#: spare either way — so neither cost nor latency decides this. `us.` names
#: every region it can route to (`us-east-1`, `us-east-2`, `us-west-2`);
#: `global.` reports an unnamed destination, and a civil-protection system
#: should be able to say where a town's alert is processed. Revisit after the
#: first live run — this is one constant and one test.
MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

#: Low but not zero: the message is one short paragraph assembled from
#: pre-digested numbers, and the value of the agent over the template is
#: phrasing, not invention.
#:
#: **Only one of temperature and `top_p` may be set.** Bedrock refuses both
#: for this model — `ValidationException: temperature and top_p cannot both
#: be specified for this model` — and refuses at `ConverseStream`, not at
#: construction, so `BedrockModel` accepts the invalid pair without complaint
#: and the first sign is a 500 from a deployed runtime. Found on the first
#: live invocation, 2026-09-21; see `docs/evidence/`.
#:
#: Temperature is the one kept, because the paragraph above is an argument
#: about how much the model may invent and temperature states that directly.
#: `top_p` would say the same thing sideways, by truncating the sampling pool.
TEMPERATURE = 0.2

#: The one key the invocation payload must carry. The application's invoker
#: writes it, as `PROMPT_PAYLOAD_KEY`.
#:
#: Pinned against the application's side by `contracts/composer-fence.json`,
#: asserted from both suites (`agent/tests/test_fence_contract.py`).
PROMPT_KEY = "prompt"

#: Where the application binds the continuity context (partition, `as_of`, the
#: current level) — a sibling of the prompt, never inside the fenced data.
#: Pinned by `contracts/composer-fence.json` (schema 2).
HISTORY_KEY = "history"

app = BedrockAgentCoreApp()


class ComposerAgent(Protocol):
    """The single capability this entrypoint uses: be called with a prompt and
    an output model, and answer with something carrying `structured_output`.

    Narrower than `Agent` on purpose: it is the whole surface the offline tests
    need to fake, and writing it down keeps everything else about an `Agent` —
    history, callbacks, extra tools — out of this module's reach.
    """

    def __call__(self, prompt: str, *, structured_output_model: type[CompositionOutput]) -> Any: ...


def build_model() -> BedrockModel:
    """The model, configured. Builds no client and reaches no network."""
    return BedrockModel(model_id=MODEL_ID, temperature=TEMPERATURE)


#: Built once at import rather than per invocation, so a deploy that cannot
#: construct the model fails at startup instead of on the first alert of the
#: season. The *agent* is the per-request part (see the module docstring).
MODEL = build_model()

#: One lazy reader for the process. It holds a client and no request state, so
#: sharing it across invocations leaks nothing; the client is built on the first
#: read, which is why importing this module needs no region or credentials.
SOURCE = DynamoAlertReader()


def build_request_agent(
    binding: HistoryBinding | None,
    source: AlertSource | None = None,
    model: Model | None = None,
) -> Agent:
    """An agent for **one request**, with exactly one read-only tool.

    `tools=[...]` is written explicitly rather than left to the default. The
    property "this agent reaches no side effect" is the reason this whole
    capability was acceptable, and a property that depends on a default is one
    a later version of the library can change without anyone noticing.

    The tool is registered even when `binding` is `None`: it then returns the
    empty history without touching the store, so the agent's shape does not
    depend on whether the payload carried a usable binding.

    The fixed rules travel in the system channel (`prompts.SYSTEM_PROMPT`), so
    the caller controls only the data in the user turn.

    Args:
        binding: This request's history binding, or `None` for no history.
        source: Where history is read from. Defaults to the process-wide reader;
            tests pass a fake.
        model: Defaults to `MODEL`; tests pass a scripted one.
    """
    tool = build_tool(binding, source if source is not None else SOURCE)
    return Agent(model=model if model is not None else MODEL, tools=[tool], system_prompt=SYSTEM_PROMPT)


def compose(payload: object, build_agent: Callable[[HistoryBinding | None], ComposerAgent]) -> dict[str, Any]:
    """The prompt in `payload`, composed into the output contract.

    Args:
        payload: The invocation payload, **as the runtime produced it**. Typed
            `object` rather than `Mapping` because that is the truth at this
            edge: the runtime hands the entrypoint `await request.json()`
            unchanged, and `null`, a list and a bare string are all valid JSON
            documents. Narrowing it is this function's job, not its caller's.
            Must carry a non-blank string under `PROMPT_KEY`. May carry a
            history binding under `HISTORY_KEY`; an absent or invalid one means
            no history and never refuses the composition.
        build_agent: Builds the agent for this request from its binding. The
            real one is `build_request_agent`; the tests pass a factory that
            returns a spy, which is why this is a parameter.

    Returns:
        The validated output as a JSON-serializable dict — exactly the four
        keys `adapters/agent_composer.py::parse_candidate` reads.

    Raises:
        ValueError: If the payload is not a mapping, carries no usable prompt,
            or the run produced no structured output. Refused rather than
            passed on: an empty prompt is a paid invocation answering no
            question, and the application would then validate an answer it
            never asked for. The error travels back as an unsuccessful
            response, which the invoker turns into one more fallback.
    """
    if not isinstance(payload, Mapping):
        raise ValueError(f"payload must be a mapping carrying {PROMPT_KEY!r}")
    prompt = payload.get(PROMPT_KEY)
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError(f"payload must carry a non-blank string under {PROMPT_KEY!r}")
    agent = build_agent(parse_binding(payload.get(HISTORY_KEY)))
    result = agent(prompt, structured_output_model=CompositionOutput)
    output = result.structured_output
    if not isinstance(output, CompositionOutput):
        raise ValueError("the agent run returned no structured output")
    return output.model_dump(mode="json")


@app.entrypoint
def compose_message(payload: object) -> dict[str, Any]:
    """What AgentCore Runtime calls. Thin on purpose: everything worth testing
    is in `compose`, which takes its agent builder as an argument."""
    return compose(payload, build_request_agent)


if __name__ == "__main__":
    app.run()
