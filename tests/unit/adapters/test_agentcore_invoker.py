"""`BedrockAgentCoreInvoker` — the real `AgentInvoker` (design.md D18, D19).

Every case here injects a hand-written fake boto3 client (`tests/support/fakes.py`'s
own convention: no `unittest.mock`, because `Mock` satisfies any interface and
would hide exactly the drift these tests exist to catch). No credentials, no
network, no real `boto3` client is ever constructed.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest
from botocore.exceptions import ClientError, ConnectTimeoutError, EndpointConnectionError

from rain_alert.adapters.agent_invoker import AgentInvocationError
from rain_alert.adapters.agentcore_invoker import (
    MAX_RESPONSE_BYTES,
    BedrockAgentCoreInvoker,
)
from rain_alert.domain.values import UnavailableReason

ARN = "arn:aws:bedrock-agentcore:us-east-1:example-account-id:runtime/example-runtime-abc123"


class _FakeStream:
    """A hand-written stand-in for boto3's `StreamingBody`."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self, amount: int | None = None) -> bytes:
        if amount is None:
            return self._body
        return self._body[:amount]


class _FakeBotoClient:
    """`invoke_agent_runtime` scripted to return a response or raise."""

    def __init__(self, response: Mapping[str, Any] | None = None, error: BaseException | None = None) -> None:
        self._response = response
        self._error = error
        self.calls: list[dict[str, Any]] = []

    def invoke_agent_runtime(self, **kwargs: Any) -> Mapping[str, Any]:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response


def _json_response(payload: Mapping[str, Any], *, status: int = 200) -> dict[str, Any]:
    return {
        "statusCode": status,
        "contentType": "application/json",
        "response": _FakeStream(json.dumps(payload).encode("utf-8")),
    }


def test_a_successful_call_returns_the_decoded_json_body() -> None:
    client = _FakeBotoClient(response=_json_response({"title": "t", "body": "b"}))
    invoker = BedrockAgentCoreInvoker(ARN, client=client)

    result = invoker.invoke("a prompt")

    assert dict(result) == {"title": "t", "body": "b"}


def test_the_prompt_is_sent_as_a_json_payload_under_the_agreed_key() -> None:
    client = _FakeBotoClient(response=_json_response({"title": "t", "body": "b"}))
    invoker = BedrockAgentCoreInvoker(ARN, client=client)

    invoker.invoke("hello")

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["agentRuntimeArn"] == ARN
    assert json.loads(call["payload"]) == {"prompt": "hello"}


def test_no_runtime_session_id_is_sent_unless_one_is_supplied() -> None:
    """AWS auto-generates one when omitted; a hand-rolled id risks the
    documented 33-character minimum (`runtimeSessionId` is an idempotency
    token, min 33 / max 256 characters per the service model)."""
    client = _FakeBotoClient(response=_json_response({"title": "t", "body": "b"}))
    invoker = BedrockAgentCoreInvoker(ARN, client=client)

    invoker.invoke("hello")

    assert "runtimeSessionId" not in client.calls[0]


def test_a_qualifier_is_forwarded_when_configured() -> None:
    client = _FakeBotoClient(response=_json_response({"title": "t", "body": "b"}))
    invoker = BedrockAgentCoreInvoker(ARN, client=client, qualifier="DEFAULT")

    invoker.invoke("hello")

    assert client.calls[0]["qualifier"] == "DEFAULT"


def test_a_connect_timeout_raises_agent_invocation_error_tagged_timeout() -> None:
    client = _FakeBotoClient(error=ConnectTimeoutError(endpoint_url="https://example.invalid"))
    invoker = BedrockAgentCoreInvoker(ARN, client=client)

    with pytest.raises(AgentInvocationError) as exc_info:
        invoker.invoke("hello")

    assert exc_info.value.reason is UnavailableReason.TIMEOUT


def test_a_connection_refused_raises_agent_invocation_error_tagged_transport_error() -> None:
    client = _FakeBotoClient(error=EndpointConnectionError(endpoint_url="https://example.invalid"))
    invoker = BedrockAgentCoreInvoker(ARN, client=client)

    with pytest.raises(AgentInvocationError) as exc_info:
        invoker.invoke("hello")

    assert exc_info.value.reason is UnavailableReason.TRANSPORT_ERROR


def test_a_non_2xx_response_raises_agent_invocation_error_tagged_bad_status() -> None:
    client = _FakeBotoClient(response=_json_response({"title": "t", "body": "b"}, status=500))
    invoker = BedrockAgentCoreInvoker(ARN, client=client)

    with pytest.raises(AgentInvocationError) as exc_info:
        invoker.invoke("hello")

    assert exc_info.value.reason is UnavailableReason.BAD_STATUS


def test_a_modeled_client_error_raises_agent_invocation_error_tagged_bad_status() -> None:
    """`ValidationException`, `ThrottlingException` and the rest of the
    operation's modeled errors all arrive as `botocore.exceptions.ClientError`."""
    error = ClientError(
        error_response={"Error": {"Code": "ThrottlingException", "Message": "rate limited"}},
        operation_name="InvokeAgentRuntime",
    )
    client = _FakeBotoClient(error=error)
    invoker = BedrockAgentCoreInvoker(ARN, client=client)

    with pytest.raises(AgentInvocationError) as exc_info:
        invoker.invoke("hello")

    assert exc_info.value.reason is UnavailableReason.BAD_STATUS
    assert "ThrottlingException" in exc_info.value.detail


def test_a_non_json_body_raises_agent_invocation_error_tagged_malformed_payload() -> None:
    client = _FakeBotoClient(
        response={
            "statusCode": 200,
            "contentType": "application/json",
            "response": _FakeStream(b"not json at all"),
        }
    )
    invoker = BedrockAgentCoreInvoker(ARN, client=client)

    with pytest.raises(AgentInvocationError) as exc_info:
        invoker.invoke("hello")

    assert exc_info.value.reason is UnavailableReason.MALFORMED_PAYLOAD


def test_a_non_mapping_json_body_is_also_malformed_payload() -> None:
    client = _FakeBotoClient(
        response={
            "statusCode": 200,
            "contentType": "application/json",
            "response": _FakeStream(b"[1, 2, 3]"),
        }
    )
    invoker = BedrockAgentCoreInvoker(ARN, client=client)

    with pytest.raises(AgentInvocationError) as exc_info:
        invoker.invoke("hello")

    assert exc_info.value.reason is UnavailableReason.MALFORMED_PAYLOAD


def test_an_unsupported_content_type_is_malformed_payload() -> None:
    """A streaming response is a shape this application never produces
    (`agent/app.py`'s entrypoint returns a plain dict, not a generator), so an
    event-stream body is unsupported rather than parsed."""
    client = _FakeBotoClient(
        response={
            "statusCode": 200,
            "contentType": "text/event-stream",
            "response": _FakeStream(b"data: {}\n\n"),
        }
    )
    invoker = BedrockAgentCoreInvoker(ARN, client=client)

    with pytest.raises(AgentInvocationError) as exc_info:
        invoker.invoke("hello")

    assert exc_info.value.reason is UnavailableReason.MALFORMED_PAYLOAD


def test_an_oversized_body_is_refused_before_it_is_parsed() -> None:
    oversized = json.dumps({"title": "t" * MAX_RESPONSE_BYTES, "body": "b"}).encode("utf-8")
    assert len(oversized) > MAX_RESPONSE_BYTES
    client = _FakeBotoClient(
        response={"statusCode": 200, "contentType": "application/json", "response": _FakeStream(oversized)}
    )
    invoker = BedrockAgentCoreInvoker(ARN, client=client)

    with pytest.raises(AgentInvocationError) as exc_info:
        invoker.invoke("hello")

    assert exc_info.value.reason is UnavailableReason.MALFORMED_PAYLOAD


def test_an_unexpected_exception_type_is_not_swallowed_here() -> None:
    """The invoker translates every *known* failure; an SDK bug producing an
    exception type nobody anticipated is `AgentBackedComposer`'s problem to
    absorb (design D15's broad `except Exception`), not this module's to hide."""
    client = _FakeBotoClient(error=RuntimeError("something the SDK never documented"))
    invoker = BedrockAgentCoreInvoker(ARN, client=client)

    with pytest.raises(RuntimeError):
        invoker.invoke("hello")


def test_no_client_is_built_until_the_first_invoke() -> None:
    """Constructing a `boto3` client without a region configured raises on a
    clean machine, and the offline suite must not depend on one being
    present (design D18)."""
    invoker = BedrockAgentCoreInvoker(ARN)

    assert invoker._client is None  # noqa: SLF001 — the property under test
