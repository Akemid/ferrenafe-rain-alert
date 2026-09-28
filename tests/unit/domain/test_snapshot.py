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
from pathlib import Path
from typing import Any

from rain_alert.application.dependencies import CycleDependencies
from rain_alert.application.run_alert_cycle import CycleResult, RunAlertCycle
from rain_alert.domain.config import AlertConfig
from rain_alert.domain.entities import Warning
from rain_alert.domain.messages import AlertRecord
from rain_alert.domain.snapshot import SNAPSHOT_SCHEMA_VERSION, build_snapshot
from rain_alert.domain.sources import Available, Unavailable, status_of
from rain_alert.domain.values import Level, SourceName, TimeWindow, UnavailableReason, WarningLevel
from tests.support.wiring import DEFAULT_CONFIG, DEFAULT_NOW, build_fake_deps

# tests/unit/domain/test_snapshot.py -> parents[3] is the repo root (same
# convention as tests/hygiene/test_repo_hygiene.py and
# tests/architecture/test_layer_boundaries.py). Resolving through
# `__file__` rather than a bare relative path means the golden-contract test
# below passes regardless of the working directory `uv run pytest` is
# invoked from.
REPO_ROOT = Path(__file__).resolve().parents[3]


def config() -> AlertConfig:
    return DEFAULT_CONFIG


def quiet_cycle_result() -> CycleResult:
    """No warnings, a calm forecast: nothing clears any threshold, so the
    evaluator returns `Level.NONE` and `should_send` never authorizes a
    send for it (`domain/dedup.py`)."""
    return RunAlertCycle(build_fake_deps()).execute()


def _alerting_cycle_deps() -> CycleDependencies:
    """The dependency graph behind `alerting_cycle_result()`, exposed so a
    test can read back what `RunAlertCycle` actually wrote through it (e.g.
    the `AlertRecord` its own `AlertRepository.record_alert` call stored)."""
    warning = Warning(
        source_id="335",
        title="PRECIPITACIONES EN LA COSTA",
        level=WarningLevel.ORANGE,
        region="Lambayeque",
        window=TimeWindow(start=DEFAULT_NOW, end=DEFAULT_NOW + timedelta(hours=48)),
        emitted_at=DEFAULT_NOW,
    )
    return build_fake_deps(warnings=Available(data=(warning,), fetched_at=DEFAULT_NOW))


def alerting_cycle_result() -> CycleResult:
    """One official orange warning over the coast is, on its own, enough to
    raise `Level.IMMINENT` (`RiskEvaluator._imminent_from_official`) and
    authorize a send against an empty dedup history."""
    return RunAlertCycle(_alerting_cycle_deps()).execute()


def sent_alert_record() -> AlertRecord:
    """A genuine `AlertRecord`, exactly as `RunAlertCycle` wrote it via
    `AlertRepository.record_alert` — not a hand-built stand-in that could
    drift from what the cycle actually records (design 2026-09-21, task 2
    fix round 1). Read back through the port's own query method, not a
    fake-only convenience attribute, so this fixture only relies on the
    `AlertRepository` contract every adapter must satisfy.
    """
    deps = _alerting_cycle_deps()
    RunAlertCycle(deps).execute()
    records = deps.alerts.alerts_with_window_start_between(
        DEFAULT_CONFIG.city_slug, DEFAULT_NOW - timedelta(days=365), DEFAULT_NOW + timedelta(days=365)
    )
    (record,) = records
    return record


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


class TestEveryPublishedInstantIsConvertedToTheConfiguredLocalTimezone:
    """Fix round 1, finding 1. `snapshot.py` routes every timestamp through
    `_local()`, which calls `.astimezone()` before `.isoformat()` — but no
    prior test asserted on the *rendered value*, only on fields unaffected by
    it. A regression that dropped `.astimezone()` and published `evaluated_at`
    as raw UTC (`...+00:00` instead of `...-05:00`) would have passed all
    five original tests: this task's own stated risk is a resident reading
    the wrong hour for a flood warning, so the rendered offset itself has to
    be asserted, not merely the field's presence.

    `America/Lima` carries no DST (UTC-5 year-round), so the expected offset
    below is not a seasonal accident of the fixed `DEFAULT_NOW`.
    """

    def test_evaluated_at_is_rendered_in_the_configured_local_offset(self) -> None:
        document = build_snapshot(quiet_cycle_result(), config(), recent=())

        assert document["evaluated_at"] == "2026-09-03T07:00:00-05:00"

    def test_the_window_bounds_are_rendered_in_the_configured_local_offset(self) -> None:
        document = build_snapshot(quiet_cycle_result(), config(), recent=())

        assert document["window"]["start"] == "2026-09-03T07:00:00-05:00"
        assert document["window"]["end"] == "2026-09-05T07:00:00-05:00"

    def test_a_recent_alerts_sent_at_is_rendered_in_the_configured_local_offset(self) -> None:
        record = sent_alert_record()

        document = build_snapshot(quiet_cycle_result(), config(), recent=(record,))

        assert document["recent_alerts"][0]["sent_at"] == "2026-09-03T07:00:00-05:00"


class TestRecentAlertsArePublishedFromWhatWasActuallySent:
    """Fix round 1, finding 2. All five original tests pass `recent=()`, so
    the per-item transformation at the end of `build_snapshot` — level,
    localized `sent_at`, title, `composed_by` — had no coverage at all: a
    wrong attribute or a typo there failed nothing. `sent_alert_record()`
    supplies a genuine `AlertRecord`, read back through the real
    `AlertRepository` port after a real `RunAlertCycle.execute()` call, for
    the same reason `alerting_cycle_result()` runs a real cycle instead of
    hand-building a `CycleResult`.
    """

    def test_a_recent_alert_is_rendered_with_its_real_fields(self) -> None:
        record = sent_alert_record()

        document = build_snapshot(quiet_cycle_result(), config(), recent=(record,))

        assert document["recent_alerts"] == [
            {
                "level": "imminent",
                "sent_at": "2026-09-03T07:00:00-05:00",
                "title": record.message.title,
                "composed_by": "template",
            }
        ]

    def test_multiple_recent_alerts_are_all_published_in_order(self) -> None:
        record = sent_alert_record()

        document = build_snapshot(quiet_cycle_result(), config(), recent=(record, record))

        assert len(document["recent_alerts"]) == 2

    def test_recent_alerts_are_json_serializable(self) -> None:
        record = sent_alert_record()

        document = build_snapshot(quiet_cycle_result(), config(), recent=(record,))

        assert json.loads(json.dumps(document)) == document


class TestThePublishedKeysMatchTheGoldenContract:
    """`contracts/public-snapshot.json` is asserted from both sides. The web
    suite asserts the page reads exactly these; this asserts the publisher
    writes exactly these.

    All three golden lists are exercised here, including `recent_alert` —
    covered with a genuine `AlertRecord` rather than `recent=()`, which would
    leave that third of the contract unchecked (design 2026-09-21, task 6
    fix round 1)."""

    def test_the_published_keys_match_the_golden_contract(self) -> None:
        golden = _golden_contract()
        record = sent_alert_record()

        document = build_snapshot(alerting_cycle_result(), config(), recent=(record,))

        assert set(document) == set(golden["top_level"])
        assert set(document["alert"]) == set(golden["alert"])
        assert set(document["recent_alerts"][0]) == set(golden["recent_alert"])


def _golden_contract() -> dict[str, Any]:
    return json.loads((REPO_ROOT / "contracts" / "public-snapshot.json").read_text(encoding="utf-8"))


class TestThePublishedValuesMatchTheGoldenContract:
    """Pinning which *keys* appear says nothing about what may be in them.

    The page branches on `level` to pick a colour and on `sources.*` to decide
    whether to say a source was unreadable. A new `Level` member reaching the
    page as an unknown string renders as neither, and the resident sees a
    status page that has quietly stopped saying anything. Until this class the
    page enforced its own idea of the level set unilaterally; that agreement
    belongs in the contract, asserted from both sides, exactly as the key sets
    already are.

    **Derived from the enum, never hand-copied.** A literal list here would let
    a fourth `Level` land with the contract still green and the page still
    blind. Adding one has to fail this test, and the only way to make it pass
    again is to widen the contract deliberately — which is the conversation
    with the page's author that a new level requires.
    """

    def test_the_level_domain_is_exactly_the_level_enum(self) -> None:
        golden = _golden_contract()

        assert set(golden["values"]["level"]) == {level.value for level in Level}

    def test_the_source_status_domain_is_exactly_what_status_of_can_return(self) -> None:
        """`status_of` has no enum behind it — it returns one of two literal
        strings — so the domain is derived by asking it, once per branch of
        the `SourceResult` union it narrows."""
        golden = _golden_contract()

        produced = {
            status_of(Available(data=(), fetched_at=DEFAULT_NOW)),
            status_of(
                Unavailable(
                    source=SourceName.SENAMHI,
                    reason=UnavailableReason.TRANSPORT_ERROR,
                    detail="unreachable",
                    observed_at=DEFAULT_NOW,
                )
            ),
        }

        assert set(golden["values"]["source_status"]) == produced

    def test_a_published_document_only_carries_values_from_those_domains(self) -> None:
        """The contract and the enum can agree while `build_snapshot` publishes
        something else entirely — it writes `assessment.level.value` and the two
        `assessment.*_status` strings, none of which this file otherwise reads
        against the contract."""
        golden = _golden_contract()

        document = build_snapshot(alerting_cycle_result(), config(), recent=())

        assert document["level"] in golden["values"]["level"]
        assert set(document["sources"].values()) <= set(golden["values"]["source_status"])
