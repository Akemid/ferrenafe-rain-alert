"""The producer's LaunchAgent template (design D42, D47).

`man launchd.plist`: a `StartInterval` run that falls during sleep is dropped,
while `StartCalendarInterval` runs are coalesced into one run on wake. So the
template must schedule hourly with `StartCalendarInterval {Minute: 0}` and must
not carry `StartInterval` at all (the two keys are evaluated independently, so
both would double-fire).
"""

from __future__ import annotations

import plistlib
import re
from pathlib import Path
from typing import Any

import pytest

from tests.hygiene.test_repo_hygiene import REPO_ROOT

PLIST = REPO_ROOT / "ops" / "launchd" / "pe.ferrenafe.relay-push.plist"
PLACEHOLDER = re.compile(r"<[a-z][a-z0-9-]*>")
#: launchd does not document tilde expansion for `StandardOutPath`, so the home
#: directory is a placeholder the operator fills in, like the repo path.
LOG_PATH = "<home>/Library/Logs/ferrenafe-relay/push.log"


def _load(path: Path = PLIST) -> dict[str, Any]:
    with path.open("rb") as handle:
        loaded: dict[str, Any] = plistlib.load(handle)
    return loaded


def test_the_template_exists_and_parses_as_a_plist() -> None:
    assert _load()["Label"] == "pe.ferrenafe.relay-push"


def test_it_runs_hourly_on_the_hour_through_a_single_calendar_interval() -> None:
    assert _load()["StartCalendarInterval"] == {"Minute": 0}


def test_start_interval_is_absent_so_runs_missed_in_sleep_are_not_the_schedule() -> None:
    assert "StartInterval" not in _load()


def test_it_pushes_once_at_load() -> None:
    assert _load()["RunAtLoad"] is True


def test_it_runs_the_producer_through_uv_in_the_repo() -> None:
    """`<uv>` is a placeholder: launchd starts jobs with a minimal PATH, so a bare `uv` would not resolve."""
    assert _load()["ProgramArguments"] == [
        "<uv>",
        "run",
        "--frozen",
        "--directory",
        "<repo>",
        "rain-alert-relay-push",
        "--region",
        "us-east-2",
    ]


def test_the_region_is_passed_explicitly_because_botocore_ignores_aws_region() -> None:
    """The explicit `--region` is the producer's region of record (highest precedence in `boto3.Session`).

    The environment variable alone is not enough: see
    `test_botocore_resolves_the_region_from_aws_default_region_not_aws_region`.
    """
    arguments = _load()["ProgramArguments"]

    assert arguments[arguments.index("--region") + 1] == "us-east-2"


def test_the_template_documents_the_intentional_run_at_load_and_log_directory_mode() -> None:
    text = PLIST.read_text(encoding="utf-8")

    assert "RunAtLoad is intentional" in text
    assert "chmod 700" in text


def test_the_environment_names_the_profile_region_and_bucket_placeholder() -> None:
    assert _load()["EnvironmentVariables"] == {
        "AWS_PROFILE": "ferrenafe-relay",
        "AWS_DEFAULT_REGION": "us-east-2",
        "RAIN_ALERT_RELAY_BUCKET": "<bucket>",
    }


def test_botocore_resolves_the_region_from_aws_default_region_not_aws_region(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pins the botocore behaviour that made `AWS_REGION` in this template a silent no-op.

    `boto3.Session().region_name` honours `AWS_DEFAULT_REGION` and the profile, never `AWS_REGION`:
    with `AWS_REGION` alone a profile without a region resolves to `None`, and a profile with one
    keeps it. Observed live in docs/evidence/2026-10-02-agent-composer-switched-on.md. Offline: no
    client is created, nothing connects.
    """
    import boto3

    config = tmp_path / "config"
    config.write_text("[profile noregion]\noutput = json\n", encoding="utf-8")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(config))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "absent"))
    monkeypatch.setenv("AWS_PROFILE", "noregion")
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)

    monkeypatch.setenv("AWS_REGION", "us-east-2")
    assert boto3.Session().region_name is None

    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-2")
    assert boto3.Session().region_name == "us-east-2"


def test_both_streams_go_to_the_push_log() -> None:
    template = _load()

    assert template["StandardOutPath"] == LOG_PATH
    assert template["StandardErrorPath"] == LOG_PATH


def test_every_deployment_specific_value_is_an_angle_bracket_placeholder() -> None:
    template = _load()
    arguments = template["ProgramArguments"]

    assert PLACEHOLDER.fullmatch(arguments[0])
    assert PLACEHOLDER.fullmatch(arguments[arguments.index("--directory") + 1])
    assert template["StandardOutPath"].startswith("<home>/")
    assert PLACEHOLDER.fullmatch(template["EnvironmentVariables"]["RAIN_ALERT_RELAY_BUCKET"])


def test_the_template_carries_no_secret_account_id_or_arn() -> None:
    text = PLIST.read_text(encoding="utf-8")

    assert re.search(r"AKIA[0-9A-Z]{16}", text) is None
    assert re.search(r"\b[0-9]{12}\b", text) is None
    assert "arn:aws" not in text
    for forbidden in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        assert forbidden not in text
