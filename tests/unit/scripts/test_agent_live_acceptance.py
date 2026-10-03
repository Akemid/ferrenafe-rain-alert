"""Offline tests for `scripts/agent_live_acceptance.py`, the paid live gate.

The script is imported by path and never collected by pytest. Only its pure
core and its runner (driven by hand-written fakes) are exercised here; the
live path (a real AgentCore invocation) is run by the owner, never by tests.
"""

from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "agent_live_acceptance.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("agent_live_acceptance", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # `@dataclass` resolves string annotations through `sys.modules`.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script() -> ModuleType:
    return _load()


def _results(script: ModuleType, accepted: int, total: int = 10) -> list:
    return [script.RunResult(index=i + 1, accepted=i < accepted) for i in range(total)]


class TestTheTally:
    @pytest.mark.parametrize("accepted", [8, 9, 10])
    def test_at_or_above_the_minimum_exits_zero(self, script: ModuleType, accepted: int) -> None:
        line, code = script.tally(_results(script, accepted))

        assert code == 0
        assert f"{accepted}/10" in line

    def test_below_the_minimum_exits_non_zero(self, script: ModuleType) -> None:
        line, code = script.tally(_results(script, 7))

        assert code != 0
        assert "7/10" in line


class TestTheIncidentFixture:
    def test_it_is_the_request_the_live_incident_had(self, script: ModuleType) -> None:
        from datetime import UTC, datetime

        from rain_alert.adapters.local.static_config_repository import CHECKLIST
        from rain_alert.domain.values import WarningLevel

        request = script.incident_request()

        assert request.warning is not None
        assert request.warning.source_id == "392"
        assert request.warning.level is WarningLevel.ORANGE
        assert request.warning.title == "PRECIPITACIONES EN LA SIERRA NORTE Y COSTA NORTE"
        assert request.window.start == datetime(2026, 10, 2, 5, tzinfo=UTC)
        assert request.window.end == datetime(2026, 10, 5, 5, tzinfo=UTC)
        assert request.forecast is None
        assert request.checklist == CHECKLIST


ARN = "arn:aws:bedrock-agentcore:us-east-2:<account>:runtime/secret-runtime-id"
TOKEN = "f00dfacef00dfacef00dfacef00dface"


def _good_draft(script: ModuleType) -> dict[str, str]:
    from rain_alert.domain.template import MessageComposer

    request = script.incident_request()
    message = MessageComposer().compose(request)
    return {
        "title": message.title,
        "body": message.body,
        "level": message.level.value,
        "valid_until": message.valid_until.isoformat(),
    }


class _ScriptedInvoker:
    """Hand-written fake: returns each scripted outcome in turn (a mapping, or an exception to raise)."""

    def __init__(self, outcomes: list) -> None:
        self._outcomes = list(outcomes)
        self.prompts: list[str] = []

    def invoke(self, prompt: str):
        self.prompts.append(prompt)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _run(script: ModuleType, outcomes: list, **kwargs):
    invoker = _ScriptedInvoker(outcomes)
    out = io.StringIO()
    code = script.run_gate(runs=len(outcomes), invoker=invoker, out=out, token_factory=lambda: TOKEN, **kwargs)
    return code, out.getvalue(), invoker


class TestTheRunner:
    def test_it_prints_one_line_per_run_and_the_tally(self, script: ModuleType) -> None:
        bad = {**_good_draft(script), "body": "Aviso naranja entre el 2 y el 5 de octubre."}
        code, text, _ = _run(script, [_good_draft(script)] * 8 + [bad, bad])

        lines = text.splitlines()
        run_lines = [line for line in lines if line.startswith("run ")]
        assert len(run_lines) == 10
        assert "ACCEPTED" in run_lines[0]
        assert "REJECTED" in run_lines[9]
        assert "unknown_number" in run_lines[9]
        assert any("8/10" in line for line in lines)
        assert code == 0

    def test_the_unchanged_validator_decides_below_the_minimum(self, script: ModuleType) -> None:
        bad = {**_good_draft(script), "body": "Aviso naranja entre el 2 y el 5 de octubre."}

        code, text, _ = _run(script, [_good_draft(script)] * 7 + [bad] * 3)

        assert code != 0
        assert "7/10" in text

    def test_an_invocation_error_is_a_failed_run_not_a_crash(self, script: ModuleType) -> None:
        from rain_alert.adapters.agent_invoker import AgentInvocationError
        from rain_alert.domain.values import UnavailableReason

        boom = AgentInvocationError(UnavailableReason.TIMEOUT, "read timeout")

        code, text, invoker = _run(script, [boom, _good_draft(script)])

        assert len(invoker.prompts) == 2
        assert "REJECTED" not in text.splitlines()[0]
        assert "ERROR" in text.splitlines()[0]
        assert "timeout" in text.splitlines()[0]
        assert code != 0

    def test_a_malformed_draft_is_a_failed_run(self, script: ModuleType) -> None:
        _, text, _ = _run(script, [{"title": "only a title"}])

        assert "malformed" in text.splitlines()[0]


class TestSecrecy:
    def test_neither_the_arn_nor_the_fence_token_is_printed(
        self, script: ModuleType, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from rain_alert.adapters.agent_invoker import AgentInvocationError
        from rain_alert.domain.values import UnavailableReason

        leaky_error = AgentInvocationError(UnavailableReason.BAD_STATUS, f"AccessDenied on {ARN} token {TOKEN}")
        echo = {**_good_draft(script), "body": f"{TOKEN} {ARN}"}

        _, text, _ = _run(script, [leaky_error, echo, _good_draft(script)], secrets=(ARN,))

        captured = capsys.readouterr()
        for stream in (text, captured.out, captured.err):
            assert ARN not in stream
            assert "secret-runtime-id" not in stream
            assert TOKEN not in stream

    def test_a_server_side_arn_the_script_was_not_told_about_is_masked_too(self, script: ModuleType) -> None:
        from rain_alert.adapters.agent_invoker import AgentInvocationError
        from rain_alert.domain.values import UnavailableReason

        other = "arn:aws:iam::<account>:role/other-thing"

        _, text, _ = _run(script, [AgentInvocationError(UnavailableReason.BAD_STATUS, f"denied for {other}")])

        assert other not in text


class TestItStaysOutOfTheOfflineSuite:
    def test_it_is_outside_pytests_collection(self) -> None:
        import tomllib

        root = SCRIPT.parents[1]
        config = tomllib.loads((root / "pyproject.toml").read_text())["tool"]["pytest"]["ini_options"]

        testpaths = [(root / path).resolve() for path in config["testpaths"]]
        assert not any(path == SCRIPT.parent or SCRIPT.parent.is_relative_to(path) for path in testpaths)
        assert not SCRIPT.name.startswith("test_")

    def test_importing_it_builds_no_boto3_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import boto3

        built: list[str] = []

        def _spy(service: str, *args: object, **kwargs: object) -> None:
            built.append(service)
            raise AssertionError("a client was built at import time")

        monkeypatch.setattr(boto3, "client", _spy)
        monkeypatch.setattr(boto3, "Session", _spy)

        _load()

        assert built == []

    def test_help_exits_zero_offline(self, script: ModuleType, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as raised:
            script.main(["--help"], env={})

        assert raised.value.code == 0
        assert "LIVE AND PAID" in capsys.readouterr().out

    def test_without_an_arn_it_refuses_before_any_client_and_says_how_to_fix_it(
        self, script: ModuleType, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = script.main([], env={})

        assert code == 2
        assert "RAIN_ALERT_AGENT_RUNTIME_ARN" in capsys.readouterr().err


class TestMaskingAnAccountId:
    def test_a_bare_twelve_digit_account_id_outside_an_arn_is_masked(self, script: ModuleType) -> None:
        account = "1" * 12

        masked = script._mask(f"User {account} is not authorized; role of account {account}.", ())

        assert account not in masked
        assert "<account>" in masked

    def test_a_longer_digit_run_is_left_alone(self, script: ModuleType) -> None:
        run = "1" * 13

        assert script._mask(f"id {run}", ()) == f"id {run}"
