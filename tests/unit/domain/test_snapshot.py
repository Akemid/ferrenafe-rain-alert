"""Tests for `build_snapshot` — the document the public status page reads
(design 2026-09-21).

Pure function under test: no I/O, no clock, no AWS. `quiet_cycle_result()`
and `alerting_cycle_result()` build a real `CycleResult` by running
`RunAlertCycle` over `tests.support.wiring.build_fake_deps()`, so these
fixtures cannot drift from what the cycle actually produces.
"""

from __future__ import annotations

import json
from datetime import timedelta

from rain_alert.application.run_alert_cycle import CycleResult, RunAlertCycle
from rain_alert.domain.config import AlertConfig
from rain_alert.domain.entities import Warning
from rain_alert.domain.snapshot import SNAPSHOT_SCHEMA_VERSION, build_snapshot
from rain_alert.domain.sources import Available
from rain_alert.domain.values import TimeWindow, WarningLevel
from tests.support.wiring import DEFAULT_CONFIG, DEFAULT_NOW, build_fake_deps


def config() -> AlertConfig:
    return DEFAULT_CONFIG


def quiet_cycle_result() -> CycleResult:
    """No warnings, a calm forecast: nothing clears any threshold, so the
    evaluator returns `Level.NONE` and `should_send` never authorizes a
    send for it (`domain/dedup.py`)."""
    return RunAlertCycle(build_fake_deps()).execute()


def alerting_cycle_result() -> CycleResult:
    """One official orange warning over the coast is, on its own, enough to
    raise `Level.IMMINENT` (`RiskEvaluator._imminent_from_official`) and
    authorize a send against an empty dedup history."""
    warning = Warning(
        source_id="335",
        title="PRECIPITACIONES EN LA COSTA",
        level=WarningLevel.ORANGE,
        region="Lambayeque",
        window=TimeWindow(start=DEFAULT_NOW, end=DEFAULT_NOW + timedelta(hours=48)),
        emitted_at=DEFAULT_NOW,
    )
    deps = build_fake_deps(warnings=Available(data=(warning,), fetched_at=DEFAULT_NOW))
    return RunAlertCycle(deps).execute()


class TestTheSnapshotSaysWhatThePageShows:
    def test_a_cycle_that_did_not_alert_still_publishes_a_level(self) -> None:
        """The whole point: "no risk today" is the answer a resident usually
        needs, and the system currently has no way to say it."""
        document = build_snapshot(quiet_cycle_result(), config(), recent=())

        assert document["schema_version"] == SNAPSHOT_SCHEMA_VERSION
        assert document["level"] == "none"
        assert document["level_label"] == "sin riesgo"
        assert document["alert"] is None

    def test_reasons_are_published_in_spanish(self) -> None:
        document = build_snapshot(alerting_cycle_result(), config(), recent=())

        assert document["reasons"] == (["Aviso oficial del SENAMHI, nivel naranja: PRECIPITACIONES EN LA COSTA."])

    def test_an_alert_carries_which_composer_wrote_it(self) -> None:
        """Published deliberately. A system that puts a model in front of a
        community should be able to say when it did."""
        document = build_snapshot(alerting_cycle_result(), config(), recent=())

        assert document["alert"]["composed_by"] == "template"

    def test_the_recipient_count_is_never_published(self) -> None:
        """Operational — how many people are subscribed. No resident needs it."""
        document = build_snapshot(alerting_cycle_result(), config(), recent=())

        assert "recipients_count" not in json.dumps(document)

    def test_the_document_is_json_serializable(self) -> None:
        document = build_snapshot(alerting_cycle_result(), config(), recent=())

        assert json.loads(json.dumps(document)) == document
