"""`BedrockAgentCoreInvoker` — the real `AgentInvoker` (design.md D18, D19).

**The AgentCore data-plane invocation, resolved against the live service
model (`botocore` 1.43.98) at apply time (design §13 to-verify #2).**

- Client: `boto3.client("bedrock-agentcore")` — the data plane.
  `bedrock-agentcore-control` is the control plane and is not used here.
- Operation: `invoke_agent_runtime`. Required input members: `agentRuntimeArn`
  and `payload` (a blob — `json.dumps(...).encode()`). `runtimeSessionId` is
  optional and, if supplied, must be 33-256 characters (an idempotency
  token); this module never supplies one, so AWS generates it.
- Output members used here: `response` (a blob — a `StreamingBody` in
  `boto3`), `contentType`, `statusCode`.
- **Streaming.** The operation supports streaming, distinguished by
  `response["contentType"]`: `"text/event-stream"` means the caller should
  iterate `response["response"].iter_lines()`; `"application/json"` means
  read the whole body. `agent/app.py`'s `@app.entrypoint` returns a plain
  dict, not a generator, so this application's responses are always
  `application/json` — an event-stream response is therefore unsupported
  rather than parsed, and D19's deadline mechanism is a bounded read plus the
  client's own read timeout, not a stream deadline.

**The retry-disabling key (design §13 to-verify #3, D19).** `total_max_attempts`,
not `max_attempts`, verbatim from `botocore.config.Config`'s own docstring:
`total_max_attempts` "represent[s] the maximum number of **total** attempts...
This includes the initial request, so a value of 1 indicates that no requests
will be retried"; it takes precedence over `max_attempts` when both are given,
and is the one that maps to `AWS_MAX_ATTEMPTS`. `max_attempts` alone counts
retries **excluding** the initial request, which is the opposite shape and is
what a reader gets from boto3's own guide pages if they check only those.

**No credentials offline (D18).** The client is injected and built lazily on
first `invoke`, exactly as `HttpxHtmlFetcher(transport=...)` injects its
transport: constructing a `boto3` client eagerly, in wiring, raises without a
region configured, which would break `uv run pytest` on a clean machine.

**Translation.** Every failure this module can produce is a subclass of
`AgentInvocationError`, never a `botocore` exception, mirroring `adapters/http.py`'s
`FetchError`. An exception type this module did not anticipate (a `RuntimeError`
from a future `boto3` release, say) is deliberately **not** caught here — that is
`AgentBackedComposer`'s `except Exception` to absorb (design D15), not this
seam's to hide, because a fault this module cannot name should not be renamed
into one that misleads.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from rain_alert.adapters.agent_invoker import AgentInvocationError
from rain_alert.domain.values import UnavailableReason

#: design D23's two environment variables. Read here, not through
#: `StaticConfigRepository`/`AlertConfig`: an ARN is an AWS concern, and
#: putting it on `AlertConfig` would widen the frozen stdlib core with
#: infrastructure (D1). Precedent: `DEFAULT_TIMEOUT` lives in `adapters/http.py`,
#: and the Open-Meteo endpoint lives in its own adapter.
AGENT_RUNTIME_ARN_ENV_VAR = "RAIN_ALERT_AGENT_RUNTIME_ARN"
AGENT_TIMEOUT_ENV_VAR = "RAIN_ALERT_AGENT_TIMEOUT_S"

#: 10 s wall clock, one attempt, no retry (D19). `read_timeout` bounds each
#: socket read, not total wall clock; the two coincide here because this
#: application's responses are never streamed (see the module docstring).
CONNECT_TIMEOUT = 3.0
READ_TIMEOUT = 10.0

#: A multi-megabyte body is refused before it is parsed (design section 8,
#: non-capability 8). 1 MiB is comfortably above anything
#: `agent/models.py::CompositionOutput` can produce — the validator's own
#: caps (`MAX_TITLE_LENGTH`, `MAX_BODY_LENGTH`) hold both fields to a few
#: thousand characters — and far below what a malfunctioning runtime could
#: otherwise hand to `json.loads`.
MAX_RESPONSE_BYTES = 1_048_576

_SERVICE_NAME = "bedrock-agentcore"
_JSON_CONTENT_TYPE = "application/json"

#: The one key `agent/app.py::PROMPT_KEY` reads. Pinned here as well so a
#: change to either side cannot drift silently — `contracts/agent-composition.json`
#: does the same job for the prompt payload and the output contract.
PROMPT_PAYLOAD_KEY = "prompt"


@dataclass(frozen=True, slots=True)
class AgentRuntimeSettings:
    """Where the runtime is, and how long to wait for it (design D19, D23).

    `region` is deliberately absent as a `RAIN_ALERT_*` env var: `boto3`
    already resolves a region from `AWS_REGION`/`AWS_DEFAULT_REGION`/the
    shared config file, and a second, redundant knob would only be one more
    thing that can disagree with it.
    """

    agent_runtime_arn: str
    timeout_seconds: float = READ_TIMEOUT

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> AgentRuntimeSettings:
        """Read from the environment, per design D23. Raises loudly rather
        than falling back to a guess: an operator relying on a deploy step
        that never set the ARN needs to hear about it before the first
        invocation, not after."""
        arn = env.get(AGENT_RUNTIME_ARN_ENV_VAR)
        if not arn:
            raise ValueError(f"{AGENT_RUNTIME_ARN_ENV_VAR} must be set to select the agent composer")
        raw_timeout = env.get(AGENT_TIMEOUT_ENV_VAR)
        if raw_timeout is None:
            return cls(agent_runtime_arn=arn)
        try:
            timeout = float(raw_timeout)
        except ValueError as exc:
            raise ValueError(f"{AGENT_TIMEOUT_ENV_VAR} must be a number of seconds, got {raw_timeout!r}") from exc
        return cls(agent_runtime_arn=arn, timeout_seconds=timeout)


class BedrockAgentCoreInvoker:
    """`AgentInvoker` over `boto3`'s `bedrock-agentcore` data-plane client.

    Args:
        agent_runtime_arn: The deployed runtime's ARN.
        client: Injected for testability (D18). `None` (the default) defers
            construction to the first `invoke` call, so the offline suite
            never builds a real client and never needs a region configured.
        qualifier: The runtime alias/version to invoke. `None` uses the
            service's own default.
        read_timeout: Tunable, not a constant (D19): the first live
            measurements can move it without a code change. Defaults to the
            designed deadline.
    """

    def __init__(
        self,
        agent_runtime_arn: str,
        *,
        client: Any | None = None,
        qualifier: str | None = None,
        read_timeout: float = READ_TIMEOUT,
    ) -> None:
        self._agent_runtime_arn = agent_runtime_arn
        self._qualifier = qualifier
        self._client = client
        self._read_timeout = read_timeout

    @classmethod
    def from_settings(cls, settings: AgentRuntimeSettings, *, client: Any | None = None) -> BedrockAgentCoreInvoker:
        return cls(settings.agent_runtime_arn, client=client, read_timeout=settings.timeout_seconds)

    def _resolved_client(self) -> Any:
        if self._client is None:
            self._client = boto3.client(
                _SERVICE_NAME,
                config=Config(
                    connect_timeout=CONNECT_TIMEOUT,
                    read_timeout=self._read_timeout,
                    retries={"total_max_attempts": 1},
                ),
            )
        return self._client

    def invoke(self, prompt: str) -> Mapping[str, Any]:
        client = self._resolved_client()
        kwargs: dict[str, Any] = {
            "agentRuntimeArn": self._agent_runtime_arn,
            "payload": json.dumps({PROMPT_PAYLOAD_KEY: prompt}).encode("utf-8"),
        }
        if self._qualifier is not None:
            kwargs["qualifier"] = self._qualifier

        try:
            response = client.invoke_agent_runtime(**kwargs)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", type(exc).__name__)
            raise AgentInvocationError(UnavailableReason.BAD_STATUS, f"{code}: {exc}") from exc
        except BotoCoreError as exc:
            # `ConnectTimeoutError` and `ReadTimeoutError` are the only two
            # `BotoCoreError` subclasses this operation can raise for a
            # deadline; everything else in that hierarchy (a DNS failure, a
            # refused connection, a broken pipe) is a transport fault.
            reason = UnavailableReason.TIMEOUT if "Timeout" in type(exc).__name__ else UnavailableReason.TRANSPORT_ERROR
            raise AgentInvocationError(reason, f"{type(exc).__name__}: {exc}") from exc

        status = response.get("statusCode")
        if status is not None and not (200 <= status < 300):
            raise AgentInvocationError(UnavailableReason.BAD_STATUS, f"HTTP {status} from the agent runtime")

        content_type = response.get("contentType")
        if content_type != _JSON_CONTENT_TYPE:
            raise AgentInvocationError(UnavailableReason.MALFORMED_PAYLOAD, f"unsupported contentType {content_type!r}")

        body = self._read_bounded(response["response"])
        try:
            decoded = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise AgentInvocationError(UnavailableReason.MALFORMED_PAYLOAD, f"{type(exc).__name__}: {exc}") from exc
        if not isinstance(decoded, Mapping):
            raise AgentInvocationError(
                UnavailableReason.MALFORMED_PAYLOAD, f"decoded body is a {type(decoded).__name__}, not an object"
            )
        return decoded

    def _read_bounded(self, stream: Any) -> bytes:
        """`stream.read(MAX_RESPONSE_BYTES + 1)`, refused if it fills the cap.

        Reading one byte past the cap is what tells an exactly-sized body
        apart from an oversized one without buffering the oversized body in
        full.
        """
        chunk: bytes = stream.read(MAX_RESPONSE_BYTES + 1)
        if len(chunk) > MAX_RESPONSE_BYTES:
            raise AgentInvocationError(
                UnavailableReason.MALFORMED_PAYLOAD, f"response exceeds the {MAX_RESPONSE_BYTES}-byte cap"
            )
        return chunk
