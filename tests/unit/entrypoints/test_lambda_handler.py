"""`lambda_handler.handler` — the Lambda entry point for the six-hourly
scheduled cycle (design.md D29; `scheduled-execution` spec).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

import rain_alert.entrypoints.lambda_handler as lambda_handler
from rain_alert.domain.sources import Unavailable
from rain_alert.domain.values import SourceName, UnavailableReason
from tests.support.fakes import RecordingPublisher
from tests.support.wiring import DEFAULT_NOW, build_fake_deps


class _RaisingBuildCloudDeps:
    """Stands in for a `build_cloud_deps` that cannot construct the graph
    (a missing or unparseable SSM parameter, a missing agent ARN)."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    def __call__(self) -> Any:
        raise self._error


class TestHandlerIgnoresEventPayload:
    def test_handler_ignores_event_payload(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A hostile `event` dict and an empty `event` dict produce an
        identical `CycleResult` and logged document."""
        deps = build_fake_deps()
        monkeypatch.setattr(lambda_handler, "build_cloud_deps", lambda: deps)
        monkeypatch.setattr(lambda_handler, "select_snapshot_publisher", lambda env: None)

        lambda_handler.handler({}, None)
        empty_document = capsys.readouterr().out

        lambda_handler.handler(
            {"maliciousKey": "'; DROP TABLE alerts;--", "composer": "agent", "now": "2000-01-01T00:00:00Z"}, None
        )
        hostile_document = capsys.readouterr().out

        assert empty_document == hostile_document
        assert empty_document  # something was actually logged


class TestHandlerDoesNotCatchConstructionFailures:
    def test_handler_does_not_catch_construction_failures(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A faked `build_cloud_deps` that raises propagates out of `handler`
        uncaught; no `CycleResult`, no logged document, no publish attempt."""
        monkeypatch.setattr(
            lambda_handler, "build_cloud_deps", _RaisingBuildCloudDeps(ValueError("SSM parameter missing"))
        )

        with pytest.raises(ValueError, match="SSM parameter missing"):
            lambda_handler.handler({}, None)

        assert capsys.readouterr().out == ""


class TestHandlerLogsAsJsonForEveryCompletedOutcome:
    @pytest.mark.parametrize(
        "deps_factory",
        [
            pytest.param(lambda: build_fake_deps(), id="none"),
            pytest.param(
                lambda: build_fake_deps(
                    warnings=Unavailable(
                        source=SourceName.SENAMHI,
                        reason=UnavailableReason.STRUCTURE_UNRECOGNIZED,
                        detail="broken page",
                        observed_at=DEFAULT_NOW,
                    )
                ),
                id="degraded",
            ),
        ],
    )
    def test_handler_logs_as_json_for_every_completed_outcome(
        self,
        deps_factory: Any,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """One `as_json` line to stdout, `handler` never raises for a
        completed cycle."""
        deps = deps_factory()
        monkeypatch.setattr(lambda_handler, "build_cloud_deps", lambda: deps)
        monkeypatch.setattr(lambda_handler, "select_snapshot_publisher", lambda env: None)

        summary = lambda_handler.handler({}, None)

        out = capsys.readouterr().out
        lines = [line for line in out.splitlines() if line.strip()]
        document = json.loads(out)
        assert document["sent"] is False
        assert isinstance(summary, dict)
        assert summary["sent"] is False
        # One JSON document, not one line per bracket: `json.dumps(indent=2)`
        # is multi-line, so "one as_json line" means one *document*, parsed
        # whole — not literally `len(lines) == 1`.
        assert lines[0] == "{"

    def test_a_deduplicated_cycle_still_logs_a_document(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A standing `prepare` already recorded for the current window: the
        handler runs a second time within that window and logs a document
        with `sent: false`, without raising."""
        from rain_alert.domain.messages import AlertMessage, AlertRecord
        from rain_alert.domain.values import ComposerName, Level, TimeWindow

        now = datetime(2026, 9, 3, 12, tzinfo=UTC)
        window = TimeWindow(start=now - timedelta(hours=1), end=now + timedelta(hours=47))
        prior = AlertRecord(
            city_slug="ferrenafe",
            level=Level.PREPARE,
            window=window,
            sent_at=now - timedelta(hours=1),
            message=AlertMessage(title="t", body="b", level=Level.PREPARE, valid_until=now),
            reasons=(),
            senamhi_status="available",
            open_meteo_status="available",
            composer=ComposerName.TEMPLATE.value,
        )
        deps = build_fake_deps(now=now, prior_alerts=[prior])
        monkeypatch.setattr(lambda_handler, "build_cloud_deps", lambda: deps)
        monkeypatch.setattr(lambda_handler, "select_snapshot_publisher", lambda env: None)

        lambda_handler.handler({}, None)

        document = json.loads(capsys.readouterr().out)
        assert document["sent"] is False


class TestDedupWindowDerivesFromActualClock:
    def test_dedup_window_derives_from_actual_clock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Two `handler` invocations spaced other than six hours apart still
        derive the dedup query window from `now`, never a hardcoded period
        constant.

        A publisher is wired in (`RecordingPublisher`) so `run_once`'s
        `_recent_alerts` call actually runs — it is skipped entirely when
        the publisher is `None` (`run_once.py`)."""
        monkeypatch.setattr(lambda_handler, "select_snapshot_publisher", lambda env: RecordingPublisher())

        first_now = datetime(2026, 9, 3, 12, tzinfo=UTC)
        first_deps = build_fake_deps(now=first_now)
        monkeypatch.setattr(lambda_handler, "build_cloud_deps", lambda: first_deps)
        lambda_handler.handler({}, None)

        second_now = first_now + timedelta(hours=2)  # deliberately not 6h
        second_deps = build_fake_deps(now=second_now)
        monkeypatch.setattr(lambda_handler, "build_cloud_deps", lambda: second_deps)
        lambda_handler.handler({}, None)

        # `query_calls[0]` is `AlertPolicy`'s own dedup check
        # (`application/policies.py`), bounded by `assessment.window.end`
        # (the forecast horizon), not the cycle clock directly — this test
        # is about `_recent_alerts`'s query (`run_once.py`), the one bounded
        # by `now` itself, so it reads the *last* recorded call: the one
        # made after `RunAlertCycle.execute()` has already returned.
        first_call = first_deps.alerts.query_calls[-1]
        second_call = second_deps.alerts.query_calls[-1]
        assert first_call[2] == first_now
        assert second_call[2] == second_now
        assert second_call[2] - first_call[2] == timedelta(hours=2)
