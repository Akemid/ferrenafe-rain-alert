"""`run_once` — the shared post-dependency sequence both entry points call
(design.md D29; `scheduled-execution` spec, "Both entry points execute the
same post-dependency sequence").
"""

from __future__ import annotations

import io

from rain_alert.application.run_alert_cycle import CycleResult
from rain_alert.domain.config import AlertConfig
from rain_alert.entrypoints.run_once import SNAPSHOT_FAILURE_PREFIX, run_once
from tests.support.fakes import RecordingPublisher
from tests.support.wiring import build_fake_deps


class RaisingPublisher:
    """Every destination fails eventually. This one always does."""

    def publish(self, document: dict) -> None:
        raise OSError("bucket on fire")


class TestRunOnceExecutesTheCalibratedSequence:
    def test_run_once_executes_the_calibrated_sequence(self) -> None:
        """`run_once(deps, publisher, report=...)` performs `config.load()`,
        `RunAlertCycle(deps).execute()`, and the forgiving snapshot guard,
        returning `(CycleResult, AlertConfig)`."""
        deps = build_fake_deps()
        publisher = RecordingPublisher()
        report = io.StringIO()

        result, config = run_once(deps, publisher, report=report)

        assert isinstance(result, CycleResult)
        assert isinstance(config, AlertConfig)
        # Twice, not once: `run_once` calls it directly, and `RunAlertCycle.execute()`
        # reads it again for the cycle itself (design.md D14/D29) — unchanged from the
        # pre-extraction behaviour this test pins, not a regression this extraction adds.
        assert deps.config.calls == 2
        assert publisher.documents  # the snapshot was published
        assert report.getvalue() == ""

    def test_a_calm_cycle_still_returns_the_config(self) -> None:
        deps = build_fake_deps()

        result, config = run_once(deps, None, report=io.StringIO())

        assert result.sent is False
        assert config.city_slug == "ferrenafe"


class TestSnapshotPublishFailureDoesNotCostTheAlert:
    def test_snapshot_publish_failure_does_not_cost_the_alert(self) -> None:
        """`publisher.publish` raises after `notifier.send_alert` already
        ran; `run_once` returns normally and the `SNAPSHOT_FAILURE_PREFIX`
        line appears on the `report` stream."""
        deps = build_fake_deps()
        report = io.StringIO()

        result, config = run_once(deps, RaisingPublisher(), report=report)

        assert isinstance(result, CycleResult)
        assert isinstance(config, AlertConfig)
        assert SNAPSHOT_FAILURE_PREFIX in report.getvalue()
        assert "OSError" in report.getvalue()
