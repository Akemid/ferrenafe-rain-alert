"""`run_once` — the shared post-dependency sequence both entry points call
(design.md D29; `scheduled-execution` spec, "Both entry points execute the
same post-dependency sequence").
"""

from __future__ import annotations

import io
from datetime import timedelta

from rain_alert.application.run_alert_cycle import CycleResult
from rain_alert.domain.config import AlertConfig
from rain_alert.domain.entities import Forecast, HourlyPoint
from rain_alert.domain.sources import Available
from rain_alert.entrypoints.run_once import SNAPSHOT_FAILURE_PREFIX, run_once
from tests.support.fakes import RecordingPublisher
from tests.support.wiring import DEFAULT_CONFIG, DEFAULT_NOW, build_fake_deps


class RaisingPublisher:
    """Every destination fails eventually. This one always does."""

    def publish(self, document: dict) -> None:
        raise OSError("bucket on fire")


def _authorizing_forecast(hours: int = 24, *, mm_total: float = 29.8, probability_pct: int = 90) -> Available[Forecast]:
    """A forecast that clears `DEFAULT_CONFIG`'s imminent threshold
    (`imminent_mm_24h=20.0`, `imminent_probability_pct=70`), so `execute()`
    actually authorizes and delivers a send — the same pattern
    `test_run_alert_cycle.py::_forecast` uses."""
    mm_per_hour = mm_total / hours
    points = tuple(
        HourlyPoint(at=DEFAULT_NOW + timedelta(hours=h), precipitation_mm=mm_per_hour, probability_pct=probability_pct)
        for h in range(hours)
    )
    return Available(data=Forecast(location=DEFAULT_CONFIG.coordinates, points=points), fetched_at=DEFAULT_NOW)


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
        line appears on the `report` stream (`scheduled-execution` spec,
        "A snapshot-publish failure does not cost the alert, on either
        path").

        `build_fake_deps()`'s calm default never authorizes a send at all
        (fix round 1, item 1 — `deps.notifier.alert_calls` stayed empty
        under the previous version of this test, so the GIVEN this
        scenario names was never actually exercised). `_authorizing_forecast`
        forces a real, delivered send so the precondition holds for real."""
        deps = build_fake_deps(forecast=_authorizing_forecast())
        report = io.StringIO()

        result, config = run_once(deps, RaisingPublisher(), report=report)

        # The precondition: the alert was actually composed and delivered
        # through the notifier before the publish guard ran at all — proven
        # on the shared `CallLog`, not merely inferred from `result.sent`.
        assert result.sent is True
        assert len(deps.notifier.alert_calls) == 1
        assert deps.notifier.log.position_of("notifier", "send_alert") < deps.alerts.log.position_of(
            "alerts", "record_alert"
        )
        assert isinstance(result, CycleResult)
        assert isinstance(config, AlertConfig)
        assert SNAPSHOT_FAILURE_PREFIX in report.getvalue()
        assert "OSError" in report.getvalue()
