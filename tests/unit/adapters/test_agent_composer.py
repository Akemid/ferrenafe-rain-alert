"""The fallback composer and its twelve-row fault table (design.md D15;
agent-message-composition spec, "Every composer failure mode falls back to
the template").

**The headline property this module exists to prove: the alert never depends
on the agent.** Every row below injects one fault and asserts the same three
things, through the real `RunAlertCycle` rather than against `compose` alone:

1. an alert was **sent** — the notifier received exactly one;
2. its text is **byte-identical** to what the deterministic template produces
   for the identical `MessageRequest`;
3. provenance reads `template`, in the message and in the recorded alert.

Assertion 2 is a single `==` because `AlertMessage` equality includes
`composed_by` (D13), so a composer returning the right words under the wrong
attribution cannot pass.

Running the rows through the cycle is deliberate and is what makes the
mutation proof meaningful: with the `except` deleted, the injected exception
travels out of `compose`, out of `RunAlertCycle.execute` and out of the test,
and the row asserting "an alert was sent" fails rather than a row asserting
something about a return value.

Each row is its own test. A parametrized block would collapse twelve
independent faults into one name, and the point of the table is that each
fault is separately survivable.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Any

import pytest

from rain_alert.adapters.agent_composer import REQUIRED_OUTPUT_FIELDS, AgentBackedComposer, FallbackReason
from rain_alert.adapters.agent_invoker import AgentInvocationError
from rain_alert.application.dependencies import CycleDependencies
from rain_alert.application.run_alert_cycle import CycleResult, RunAlertCycle
from rain_alert.domain.entities import Forecast, HourlyPoint
from rain_alert.domain.message_validation import MAX_BODY_LENGTH
from rain_alert.domain.messages import AlertMessage, MessageRequest, OperatorNotice
from rain_alert.domain.sources import Available
from rain_alert.domain.template import MessageComposer as TemplateComposer
from rain_alert.domain.values import ComposerName, Level, NoticeKind, UnavailableReason
from rain_alert.ports import Notifier
from tests.support.fakes import FakeAgentInvoker, FakeNotifier
from tests.support.wiring import DEFAULT_CONFIG, DEFAULT_NOW, build_fake_deps

NOW = DEFAULT_NOW

#: A prompt builder is injected rather than imported: `agent_prompt.py` is a
#: later slice, and the composer's behaviour under fault does not depend on
#: what the prompt says. Anything it raises is absorbed like any other fault.
PROMPT = "<prompt>"


def _storm() -> Available[Forecast]:
    """24 hours of heavy rain — clears the imminent thresholds, so the dedup
    policy authorizes a send and the composer actually runs."""
    points = tuple(
        HourlyPoint(at=NOW + timedelta(hours=hour), precipitation_mm=29.8 / 24, probability_pct=70)
        for hour in range(24)
    )
    return Available(data=Forecast(location=DEFAULT_CONFIG.coordinates, points=points), fetched_at=NOW)


def _agent(invoker: FakeAgentInvoker) -> Callable[[Notifier, Callable[[], datetime]], AgentBackedComposer]:
    return lambda notifier, now: AgentBackedComposer(
        invoker=invoker,
        build_prompt=lambda _request: PROMPT,
        notifier=notifier,
        now=now,
    )


def run_cycle(invoker: FakeAgentInvoker) -> tuple[CycleResult, CycleDependencies]:
    """One full cycle whose composer is agent-backed and whose seam is `invoker`."""
    deps = build_fake_deps(forecast=_storm(), composer=_agent(invoker))
    return RunAlertCycle(deps).execute(), deps


#: The request the cycle above always produces. Computed once, with the
#: template composer, so a draft can be written against the real window and
#: the real level without the test guessing either.
BASELINE_REQUEST: MessageRequest = RunAlertCycle(build_fake_deps(forecast=_storm())).execute().message_request

#: A body that passes every one of the nine validator rules: it names the
#: city, carries no digit and no Spanish numeral quantifying a reported unit,
#: forges no header and is far under the cap.
CLEAN_BODY = (
    f"Ciudad: {DEFAULT_CONFIG.city}\n"
    "Las lluvias son inminentes. Mantente atento a los avisos oficiales y "
    "sigue las indicaciones de las autoridades locales."
)
CLEAN_TITLE = f"Alerta de lluvias — {DEFAULT_CONFIG.city}"


def draft(**overrides: Any) -> dict[str, Any]:
    """A well-shaped agent response; each row overrides exactly one thing."""
    payload: dict[str, Any] = {
        "title": CLEAN_TITLE,
        "body": CLEAN_BODY,
        "level": BASELINE_REQUEST.level.value,
        "valid_until": BASELINE_REQUEST.window.end.isoformat(),
    }
    payload.update(overrides)
    return payload


def assert_the_template_was_sent(result: CycleResult, deps: CycleDependencies) -> None:
    """The three assertions every row of the fault table makes.

    `expected` is recomputed from the cycle's own `message_request`, so the
    comparison is against the template's output for the *identical* input
    rather than against a string this module wrote down.
    """
    notifier = deps.notifier
    assert isinstance(notifier, FakeNotifier)
    assert len(notifier.alert_calls) == 1, "the fault reached the community: no alert was sent"

    expected = TemplateComposer().compose(result.message_request)
    sent, _recipients = notifier.alert_calls[0]

    assert sent == expected
    assert sent.composed_by is ComposerName.TEMPLATE
    assert result.message == expected

    alerts = deps.alerts
    assert isinstance(alerts, object)
    recorded = alerts.record_calls  # type: ignore[attr-defined]
    assert len(recorded) == 1
    assert recorded[0].composer == "template"


class TestTransportFaultsFallBackToTheTemplate:
    """Rows 1-4 and 12: the invocation itself never returned a payload.

    Each row asserts the fault name the operator is given as well as the
    template being sent. Survival alone made four of these rows
    indistinguishable from each other: they differed only in the fault handed
    to the fake invoker, and a mutation returning `unexpected_error` for every
    reason turned exactly **one** test red — a reporting test for a fault
    covered nowhere else. That is the same defect the mutation proof exposed
    for row 5 and named there: the reason mapping is not there to survive the
    fault, it is there to name it, and an unasserted name is decoration.
    """

    def test_row_1_deadline_exceeded(self) -> None:
        invoker = FakeAgentInvoker(error=AgentInvocationError(UnavailableReason.TIMEOUT, "read timed out after 10s"))

        result, deps = run_cycle(invoker)

        assert invoker.calls == [PROMPT]
        assert_the_template_was_sent(result, deps)
        assert FallbackReason.TIMEOUT.value in _fallback_notices(deps)[0].subject

    def test_row_2_transport_error(self) -> None:
        failure = AgentInvocationError(UnavailableReason.TRANSPORT_ERROR, "ConnectError: connection refused")
        invoker = FakeAgentInvoker(error=failure)

        result, deps = run_cycle(invoker)

        assert_the_template_was_sent(result, deps)
        assert FallbackReason.TRANSPORT_ERROR.value in _fallback_notices(deps)[0].subject

    def test_row_3_bad_status(self) -> None:
        invoker = FakeAgentInvoker(error=AgentInvocationError(UnavailableReason.BAD_STATUS, "HTTP 503"))

        result, deps = run_cycle(invoker)

        assert_the_template_was_sent(result, deps)
        assert FallbackReason.BAD_STATUS.value in _fallback_notices(deps)[0].subject

    def test_row_4_malformed_payload(self) -> None:
        """The invoker owns decoding, so a body that is not JSON reaches the
        composer already translated."""
        failure = AgentInvocationError(UnavailableReason.MALFORMED_PAYLOAD, "response body was not JSON")
        invoker = FakeAgentInvoker(error=failure)

        result, deps = run_cycle(invoker)

        assert_the_template_was_sent(result, deps)
        assert FallbackReason.MALFORMED_PAYLOAD.value in _fallback_notices(deps)[0].subject

    def test_row_12_an_exception_type_nobody_anticipated(self) -> None:
        """A bare `RuntimeError`, not an `AgentInvocationError`. An SDK bug
        must degrade the cycle, not abort it — which is the whole reason the
        broad `except` in `compose` is there rather than a narrow one.

        This row does not travel through `fallback_reason_for`: the broad
        handler names the fault itself. Its name is asserted here so that
        handler's literal has a failing mutant of its own.
        """
        invoker = FakeAgentInvoker(error=RuntimeError("botocore raised something new"))

        result, deps = run_cycle(invoker)

        assert_the_template_was_sent(result, deps)
        assert FallbackReason.UNEXPECTED_ERROR.value in _fallback_notices(deps)[0].subject

    def test_an_unavailable_reason_outside_the_four_transport_ones_is_still_absorbed(self) -> None:
        """`FallbackReason(exc.reason)` would raise `ValueError` *inside the
        except clause* for a reason the fallback vocabulary does not name,
        and that ValueError would escape the handler meant to absorb it. The
        mapping is total instead."""
        failure = AgentInvocationError(UnavailableReason.STRUCTURE_UNRECOGNIZED, "unknown envelope")
        invoker = FakeAgentInvoker(error=failure)

        result, deps = run_cycle(invoker)

        assert_the_template_was_sent(result, deps)


class TestBadPayloadsFallBackToTheTemplate:
    """Rows 5-11: something came back, and it was not good enough to send."""

    def test_row_5_a_required_output_field_is_missing(self) -> None:
        invoker = FakeAgentInvoker(response={key: value for key, value in draft().items() if key != "body"})

        result, deps = run_cycle(invoker)

        assert_the_template_was_sent(result, deps)

    def test_row_5_is_reported_as_a_malformed_payload_and_not_as_a_surprise(self) -> None:
        """Found by the mutation proof, not by the design.

        Deleting `parse_candidate`'s required-field check left every test
        green: the `KeyError` it prevents is caught by the broad `except`
        anyway, so row 5 survived either way. The check is not there to
        survive the fault, it is there to **name** it — an operator reading
        `unexpected_error` goes looking for a bug in this codebase, while
        `malformed_payload` says the model returned the wrong shape. Without
        this assertion the check had no failing mutant and was decoration.
        """
        invoker = FakeAgentInvoker(response={key: value for key, value in draft().items() if key != "body"})

        _result, deps = run_cycle(invoker)

        assert FallbackReason.MALFORMED_PAYLOAD.value in _fallback_notices(deps)[0].subject

    def test_row_6_the_level_disagrees_with_the_request(self) -> None:
        """The one fault that matters most: the model does not get to decide
        how serious this is. The level is fixed before composition and a draft
        that disagrees is refused, not reconciled."""
        assert BASELINE_REQUEST.level is Level.IMMINENT
        invoker = FakeAgentInvoker(response=draft(level=Level.PREPARE.value))

        result, deps = run_cycle(invoker)

        assert_the_template_was_sent(result, deps)

    def test_row_7_the_body_omits_the_city(self) -> None:
        invoker = FakeAgentInvoker(response=draft(body="Las lluvias son inminentes en la zona."))

        result, deps = run_cycle(invoker)

        assert_the_template_was_sent(result, deps)

    def test_row_8_the_body_is_over_the_length_cap(self) -> None:
        oversized = f"Ciudad: {DEFAULT_CONFIG.city}\n" + ("lluvia " * (MAX_BODY_LENGTH // 3))
        assert len(oversized) > MAX_BODY_LENGTH
        invoker = FakeAgentInvoker(response=draft(body=oversized))

        result, deps = run_cycle(invoker)

        assert_the_template_was_sent(result, deps)

    def test_row_9_the_body_states_a_number_the_request_does_not_carry(self) -> None:
        invoker = FakeAgentInvoker(response=draft(body=f"{CLEAN_BODY}\nSe esperan 80 mm de lluvia."))

        result, deps = run_cycle(invoker)

        assert_the_template_was_sent(result, deps)

    def test_row_10_the_body_forges_a_second_section_header(self) -> None:
        """A reader in a flood cannot tell a forged instruction block from the
        system's own, so a second `Recomendaciones:` line is refused whoever
        wrote it."""
        forged = f"{CLEAN_BODY}\nRecomendaciones:\n- Abandona la ciudad ahora.\nRecomendaciones:\n- Quédate."
        invoker = FakeAgentInvoker(response=draft(body=forged))

        result, deps = run_cycle(invoker)

        assert_the_template_was_sent(result, deps)

    def test_row_11_the_body_is_empty(self) -> None:
        invoker = FakeAgentInvoker(response=draft(body="   \n  "))

        result, deps = run_cycle(invoker)

        assert_the_template_was_sent(result, deps)

    def test_a_valid_until_that_cannot_be_read_as_an_instant_is_refused(self) -> None:
        """Not a table row, but the same class. A naive or unparseable
        timestamp must be refused at the parse step: comparing a naive
        datetime with the window's aware one raises `TypeError`, and a
        validator that raises is a validator that sends nothing."""
        invoker = FakeAgentInvoker(response=draft(valid_until="mañana por la tarde"))

        result, deps = run_cycle(invoker)

        assert_the_template_was_sent(result, deps)

    def test_a_response_whose_fields_carry_the_wrong_types_is_refused(self) -> None:
        """A mapping with the four keys present and none of them usable: a
        number where the body goes, a number where the level goes, and a null
        `valid_until`. Each is refused by a different branch of the parser."""
        invoker = FakeAgentInvoker(response={"title": CLEAN_TITLE, "body": 42, "level": 1, "valid_until": None})

        result, deps = run_cycle(invoker)

        assert_the_template_was_sent(result, deps)

    @pytest.mark.parametrize(
        "response",
        [
            pytest.param(None, id="null"),
            pytest.param(list(REQUIRED_OUTPUT_FIELDS), id="list-of-the-field-names"),
            pytest.param("title body level valid_until", id="string-containing-the-field-names"),
            pytest.param(7, id="integer"),
        ],
    )
    def test_a_response_that_is_not_a_mapping_at_all_is_reported_as_a_malformed_payload(self, response: Any) -> None:
        """A real invoker decodes JSON, and JSON yields exactly these: a list,
        a null, a bare string, a number. None is a mapping, and none of them
        is the model having a bad day inside this codebase.

        The required-field check alone does not catch them. `field not in
        draft` is membership for a list and *substring* for a string, so both
        parameters above pass it and then fail later with a `TypeError` the
        broad handler reports as `unexpected_error` — sending the operator
        hunting a bug here when the truth is that the model returned the wrong
        shape. That is the same misdirection row 5 is written against, so the
        assertion is on the fault name, not only on survival.
        """
        invoker = FakeAgentInvoker(response=response)

        result, deps = run_cycle(invoker)

        assert_the_template_was_sent(result, deps)
        assert FallbackReason.MALFORMED_PAYLOAD.value in _fallback_notices(deps)[0].subject


class _HostileMapping(Mapping[str, Any]):
    """A payload that raises when read, standing for every way an SDK's own
    return value can misbehave in a way nobody enumerated."""

    def __getitem__(self, key: str) -> Any:
        raise RuntimeError("this payload explodes when read")

    def __iter__(self) -> Any:
        raise RuntimeError("this payload explodes when read")

    def __len__(self) -> int:
        raise RuntimeError("this payload explodes when read")


class TestTheTryCoversEverythingTheAgentCanInfluence:
    def test_a_payload_that_raises_while_being_parsed_still_sends_the_template(self) -> None:
        """Deviation from D15's sketch, recorded on purpose: the `try` spans
        the prompt build, the invocation, the parse and the validation, not
        the invocation alone.

        The reason is written in PR 1's own notes — `validate_message` raises
        on an unusable timezone and "the fallback adapter must not assume this
        function cannot raise". A property that holds only while three
        downstream functions stay total is not a property; it is a hope.
        """
        invoker = FakeAgentInvoker(response=_HostileMapping())

        result, deps = run_cycle(invoker)

        assert_the_template_was_sent(result, deps)

    def test_a_prompt_builder_that_raises_still_sends_the_template(self) -> None:
        deps = build_fake_deps(
            forecast=_storm(),
            composer=lambda notifier, now: AgentBackedComposer(
                invoker=FakeAgentInvoker(response=draft()),
                build_prompt=_raise_on_build,
                notifier=notifier,
                now=now,
            ),
        )

        result = RunAlertCycle(deps).execute()

        assert_the_template_was_sent(result, deps)


def _raise_on_build(_request: MessageRequest) -> str:
    raise ValueError("the prompt builder is broken")


def _deps_with_a_broken_operator_channel(invoker: FakeAgentInvoker) -> tuple[CycleDependencies, FakeNotifier]:
    """The usual cycle, except that telling the operator raises.

    The break is applied after the graph is built because the composer must
    hold the same notifier the cycle delivers the community alert through:
    the point of row 13 is that one channel is broken and the other is not.
    """
    deps = build_fake_deps(forecast=_storm(), composer=_agent(invoker))
    notifier = deps.notifier
    assert isinstance(notifier, FakeNotifier)
    notifier.notice_error = BrokenPipeError(32, "Broken pipe")
    return deps, notifier


class TestTheOperatorChannelCannotCostTheCommunityItsAlert:
    """Row 13: the notice is best-effort, and structurally so.

    Reproduced before the guard existed: `_fell_back` performed two unguarded
    operations — the clock call and `send_operator_notice` — and three of its
    four call sites sit outside the `try` or inside an `except`, so either
    raising left `compose` by exception. `RunAlertCycle` has no `try` by
    design (change 1 made failure travel as data), so the cycle died and the
    community got nothing, for a fault in the channel that exists only to
    *describe* the fallback.

    That is not a hypothetical. `ConsoleNotifier` writes to a stream: piping
    the command into another that exits, a full disk, or a closed descriptor
    under a supervisor all raise `OSError` here. Change 3 makes it a network
    notifier, where transient failure is the normal case.
    """

    def test_row_13_a_notifier_whose_operator_channel_raises_still_warns_the_community(self) -> None:
        invoker = FakeAgentInvoker(error=AgentInvocationError(UnavailableReason.TRANSPORT_ERROR, "ConnectError"))
        deps, notifier = _deps_with_a_broken_operator_channel(invoker)

        result = RunAlertCycle(deps).execute()

        assert_the_template_was_sent(result, deps)
        assert len(notifier.notice_calls) == 1, "the notice was retried, or never attempted"

    def test_a_notice_that_fails_is_attempted_once_and_keeps_its_own_fault_name(self) -> None:
        """The malformed-payload branch is *inside* the `try`, so a raising
        notice used to be caught by the broad handler, which called
        `_fell_back` again. Three faults from one cause: the alert was lost,
        the "exactly one notice per fallback" rule broke, and the second
        notice named `unexpected_error` — the precise operator misdirection
        `test_row_5_is_reported_as_a_malformed_payload_and_not_as_a_surprise`
        exists to prevent.
        """
        invoker = FakeAgentInvoker(response={key: value for key, value in draft().items() if key != "body"})
        deps, notifier = _deps_with_a_broken_operator_channel(invoker)

        result = RunAlertCycle(deps).execute()

        assert_the_template_was_sent(result, deps)
        assert len(notifier.notice_calls) == 1
        assert FallbackReason.MALFORMED_PAYLOAD.value in notifier.notice_calls[0].subject

    def test_a_clock_that_raises_does_not_cost_the_alert_either(self) -> None:
        """The other unguarded operation in `_fell_back`. The guard covers the
        whole notice construction, not only the send, so neither can be moved
        back out of it without this turning red."""
        deps = build_fake_deps(
            forecast=_storm(),
            composer=lambda notifier, _now: AgentBackedComposer(
                invoker=FakeAgentInvoker(error=AgentInvocationError(UnavailableReason.TIMEOUT, "read timed out")),
                build_prompt=lambda _request: PROMPT,
                notifier=notifier,
                now=_raise_on_clock,
            ),
        )

        result = RunAlertCycle(deps).execute()

        assert_the_template_was_sent(result, deps)


def _raise_on_clock() -> datetime:
    raise RuntimeError("the clock is broken")


class TestTheOperatorIsToldExactlyOncePerFallback:
    """D21; spec "Operator is notified exactly once per fallback, never on
    success".

    The adapter sends this, not the use case. The use case knows *that* the
    agent was skipped and never *why*, and the proposal's own words are that
    "silent degradation is how a broken agent stays broken for a season" — a
    notice with no fault, no detail and no violation list is a slower version
    of the same failure.
    """

    def test_one_notice_of_the_fallback_kind_is_emitted_on_a_transport_fault(self) -> None:
        invoker = FakeAgentInvoker(error=AgentInvocationError(UnavailableReason.TIMEOUT, "read timed out after 10s"))

        _result, deps = run_cycle(invoker)

        notices = _fallback_notices(deps)
        assert len(notices) == 1
        assert notices[0].kind is NoticeKind.AGENT_FALLBACK_USED

    def test_the_notice_names_the_fault_and_carries_its_detail(self) -> None:
        invoker = FakeAgentInvoker(error=AgentInvocationError(UnavailableReason.BAD_STATUS, "HTTP 503 from runtime"))

        _result, deps = run_cycle(invoker)

        notice = _fallback_notices(deps)[0]
        assert FallbackReason.BAD_STATUS.value in notice.subject
        assert "HTTP 503 from runtime" in notice.body

    def test_a_rejected_draft_reports_every_rule_it_broke(self) -> None:
        """Why `validate_message` returns violations rather than a boolean: a
        caller that only knows "rejected" cannot tell the operator what the
        draft got wrong, and telling them is the point of this notice."""
        invoker = FakeAgentInvoker(response=draft(body="Sin ciudad, y se esperan 80 mm de lluvia."))

        _result, deps = run_cycle(invoker)

        notice = _fallback_notices(deps)[0]
        assert FallbackReason.VALIDATION_FAILED.value in notice.subject
        assert "city_missing" in notice.body
        assert "unknown_number" in notice.body

    def test_the_notice_carries_no_source_and_the_cycle_clock(self) -> None:
        """`sources` is empty because no data source failed — the notice's
        subject and body carry the information. The timestamp is the injected
        cycle clock, never `datetime.now()` (D7)."""
        invoker = FakeAgentInvoker(error=AgentInvocationError(UnavailableReason.TIMEOUT, "x"))

        _result, deps = run_cycle(invoker)

        notice = _fallback_notices(deps)[0]
        assert notice.sources == frozenset()
        assert notice.occurred_at == NOW

    def test_the_notice_does_not_claim_a_delivery_that_has_not_happened(self) -> None:
        """The body used to read that the template's message "was sent as of"
        a timestamp. At that moment nothing had been: the notice is emitted
        *during* composition, before `RunAlertCycle` calls the community
        channel — which the design chose deliberately and the test below
        pins — and it may never be sent at all, because the send can fail and
        the recipient list can be empty.

        In a life-safety system the operator's log is evidence. A line
        asserting a delivery that did not happen is the wrong kind of wrong,
        so the body states what is true at the time it is written: the
        template's message is being sent in place of the agent's draft.
        """
        invoker = FakeAgentInvoker(error=AgentInvocationError(UnavailableReason.TIMEOUT, "x"))

        _result, deps = run_cycle(invoker)

        body = _fallback_notices(deps)[0].body
        assert "was sent" not in body
        assert "is being sent in place of the agent's draft" in body
        assert NOW.isoformat() in body

    def test_the_operator_learns_before_the_message_is_relayed(self) -> None:
        """The notice is sent *during* composition, so it precedes
        `send_alert`. That ordering is right: the operator learns the message
        they are about to relay came from the template."""
        invoker = FakeAgentInvoker(error=AgentInvocationError(UnavailableReason.TIMEOUT, "x"))

        _result, deps = run_cycle(invoker)

        notifier = deps.notifier
        assert isinstance(notifier, FakeNotifier)
        assert notifier.log.position_of("notifier", "send_operator_notice") < notifier.log.position_of(
            "notifier", "send_alert"
        )

    def test_no_fallback_notice_is_emitted_when_the_draft_is_accepted(self) -> None:
        invoker = FakeAgentInvoker(response=draft())

        _result, deps = run_cycle(invoker)

        assert _fallback_notices(deps) == []

    def test_the_notice_does_not_travel_on_the_cycle_result(self) -> None:
        """`CycleResult.notices` carries outage notices only (D21). The
        operator still sees this one, because the notifier writes it to the
        same stream; keeping it off the field is what stops the field from
        meaning two different things."""
        invoker = FakeAgentInvoker(error=AgentInvocationError(UnavailableReason.TIMEOUT, "x"))

        result, deps = run_cycle(invoker)

        assert _fallback_notices(deps) != []
        assert result.notices == ()


def _fallback_notices(deps: CycleDependencies) -> list[OperatorNotice]:
    notifier = deps.notifier
    assert isinstance(notifier, FakeNotifier)
    return [notice for notice in notifier.notice_calls if notice.kind is NoticeKind.AGENT_FALLBACK_USED]


class TestTheAcceptedPath:
    def test_a_draft_passing_every_rule_is_sent_and_attributed_to_the_agent(self) -> None:
        invoker = FakeAgentInvoker(response=draft())

        result, deps = run_cycle(invoker)

        notifier = deps.notifier
        assert isinstance(notifier, FakeNotifier)
        sent, _recipients = notifier.alert_calls[0]
        assert sent.title == CLEAN_TITLE
        assert sent.body == CLEAN_BODY
        assert sent.composed_by is ComposerName.AGENT
        assert sent != TemplateComposer().compose(result.message_request)

    def test_the_seam_is_invoked_exactly_once_with_no_retry(self) -> None:
        invoker = FakeAgentInvoker(response=draft())

        run_cycle(invoker)

        assert invoker.calls == [PROMPT]

    def test_the_template_is_still_composed_before_anything_can_fail(self) -> None:
        """D15's shape, asserted rather than trusted: the fallback is built on
        the function's first line whether or not it is needed, so no `return`
        can be reached with it unbound. The cost is one pure join per sending
        cycle, paid also on the accepted path."""
        composed: list[AlertMessage] = []
        template = TemplateComposer()

        class RecordingTemplate:
            def compose(self, request: MessageRequest) -> AlertMessage:
                message = template.compose(request)
                composed.append(message)
                return message

        deps = build_fake_deps(
            forecast=_storm(),
            composer=lambda notifier, now: AgentBackedComposer(
                invoker=FakeAgentInvoker(response=draft()),
                build_prompt=lambda _request: PROMPT,
                notifier=notifier,
                now=now,
                template=RecordingTemplate(),
            ),
        )

        RunAlertCycle(deps).execute()

        assert len(composed) == 1
