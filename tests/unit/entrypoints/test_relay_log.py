"""The cloud `on_read` writer (design D40): one compact JSON line per read.

Lambda's Python runtime ships `print` output as plain text and multi-line
splitting is undocumented (V.4), so the record must be exactly one line, and
nothing S3-supplied may break out of it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

import rain_alert.entrypoints.wiring as wiring
from rain_alert.adapters.s3_relay_reader import RELAY_BUCKET_ENV_VAR
from rain_alert.adapters.senamhi_relay import RelayReadRecord
from rain_alert.domain.values import ComposerName, UnavailableReason
from tests.unit.entrypoints.test_wiring import NOW, _config, _FakeSsmConfigRepository

MODIFIED = datetime(2026, 10, 2, 5, 0, tzinfo=UTC)


def _record(version_id: str | None = "ver-1", **overrides: object) -> RelayReadRecord:
    fields: dict[str, object] = {
        "degraded": 0,
        "reason": None,
        "version_id": version_id,
        "last_modified": MODIFIED,
        "age_seconds": 120,
        "skew_seconds": -30,
    }
    fields.update(overrides)
    return RelayReadRecord(**fields)  # type: ignore[arg-type]


def test_a_healthy_read_prints_exactly_one_compact_json_line(capsys: pytest.CaptureFixture[str]) -> None:
    wiring.log_relay_read(_record())

    out = capsys.readouterr().out
    assert out.count("\n") == 1 and out.endswith("\n")
    assert ", " not in out and ": " not in out
    assert json.loads(out) == {
        "event": "senamhi_relay",
        "degraded": 0,
        "reason": None,
        "version_id": "ver-1",
        "last_modified": "2026-10-02T05:00:00Z",
        "age_seconds": 120,
        "skew_seconds": -30,
    }


def test_a_degraded_read_carries_the_reason_value(capsys: pytest.CaptureFixture[str]) -> None:
    record = _record(
        degraded=1, reason=UnavailableReason.RELAY_MISSING, version_id=None, last_modified=None, age_seconds=None
    )

    wiring.log_relay_read(record)

    payload = json.loads(capsys.readouterr().out)
    assert payload["degraded"] == 1
    assert payload["reason"] == "relay_missing"
    assert payload["last_modified"] is None


def test_a_hostile_version_id_cannot_forge_or_split_a_record(capsys: pytest.CaptureFixture[str]) -> None:
    hostile = 'v1\n{"event":"senamhi_relay","degraded":0}\r ' + "x" * 500

    wiring.log_relay_read(_record(version_id=hostile))

    out = capsys.readouterr().out
    assert out.count("\n") == 1
    assert "\r" not in out and " " not in out
    payload = json.loads(out)
    assert payload["degraded"] == 0 and payload["event"] == "senamhi_relay"
    assert "\n" not in payload["version_id"]
    assert len(payload["version_id"]) <= 100


def test_a_naive_last_modified_never_breaks_the_writer(capsys: pytest.CaptureFixture[str]) -> None:
    wiring.log_relay_read(_record(last_modified=datetime(2026, 10, 2, 5, 0)))

    assert json.loads(capsys.readouterr().out)["event"] == "senamhi_relay"


def test_build_cloud_deps_wires_the_writer_as_on_read(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(wiring, "SsmConfigRepository", lambda: _FakeSsmConfigRepository(_config(ComposerName.TEMPLATE)))
    deps = wiring.build_cloud_deps({RELAY_BUCKET_ENV_VAR: ""})

    deps.warnings.fetch_current_warnings("lambayeque", NOW)

    payload = json.loads(capsys.readouterr().out)
    assert payload["event"] == "senamhi_relay"
    assert payload["degraded"] == 1
    assert payload["reason"] == "relay_unreadable"
