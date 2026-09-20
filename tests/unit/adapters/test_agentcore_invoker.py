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

from rain_alert.adapters import agentcore_invoker
from rain_alert.adapters.agent_invoker import AgentInvocationError
from rain_alert.adapters.agentcore_invoker import (
    CONNECT_TIMEOUT,
    MAX_RESPONSE_BYTES,
    READ_TIMEOUT,
    AgentRuntimeSettings,
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


def test_status_299_the_top_of_the_accepted_range_is_accepted() -> None:
    """The gate is `200 <= status < 300`; 299 is inside it and 300 is not.
    Neither boundary was exercised before — only far-outside values
    (`200`, `500`) were, which a widened or narrowed comparison would not
    disturb."""
    client = _FakeBotoClient(response=_json_response({"title": "t", "body": "b"}, status=299))
    invoker = BedrockAgentCoreInvoker(ARN, client=client)

    assert dict(invoker.invoke("hello")) == {"title": "t", "body": "b"}


def test_status_300_just_outside_the_accepted_range_is_bad_status() -> None:
    """Widening the gate to `200 <= status <= 300` survives against a
    fixture of `500` alone; `300` is the one value that distinguishes the
    two comparisons."""
    client = _FakeBotoClient(response=_json_response({"title": "t", "body": "b"}, status=300))
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
    event-stream body is unsupported rather than parsed.

    The body here is deliberately valid, parseable JSON — the same shape a
    successful call would carry. Disabling the content-type gate (`if False`)
    used to survive because the old fixture, `b"data: {}\\n\\n"`, is not
    valid JSON either, so the body still failed downstream for an unrelated
    reason and the test's `MALFORMED_PAYLOAD` assertion passed regardless of
    whether the gate ran at all. With a body that *would* decode cleanly,
    only the gate itself can make this test raise.
    """
    client = _FakeBotoClient(
        response={
            "statusCode": 200,
            "contentType": "text/event-stream",
            "response": _FakeStream(json.dumps({"title": "t", "body": "b"}).encode("utf-8")),
        }
    )
    invoker = BedrockAgentCoreInvoker(ARN, client=client)

    with pytest.raises(AgentInvocationError) as exc_info:
        invoker.invoke("hello")

    assert exc_info.value.reason is UnavailableReason.MALFORMED_PAYLOAD
    assert "contentType" in exc_info.value.detail


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


def test_the_cap_fires_even_when_truncating_at_it_would_parse_cleanly() -> None:
    """`test_an_oversized_body_is_refused_before_it_is_parsed` mutates
    `stream.read(MAX_RESPONSE_BYTES + 1)` to `stream.read(MAX_RESPONSE_BYTES)`
    and survives: its fixture is one huge unterminated JSON string, so
    truncating it anywhere — cap or no cap — lands mid-string and fails to
    parse for an unrelated reason, and the test only asserts the *reason*
    (`MALFORMED_PAYLOAD`), not *why*.

    This fixture's first `MAX_RESPONSE_BYTES` bytes are a complete, valid,
    whitespace-padded JSON object on their own; only a run of non-whitespace
    bytes appended after that pushes the whole body over the cap. Reading
    exactly `MAX_RESPONSE_BYTES` (the mutation) would therefore parse
    without error and return a *decoded* result, distinguishing "the cap
    fired" from "the parser choked" and making `+ 1` load-bearing again.
    """
    valid_prefix = json.dumps({"title": "t", "body": "b"}).encode("utf-8")
    padded = valid_prefix + b" " * (MAX_RESPONSE_BYTES - len(valid_prefix))
    assert json.loads(padded) == {"title": "t", "body": "b"}
    oversized = padded + b"x" * 8
    assert len(oversized) > MAX_RESPONSE_BYTES

    client = _FakeBotoClient(
        response={"statusCode": 200, "contentType": "application/json", "response": _FakeStream(oversized)}
    )
    invoker = BedrockAgentCoreInvoker(ARN, client=client)

    with pytest.raises(AgentInvocationError) as exc_info:
        invoker.invoke("hello")

    assert exc_info.value.reason is UnavailableReason.MALFORMED_PAYLOAD
    assert "exceeds" in exc_info.value.detail
    assert str(MAX_RESPONSE_BYTES) in exc_info.value.detail


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


def test_the_real_client_is_built_with_the_designed_service_and_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_resolved_client` is the one method every other test bypasses by
    injecting `client=` directly, so it never ran under test before this
    case: neither `_SERVICE_NAME` nor the `Config` it builds was ever
    checked. `boto3.client` is monkeypatched here — not `unittest.mock` — as
    the client-factory seam, so the real `bedrock-agentcore` client is still
    never constructed offline.

    Two mutations this reproduces surviving before this test existed:
    `"total_max_attempts"` renamed to `"max_attempts"` (the exact
    wrong-semantics trap the module's own docstring warns about — with
    `max_attempts: 1` a failed call gets one retry instead of none), and the
    whole `Config` block deleted in favour of `boto3.client(_SERVICE_NAME)`
    alone.
    """
    captured: dict[str, Any] = {}

    def fake_boto3_client(service_name: str, config: Any) -> _FakeBotoClient:
        captured["service_name"] = service_name
        captured["config"] = config
        return _FakeBotoClient(response=_json_response({"title": "t", "body": "b"}))

    monkeypatch.setattr(agentcore_invoker.boto3, "client", fake_boto3_client)
    invoker = BedrockAgentCoreInvoker(ARN)

    invoker.invoke("hello")

    assert captured["service_name"] == "bedrock-agentcore"
    config = captured["config"]
    assert config.connect_timeout == CONNECT_TIMEOUT
    assert config.read_timeout == READ_TIMEOUT
    assert config.retries == {"total_max_attempts": 1}


class TestAgentRuntimeSettings:
    """`RAIN_ALERT_AGENT_RUNTIME_ARN` / `RAIN_ALERT_AGENT_TIMEOUT_S` (design D23).

    Built here rather than through `StaticConfigRepository`/`AlertConfig`: an
    ARN is an AWS concern, and putting it on `AlertConfig` would widen the
    frozen stdlib core with infrastructure (D1), violating domain purity.
    Precedent: `DEFAULT_TIMEOUT` lives in `adapters/http.py`.
    """

    def test_the_arn_is_required(self) -> None:
        with pytest.raises(ValueError, match="RAIN_ALERT_AGENT_RUNTIME_ARN"):
            AgentRuntimeSettings.from_env({})

    def test_the_arn_is_read_from_its_env_var(self) -> None:
        settings = AgentRuntimeSettings.from_env({"RAIN_ALERT_AGENT_RUNTIME_ARN": ARN})

        assert settings.agent_runtime_arn == ARN

    def test_the_timeout_defaults_to_the_designed_deadline(self) -> None:
        settings = AgentRuntimeSettings.from_env({"RAIN_ALERT_AGENT_RUNTIME_ARN": ARN})

        assert settings.timeout_seconds == READ_TIMEOUT

    def test_the_timeout_env_var_overrides_the_default(self) -> None:
        """Tunable, not a constant (D19): the first live measurements can move
        it without a code change."""
        settings = AgentRuntimeSettings.from_env(
            {"RAIN_ALERT_AGENT_RUNTIME_ARN": ARN, "RAIN_ALERT_AGENT_TIMEOUT_S": "15"}
        )

        assert settings.timeout_seconds == 15.0

    def test_an_unparseable_timeout_fails_loudly(self) -> None:
        with pytest.raises(ValueError, match="RAIN_ALERT_AGENT_TIMEOUT_S"):
            AgentRuntimeSettings.from_env({"RAIN_ALERT_AGENT_RUNTIME_ARN": ARN, "RAIN_ALERT_AGENT_TIMEOUT_S": "soon"})

    def test_the_invoker_honours_a_configured_timeout(self) -> None:
        settings = AgentRuntimeSettings.from_env(
            {"RAIN_ALERT_AGENT_RUNTIME_ARN": ARN, "RAIN_ALERT_AGENT_TIMEOUT_S": "3"}
        )

        invoker = BedrockAgentCoreInvoker.from_settings(settings)

        assert invoker._read_timeout == 3.0  # noqa: SLF001 — the property under test
