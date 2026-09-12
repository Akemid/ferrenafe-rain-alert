"""The agent invocation seam (design.md D18).

The seam mirrors `HtmlFetcher` / `FetchError`, which is the shape change 1
already chose twice: a `Protocol` the offline suite can satisfy by hand, plus
one translated exception carrying a `UnavailableReason` the domain already
knows. Nothing here imports boto3, and no test in this module makes a network
call or needs a credential.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from rain_alert.adapters.agent_invoker import AgentInvocationError, AgentInvoker
from rain_alert.domain.values import UnavailableReason
from tests.support.fakes import FakeAgentInvoker


class TestAgentInvocationError:
    def test_it_carries_the_domain_reason_and_the_detail_separately(self) -> None:
        """The reason is what the fallback maps to a `FallbackReason`; the
        detail is what the operator notice prints. A single formatted string
        would force the composer to parse prose to learn which fault it
        survived."""
        error = AgentInvocationError(UnavailableReason.TIMEOUT, "read timed out after 10s")

        assert error.reason is UnavailableReason.TIMEOUT
        assert error.detail == "read timed out after 10s"

    def test_its_message_names_the_reason_so_a_traceback_is_readable(self) -> None:
        error = AgentInvocationError(UnavailableReason.BAD_STATUS, "HTTP 503")

        assert str(error) == "bad_status: HTTP 503"

    def test_it_is_not_a_fetch_error(self) -> None:
        """Deliberately its own type, per D18. Reusing `FetchError` would make
        `except FetchError` in the composer swallow a *fetch* error arriving
        from somewhere else entirely — and this seam is an agent invocation,
        not an HTML fetch."""
        from rain_alert.adapters.http import FetchError

        assert not issubclass(AgentInvocationError, FetchError)
        assert issubclass(AgentInvocationError, Exception)

    @pytest.mark.parametrize(
        "reason",
        [
            UnavailableReason.TIMEOUT,
            UnavailableReason.TRANSPORT_ERROR,
            UnavailableReason.BAD_STATUS,
            UnavailableReason.MALFORMED_PAYLOAD,
        ],
        ids=["timeout", "transport-error", "bad-status", "malformed-payload"],
    )
    def test_the_four_transport_reasons_the_fault_table_needs_already_exist(self, reason: UnavailableReason) -> None:
        """D18's claim, asserted rather than asserted-in-prose: the transport
        rows of the fault-injection table are exactly four values the domain
        already carries, so this change introduces no new enum."""
        assert AgentInvocationError(reason, "x").reason is reason


class _ScriptedInvoker:
    """A structural implementation, written here rather than imported, so the
    `Protocol` is exercised by something that does not subclass it."""

    def invoke(self, prompt: str) -> Mapping[str, Any]:
        return {"echo": prompt}


class TestAgentInvokerProtocol:
    def test_a_structural_implementation_satisfies_it(self) -> None:
        """`@runtime_checkable` is deliberately not used anywhere in this
        codebase — it only checks method names. Conformance is a `mypy
        --strict` fact, and this assignment is what makes it one here."""
        invoker: AgentInvoker = _ScriptedInvoker()

        assert invoker.invoke("hola") == {"echo": "hola"}

    def test_the_seam_returns_a_decoded_mapping_not_a_string(self) -> None:
        """The invoker owns decoding, so a body that is not JSON becomes a
        `MALFORMED_PAYLOAD` invocation error at the boundary rather than a
        parse attempt inside the composer."""
        result = _ScriptedInvoker().invoke("hola")

        assert isinstance(result, Mapping)


class TestFakeAgentInvoker:
    """The spy the whole offline fault table is driven through.

    Hand-written, per the standing rule in `tests/support/fakes.py`:
    `unittest.mock.Mock` satisfies any `Protocol`, so it would hide exactly
    the drift these tests exist to catch.
    """

    def test_it_conforms_to_the_seam_and_returns_its_scripted_response(self) -> None:
        invoker: AgentInvoker = FakeAgentInvoker(response={"title": "t", "body": "b"})

        assert invoker.invoke("prompt") == {"title": "t", "body": "b"}

    def test_it_records_every_prompt_it_was_given(self) -> None:
        """`calls` is what "the seam was invoked zero times" and "exactly
        once, with no retry" are asserted on."""
        invoker = FakeAgentInvoker(response={})
        invoker.invoke("first")
        invoker.invoke("second")

        assert invoker.calls == ["first", "second"]

    def test_a_scripted_exception_is_raised_instead_of_returned(self) -> None:
        failure = AgentInvocationError(UnavailableReason.TIMEOUT, "deadline exceeded")
        invoker = FakeAgentInvoker(error=failure)

        with pytest.raises(AgentInvocationError) as caught:
            invoker.invoke("prompt")

        assert caught.value is failure
        assert invoker.calls == ["prompt"]

    def test_it_can_script_an_exception_type_the_composer_never_anticipated(self) -> None:
        """Row 12 of the fault table needs a bare `RuntimeError`, not an
        `AgentInvocationError`, so the fake must not narrow what it raises."""
        invoker = FakeAgentInvoker(error=RuntimeError("botocore blew up"))

        with pytest.raises(RuntimeError, match="botocore"):
            invoker.invoke("prompt")

    def test_scripting_neither_a_response_nor_an_error_is_refused_at_construction(self) -> None:
        """A fake with nothing scripted would return `None` into a composer
        typed to receive a mapping, and the resulting failure would be read as
        a defect in the composer."""
        with pytest.raises(ValueError, match="response"):
            FakeAgentInvoker()
