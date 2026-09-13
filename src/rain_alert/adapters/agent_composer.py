"""The agent-backed composer: one call, validated, with the template beneath
it (design.md D15; agent-message-composition spec).

**The property this module owes, and the shape that makes it checkable.** The
alert never depends on the agent. The deterministic template's message is
computed on the first line of `compose`, unconditionally, before anything can
fail; every `return` afterwards yields either the agent's validated text or
that exact object. No path constructs a message late, and none can be reached
with `fallback` unbound. That makes "there is always a message" a property of
the function's *shape* rather than of its paths — something a reader can
check by looking, and a reviewer can check by deleting the `except` and
watching twelve tests turn red. The cost is one pure string join per sending
cycle, paid also on the accepted path.

**Telling the operator is best-effort, and structurally so.** `_fell_back` is
called from outside the `try` and from inside two `except` clauses, so
anything it raises leaves `compose` by exception — and `RunAlertCycle` has no
`try` by design. Its clock call and its notice are therefore wrapped in
`suppress`, and `return fallback` is the last statement, reachable past
nothing that can raise. Row 13 of the fault table pins it.

**The two faults this shape cannot absorb, stated so nobody has to discover
them.**

1. **A hang.** Every exception the seam can raise is caught, but an
   invocation that simply never returns has no branch here, and the cycle
   waits with the community. Nothing in this module can bound it; the
   deadline is the invoker's obligation alone (D19, and
   `AgentInvoker.invoke` says so), which is why an implementation that never
   raises is not thereby correct.
2. **A `BaseException`.** `except Exception` is deliberate and does not catch
   one. `KeyboardInterrupt` and `SystemExit` should travel — a cycle being
   shut down is not a composer fault — but it means an asynchronous
   cancellation (`asyncio.CancelledError` since 3.8, or any framework that
   raises through a worker) escapes `compose` and aborts the cycle. Nothing
   in this codebase raises one today, and widening the catch would swallow
   shutdown, so this is recorded rather than repaired.

**Where the `except` lives.** Here, never in `RunAlertCycle`. The use case
contains zero `try` blocks: change 1 made failure travel as data
(`SourceResult`). Failure cannot be data here, because the port returns
`AlertMessage`, so the adapter absorbs it — exactly as `adapters/http.py`
states its own first rule, "no `httpx` exception ever escapes". If the use
case caught instead, every future composer would teach the use case its own
failure modes.

**The fallback value is never validated.** Validating it would create a path
where a validator bug leaves the system with no message at all. What covers
it instead is invariant V0 — the deterministic template's output passes every
rule, for every request the configuration can produce — which
`tests/unit/domain/test_message_validation.py` pins.

**One deviation from D15's sketch, deliberate.** D15 draws the `try` around
the invocation alone, with the parse and the validation after it. Here it
spans the prompt build, the invocation, the parse *and* the validation. PR 1
recorded that `validate_message` raises on an unusable timezone and that
"the fallback adapter must not assume this function cannot raise"; a
guarantee that holds only while three downstream functions stay total is not
a guarantee. It is still one `try`, which is all D15's rationale actually
asks for, and the `if violations:` branch stays outside it so the two
mutations remain separable.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import replace
from datetime import datetime
from enum import StrEnum

from rain_alert.adapters.agent_invoker import AgentInvocationError, AgentInvoker
from rain_alert.domain.message_validation import Violation, validate_message
from rain_alert.domain.messages import AlertMessage, MessageRequest, OperatorNotice
from rain_alert.domain.sanitize import sanitize_source_text
from rain_alert.domain.template import MessageComposer as TemplateComposer
from rain_alert.domain.values import ComposerName, Level, NoticeKind, UnavailableReason
from rain_alert.ports import MessageComposer, Notifier

#: The output contract (design 7.4). A draft missing any of these, or
#: carrying one in a shape that cannot be read, is not a candidate at all.
REQUIRED_OUTPUT_FIELDS = ("title", "body", "level", "valid_until")


class FallbackReason(StrEnum):
    """Why the template was sent instead of the agent's draft.

    The four transport members reuse `UnavailableReason`'s own spellings, so
    an invocation error maps onto one without a translation table. The
    remaining two name faults that have no transport equivalent.
    """

    TIMEOUT = "timeout"
    TRANSPORT_ERROR = "transport_error"
    BAD_STATUS = "bad_status"
    MALFORMED_PAYLOAD = "malformed_payload"
    VALIDATION_FAILED = "validation_failed"
    UNEXPECTED_ERROR = "unexpected_error"


def fallback_reason_for(reason: UnavailableReason) -> FallbackReason:
    """`reason` as a `FallbackReason`, total by construction.

    `FallbackReason(exc.reason)` would be shorter and would raise
    `ValueError` **inside the `except` clause** for any reason outside the
    four transport ones — an exception escaping the handler whose entire job
    is to absorb exceptions. `UnavailableReason` already carries seven
    members and change 3 may add more.
    """
    try:
        return FallbackReason(reason.value)
    except ValueError:
        return FallbackReason.UNEXPECTED_ERROR


def parse_candidate(draft: object) -> AlertMessage | None:
    """`draft` as an `AlertMessage`, or `None` when its shape is unusable.

    This raises nothing on a bad shape, and returns `None` instead, so that a
    malformed payload is an *answer* rather than an exception the caller must
    also be ready for. It takes no `MessageRequest` on purpose, and that is
    worth stating because D15's sketch passes one: a parser that could fill a
    missing field from the request would let the agent omit precisely the
    value the validator then checks against that same request, and the check
    would pass by construction.

    **`draft` is typed `object`, and the mapping check is the first thing
    here, because the annotation is not the guard it looks like.** The
    parameter arrives from `AgentInvoker.invoke`, whose declared return type a
    real SDK-backed implementation can simply fail to honour; a decoded JSON
    body is as easily a list, a null, a bare string or a number. The
    required-field check does not catch any of those: `field not in draft` is
    membership for a list and *substring* for a string, so a list of the four
    field names and a string containing them both pass it and fail later with
    a `TypeError`. The broad handler in `compose` absorbs that, so the alert
    survived either way — but the operator was told `unexpected_error`, which
    sends them looking for a bug in this codebase when the truth is that the
    model returned the wrong shape. The check is here to **name** the fault,
    for the same reason the required-field check is.

    `valid_until` must parse to a timezone-aware instant. A naive one would
    raise `TypeError` when the validator compares it with the window's aware
    end, and a validator that raises is a validator that sends nothing.
    """
    if not isinstance(draft, Mapping):
        return None
    if any(field not in draft for field in REQUIRED_OUTPUT_FIELDS):
        return None

    title, body = draft["title"], draft["body"]
    if not isinstance(title, str) or not isinstance(body, str):
        return None

    try:
        level = Level(draft["level"])
    except ValueError:
        return None

    valid_until = draft["valid_until"]
    if not isinstance(valid_until, str):
        return None
    try:
        moment = datetime.fromisoformat(valid_until)
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None

    return AlertMessage(title=title, body=body, level=level, valid_until=moment)


class AgentBackedComposer:
    """`MessageComposer` that asks an agent and keeps the template beneath it.

    Args:
        invoker: The invocation seam (D18). One call, no retry.
        build_prompt: Turns the request into the prompt text. Injected rather
            than imported: the prompt is a later slice, and nothing about the
            fallback behaviour depends on what it says. Anything it raises is
            absorbed like any other fault.
        notifier: Where the fallback notice goes. The adapter sends it, not
            the use case, because only the adapter knows *why* it fell back
            (D21) — and a notice reading "fallback happened", with no fault
            and no violation list, is a slower version of the silent
            degradation the notice exists to prevent.
        now: The cycle clock (D7), for the notice's timestamp.
        template: The fallback. Defaults to the real deterministic template,
            which is the only thing the spec allows a fallback to be;
            injectable so a test can observe when it is called.
    """

    def __init__(
        self,
        *,
        invoker: AgentInvoker,
        build_prompt: Callable[[MessageRequest], str],
        notifier: Notifier,
        now: Callable[[], datetime],
        template: MessageComposer | None = None,
    ) -> None:
        self._invoker = invoker
        self._build_prompt = build_prompt
        self._notifier = notifier
        self._now = now
        self._template: MessageComposer = TemplateComposer() if template is None else template

    def compose(self, request: MessageRequest) -> AlertMessage:
        fallback = self._template.compose(request)
        try:
            draft = self._invoker.invoke(self._build_prompt(request))
            candidate = parse_candidate(draft)
            if candidate is None:
                return self._fell_back(FallbackReason.MALFORMED_PAYLOAD, "draft is missing or malformed", fallback)
            violations = validate_message(request, candidate)
        except AgentInvocationError as exc:
            return self._fell_back(fallback_reason_for(exc.reason), exc.detail, fallback)
        except Exception as exc:  # noqa: BLE001 — an SDK bug must degrade the cycle, not abort it
            return self._fell_back(FallbackReason.UNEXPECTED_ERROR, _unexpected_detail(exc), fallback)

        if violations:
            return self._fell_back(FallbackReason.VALIDATION_FAILED, _rendered(violations), fallback)
        return replace(candidate, composed_by=ComposerName.AGENT)

    def _fell_back(self, reason: FallbackReason, detail: str, fallback: AlertMessage) -> AlertMessage:
        """Tell the operator what went wrong — best effort — and return the template's message.

        The notice goes out *before* `RunAlertCycle` calls `send_alert`,
        because composition happens first. That ordering is right: the
        operator learns the message they are about to relay came from the
        template, rather than learning it afterwards.

        **The body states a substitution, not a delivery.** It used to read
        that the template's message "was sent as of" the timestamp. At that
        moment nothing had been sent, and it may never be: the send can fail
        and the recipient list can be empty. In a life-safety system the
        operator's log is evidence, and a line asserting a delivery that did
        not happen is the wrong kind of wrong.

        **Telling the operator is best-effort, and the `suppress` is what
        makes it structurally so.** Both operations here can raise: the
        injected clock, and the notifier. Three of this method's four call
        sites are outside the `try` in `compose` or inside one of its
        `except` clauses, so before the guard either raise left `compose` by
        exception — and `RunAlertCycle` has no `try` by design (change 1 made
        failure travel as data), so the whole cycle died. Reproduced with a
        notifier whose operator channel raises while its community channel
        works: the alert was lost, the malformed-payload branch notified
        *twice* because the broad handler caught its own failed notice, and
        the surviving notice named `unexpected_error` instead of the real
        fault. One cause, three consequences, all three of them the opposite
        of what this adapter exists to guarantee. `ConsoleNotifier` writes to
        a stream today — a closed pipe, a full disk, a dead supervisor — and
        change 3 makes it a network notifier where transient failure is
        routine. Row 13 of the fault table is the mutant that kills this
        guard if anyone removes it.

        `return fallback` is deliberately the last statement, reachable past
        nothing that can raise: the community's message must not depend on
        the operator's.

        `sources` is empty — no data source failed. `ConsoleNotifier` will
        print `sources: ` with nothing after it; the subject and body carry
        the information.
        """
        with suppress(Exception):
            now = self._now()
            self._notifier.send_operator_notice(
                OperatorNotice(
                    kind=NoticeKind.AGENT_FALLBACK_USED,
                    subject=f"Agent composer fell back to the template: {reason.value}",
                    body=(
                        f"The deterministic template's message is being sent in place of the agent's draft, "
                        f"as of {now.isoformat()}. Fault: {reason.value}. Detail: {detail}"
                    ),
                    sources=frozenset(),
                    occurred_at=now,
                )
            )
        return fallback


def _rendered(violations: tuple[Violation, ...]) -> str:
    return "; ".join(f"{violation.rule.value}: {violation.detail}" for violation in violations)


def _unexpected_detail(exc: Exception) -> str:
    """An unanticipated exception, worded for an operator notice — sanitized.

    This is the one detail in this module that carries model-influenced text
    without an escaping step. An SDK error routinely quotes the response body
    it failed on, so the model's own output ends up inside `str(exc)`,
    unbounded and carrying whatever characters it carried — and the operator
    reads this in a terminal, where `ConsoleNotifier` renders the notice body
    line-oriented. Reproduced: a live ANSI escape, a surviving right-to-left
    override, and a newline that wrote a forged `Recomendaciones:` block of
    its own, five hundred characters of padding behind it.

    The other two details need nothing. `AgentInvocationError.detail` is
    written by the invoker, and every `Violation.detail` interpolates the
    model's token with `!r`, which quotes and escapes it.

    `sanitize_source_text` is the same function the scraped aviso title
    passes through at all four of its rendering boundaries. Using it here
    rather than a local guard is the point of that module: the character
    class lives in one place, because two copies of it is how a bidi override
    eventually gets through one of them.
    """
    return sanitize_source_text(f"{type(exc).__name__}: {exc}")
