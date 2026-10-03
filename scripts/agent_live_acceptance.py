"""Live acceptance gate for the agent system prompt (design D52, D53).

LIVE AND PAID: every run is a real Bedrock AgentCore invocation (a model call)
against the deployed runtime. It is outside pytest's collection and nothing
imports it; the owner runs it by hand after `agentcore launch`.

    AWS_PROFILE=ferrenafe AWS_DEFAULT_REGION=us-east-2 \\
    RAIN_ALERT_AGENT_RUNTIME_ARN=<arn> \\
        uv run python scripts/agent_live_acceptance.py --runs 10

Without `RAIN_ALERT_AGENT_RUNTIME_ARN`, `--from-lambda` reads it from the
deployed cycle Lambda's configuration instead. The ARN is never hard-coded and
never printed, and neither is any fence token.

Each run goes through the same code the cycle uses: `build_prompt`, the
invoker, `parse_candidate` and the UNCHANGED `validate_message`. The validator
decides acceptance; this script only counts. Exit status is 0 at 8 of 10
accepted or better, non-zero below. Record all outcomes, failures included
(D53): no cherry-picking.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, TextIO

from rain_alert.adapters.agent_composer import parse_candidate
from rain_alert.adapters.agent_invoker import AgentInvocationError
from rain_alert.adapters.agent_prompt import _fence_token, build_prompt
from rain_alert.adapters.agentcore_invoker import AGENT_RUNTIME_ARN_ENV_VAR
from rain_alert.adapters.local.static_config_repository import CHECKLIST
from rain_alert.domain.message_validation import validate_message
from rain_alert.domain.messages import AlertMessage, MessageRequest, WarningSummary
from rain_alert.domain.reasons import WarningReason
from rain_alert.domain.values import Level, TimeWindow, WarningLevel

MINIMUM_ACCEPTED = 8
DEFAULT_RUNS = 10
DEFAULT_REGION = "us-east-2"
LAMBDA_NAME = "ferrenafe-rain-alert-cycle"

_CITY = "Ferreñafe"
_TITLE = "PRECIPITACIONES EN LA SIERRA NORTE Y COSTA NORTE"
_WINDOW = TimeWindow(
    start=datetime(2026, 10, 2, 5, tzinfo=UTC),
    end=datetime(2026, 10, 5, 5, tzinfo=UTC),
)


@dataclass(frozen=True, slots=True)
class RunResult:
    """One live run. `rules` are validator rule ids; `error` is set when the
    invocation itself failed."""

    index: int
    accepted: bool
    rules: tuple[str, ...] = ()
    title: str = ""
    body: str = ""
    error: str | None = None


def incident_request() -> MessageRequest:
    """The request of the 2026-10 incident: imminent, orange warning 392,
    no forecast, the real checklist."""
    return MessageRequest(
        city=_CITY,
        timezone="America/Lima",
        level=Level.IMMINENT,
        window=_WINDOW,
        reasons=(WarningReason(level=WarningLevel.ORANGE, title=_TITLE),),
        forecast=None,
        warning=WarningSummary(source_id="392", level=WarningLevel.ORANGE, title=_TITLE, window=_WINDOW),
        senamhi_status="available",
        open_meteo_status="available",
        checklist=CHECKLIST,
    )


def tally(results: Sequence[RunResult], minimum: int = MINIMUM_ACCEPTED) -> tuple[str, int]:
    """The `k/n accepted` line and the process exit code (0 at or above `minimum`)."""
    accepted = sum(1 for result in results if result.accepted)
    verdict = "PASS" if accepted >= minimum else "FAIL"
    return f"{accepted}/{len(results)} accepted (minimum {minimum}): {verdict}", 0 if accepted >= minimum else 1


class _Invoker(Protocol):
    def invoke(self, prompt: str) -> Mapping[str, Any]: ...


#: Any ARN, not just the one this script was given: an AWS error message can
#: quote a role or a runtime the script never saw.
_ARN_PATTERN = re.compile(r"arn:aws[a-z-]*:[^\s\"']+")
_MASK = "<masked>"
#: A bare account id outside an ARN, e.g. in "User <id> is not authorized".
_ACCOUNT_PATTERN = re.compile(r"\b\d{12}\b")
_ACCOUNT_MASK = "<account>"


def _mask(text: str, secrets: Sequence[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, _MASK)
    return _ACCOUNT_PATTERN.sub(_ACCOUNT_MASK, _ARN_PATTERN.sub(_MASK, text))


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _attempt(
    index: int,
    request: MessageRequest,
    invoker: _Invoker,
    token: str,
    parse: Callable[[object], AlertMessage | None],
    validate: Callable[[MessageRequest, AlertMessage], Any],
) -> RunResult:
    try:
        draft = invoker.invoke(build_prompt(request, token_factory=lambda: token))
        candidate = parse(draft)
        if candidate is None:
            return RunResult(index=index, accepted=False, error="malformed draft")
        violations = validate(request, candidate)
    except AgentInvocationError as exc:
        return RunResult(index=index, accepted=False, error=f"{exc.reason.value}: {exc.detail}")
    except Exception as exc:  # noqa: BLE001 - a failed run is a result, not a crash
        return RunResult(index=index, accepted=False, error=f"{type(exc).__name__}: {exc}")
    return RunResult(
        index=index,
        accepted=not violations,
        rules=tuple(violation.rule.value for violation in violations),
        title=candidate.title,
        body=candidate.body,
    )


def run_gate(
    *,
    runs: int,
    invoker: _Invoker,
    out: TextIO,
    secrets: Sequence[str] = (),
    token_factory: Callable[[], str] = _fence_token,
    parse: Callable[[object], AlertMessage | None] = parse_candidate,
    validate: Callable[[MessageRequest, AlertMessage], Any] = validate_message,
    minimum: int = MINIMUM_ACCEPTED,
) -> int:
    """Run the gate `runs` times, print every outcome, return the exit code.

    Everything printed passes through `_mask` with the fence token of that run
    and the configured secrets, so neither the runtime ARN nor a token can
    leave through a draft, an error detail, or this function's own output.
    """
    request = incident_request()
    results: list[RunResult] = []
    for index in range(1, runs + 1):
        token = token_factory()
        result = _attempt(index, request, invoker, token, parse, validate)
        results.append(result)
        mask = (*secrets, token)
        if result.error is not None:
            line = f"run {index:02d}: ERROR {result.error}"
        else:
            verdict = "ACCEPTED" if result.accepted else "REJECTED"
            rules = ",".join(result.rules) or "-"
            line = f"run {index:02d}: {verdict} rules=[{rules}] title={result.title!r} body={result.body!r}"
        print(_mask(_one_line(line), mask), file=out)
    line, code = tally(results, minimum)
    print(line, file=out)
    return code


def _arn_from_lambda(region: str) -> str:
    """The runtime ARN from the deployed cycle Lambda's environment."""
    import boto3  # lazy: importing this module builds no client

    client = boto3.client("lambda", region_name=region)
    config = client.get_function_configuration(FunctionName=LAMBDA_NAME)
    arn = config.get("Environment", {}).get("Variables", {}).get(AGENT_RUNTIME_ARN_ENV_VAR)
    if not arn:
        raise SystemExit(f"the Lambda has no {AGENT_RUNTIME_ARN_ENV_VAR} configured")
    return str(arn)


def _live_invoker(arn: str, region: str) -> _Invoker:
    """The real invoker, with the region passed explicitly (botocore ignores AWS_REGION)."""
    import boto3
    from botocore.config import Config

    from rain_alert.adapters.agentcore_invoker import (
        CONNECT_TIMEOUT,
        READ_TIMEOUT,
        BedrockAgentCoreInvoker,
    )

    client = boto3.client(
        "bedrock-agentcore",
        region_name=region,
        config=Config(connect_timeout=CONNECT_TIMEOUT, read_timeout=READ_TIMEOUT, retries={"total_max_attempts": 1}),
    )
    return BedrockAgentCoreInvoker(arn, client=client)


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    environment = os.environ if env is None else env
    parser = argparse.ArgumentParser(
        description="LIVE AND PAID: run the incident request through the deployed agent and count validator acceptances."
    )
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS, help="number of live invocations (default 10)")
    parser.add_argument(
        "--from-lambda",
        action="store_true",
        help=f"read the runtime ARN from the deployed Lambda instead of {AGENT_RUNTIME_ARN_ENV_VAR}",
    )
    args = parser.parse_args(argv)

    region = environment.get("AWS_DEFAULT_REGION", DEFAULT_REGION)
    arn = environment.get(AGENT_RUNTIME_ARN_ENV_VAR)
    if not arn and args.from_lambda:
        arn = _arn_from_lambda(region)
    if not arn:
        print(f"set {AGENT_RUNTIME_ARN_ENV_VAR} or pass --from-lambda", file=sys.stderr)
        return 2
    return run_gate(runs=args.runs, invoker=_live_invoker(arn, region), out=sys.stdout, secrets=(arn,))


if __name__ == "__main__":
    raise SystemExit(main())
