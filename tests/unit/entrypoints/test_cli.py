"""The local CLI (local-alert-cli spec; design.md 9, D11).

Runs the *real* object graph — real scraper, real Open-Meteo parser, real
evaluator, real template, real file repository — with only the two fetch
leaves pointed at pinned fixtures via `--offline-fixtures`. That is the
point of D6: the graph the CLI runs is the graph these tests exercise.
"""

from __future__ import annotations

import io
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from rain_alert.adapters.console_notifier import ALERT_PREFIX, NOTICE_PREFIX
from rain_alert.adapters.local.json_alert_repository import JsonFileAlertRepository
from rain_alert.adapters.local.json_file_snapshot_publisher import SNAPSHOT_PATH_ENV_VAR
from rain_alert.adapters.open_meteo import OpenMeteoForecastProvider
from rain_alert.adapters.senamhi_scraper import SenamhiWarningScraper
from rain_alert.application.run_alert_cycle import CycleResult, RunAlertCycle
from rain_alert.domain.config import AlertConfig
from rain_alert.domain.messages import AlertMessage, AlertRecord, OperatorNotice
from rain_alert.domain.values import ComposerName, Level, NoticeKind, SourceName, TimeWindow
from rain_alert.entrypoints.cli import (
    EXIT_OK,
    MAX_RECENT_ALERTS,
    OPEN_METEO_FIXTURE_NAME,
    SENAMHI_FIXTURE_NAME,
    as_json,
    default_now,
    main,
    parse_now,
    render,
)
from rain_alert.entrypoints.wiring import build_local_deps
from tests.support.fakes import RecordingPublisher
from tests.support.fixtures import (
    SENAMHI_ACTIVE_WARNING,
    SENAMHI_BROKEN_STRUCTURE,
    SENAMHI_HISTORY_ONLY,
    SENAMHI_PRECIPITATION_COAST,
)
from tests.support.wiring import DEFAULT_CONFIG, DEFAULT_NOW, build_fake_deps

NOW = "2026-09-04T12:00:00+00:00"
NOW_DT = datetime(2026, 9, 4, 12, tzinfo=UTC)


def _open_meteo_payload(*, mm: float, probability: int, at_hour: int = 0, hours: int = 72) -> str:
    """A live-shaped payload with all the rain in one hour, `at_hour` hours
    after `now`.

    All in one hour so a threshold assertion tests the rule rather than
    floating-point summation; `at_hour` chooses which evaluator branch the
    data can reach — hour 0 is inside the 24-hour imminent horizon, hour 30
    is only inside the 48-hour prepare horizon.

    The series starts 12 hours before `now`, so the adapter's forward slice
    is exercised too.
    """
    offset = 12 + at_hour
    stamps = [(NOW_DT - timedelta(hours=12) + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(hours)]
    return json.dumps(
        {
            "latitude": -6.636005,
            "longitude": -79.789860,
            "hourly_units": {"time": "iso8601", "precipitation": "mm", "precipitation_probability": "%"},
            "hourly": {
                "time": stamps,
                "precipitation": [0.0] * offset + [mm] + [0.0] * (hours - offset - 1),
                "precipitation_probability": [0] * offset + [probability] + [0] * (hours - offset - 1),
            },
        }
    )


def _fixtures(
    tmp_path: Path,
    *,
    senamhi: Path = SENAMHI_ACTIVE_WARNING,
    mm: float = 0.0,
    probability: int = 0,
    at_hour: int = 0,
) -> Path:
    directory = tmp_path / "fixtures"
    directory.mkdir(exist_ok=True)
    (directory / SENAMHI_FIXTURE_NAME).write_text(senamhi.read_text(encoding="utf-8"), encoding="utf-8")
    (directory / OPEN_METEO_FIXTURE_NAME).write_text(
        _open_meteo_payload(mm=mm, probability=probability, at_hour=at_hour), encoding="utf-8"
    )
    return directory


def _run_streams(capsys, tmp_path: Path, *extra: str, **fixture_kwargs) -> tuple[int, str, str]:
    code = main(
        [
            "--now",
            NOW,
            "--state-file",
            str(tmp_path / "state.json"),
            "--offline-fixtures",
            str(_fixtures(tmp_path, **fixture_kwargs)),
            *extra,
        ]
    )
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _run(capsys, tmp_path: Path, *extra: str, **fixture_kwargs) -> tuple[int, str]:
    code, out, _ = _run_streams(capsys, tmp_path, *extra, **fixture_kwargs)
    return code, out


class TestOutputOnEveryRun:
    """local-alert-cli: "CLI output is informative for calibration" — the
    level, the reasons and the exact message text on every run, sent or not."""

    def test_a_calm_run_prints_the_city_level_sources_and_message_preview(self, capsys, tmp_path: Path) -> None:
        code, output = _run(capsys, tmp_path)

        assert code == 0
        assert "Ferreñafe" in output
        assert "Level      none" in output
        assert "senamhi: available" in output
        assert "open_meteo: available" in output
        assert "PREVIEW (NOT SENT)" in output

    def test_the_message_preview_is_the_spanish_body_verbatim(self, capsys, tmp_path: Path) -> None:
        """D11: on a non-send the CLI renders the preview itself from
        `message_request`, so no composer is invoked and no cost is incurred —
        but the text shown is the real recipient-facing message."""
        _, output = _run(capsys, tmp_path)

        assert "Ciudad: Ferreñafe" in output
        assert "Nivel: sin riesgo" in output
        assert "Recomendaciones:" in output

    def test_the_sourced_coordinates_are_printed_without_a_marker(self, capsys, tmp_path: Path) -> None:
        """weather-sources: Coordinates come from configuration. The operator
        must see the point the forecast was fetched for; the coordinates are
        now sourced, so a marker calling them provisional would be false."""
        _, output = _run(capsys, tmp_path)

        assert "-6.6360" in output and "-79.7899" in output
        assert "PLACEHOLDER" not in output
        # Retiring the flag must not leave the operator unable to tell a
        # published point from one confirmed at the location.
        assert "public_reference" in output

    def test_the_reasons_are_the_english_operator_audit_trail(self, capsys, tmp_path: Path) -> None:
        """The CLI is the operator's view, so it renders reasons through
        `render_reasons_en` — never the Spanish recipient wording, and never
        raw `Reason` values.

        The rain sits 30 hours out, so it is outside the 24-hour imminent
        horizon and this exercises the degraded prepare branch, which is the
        one that carries both a source-outage reason and a threshold."""
        _, output = _run(capsys, tmp_path, senamhi=SENAMHI_BROKEN_STRUCTURE, mm=24.0, probability=95, at_hour=30)

        reasons = output[output.index("Reasons") : output.index("Decision")]
        assert "official SENAMHI source unavailable; forecast-only evaluation" in reasons
        assert "24.0 mm / 48 h at 95% max probability (required threshold 70%)" in reasons
        assert "Motivos" not in reasons
        assert "ForecastThresholdReason" not in reasons

    def test_the_evaluated_window_is_printed(self, capsys, tmp_path: Path) -> None:
        _, output = _run(capsys, tmp_path)

        assert "Window     2026-09-04T12:00" in output

    def test_what_a_source_set_aside_is_printed_for_the_operator(self, capsys, tmp_path: Path) -> None:
        """weather-sources: Exclusions are auditable, all the way to the
        operator's screen.

        `Level none` has two very different causes — nothing was in force, or
        everything in force was filtered out — and only one of them means the
        system is idle. Without this block they look identical on stdout.
        """
        _, output = _run(capsys, tmp_path, senamhi=SENAMHI_ACTIVE_WARNING)

        notes = output[output.index("Notes") : output.index("Level")]
        assert "senamhi" in notes
        assert "INCREMENTO DE TEMPERATURA DIURNA EN LA COSTA Y SIERRA" in notes
        assert "high_temperature" in notes

    def test_a_source_with_nothing_to_report_prints_no_notes(self, capsys, tmp_path: Path) -> None:
        _, output = _run(capsys, tmp_path, senamhi=SENAMHI_HISTORY_ONLY)

        notes = output[output.index("Notes") : output.index("Level")]
        assert notes.strip() == "Notes      —"

    def test_the_json_output_carries_the_same_notes(self, capsys, tmp_path: Path) -> None:
        """`--json` is the calibration log; an audit trail that exists only on
        the human-readable path is not an audit trail."""
        _, output = _run(capsys, tmp_path, "--json", senamhi=SENAMHI_ACTIVE_WARNING)

        document = json.loads(output)
        assert any("high_temperature" in note for note in document["notes"]["senamhi"])
        assert document["notes"]["open_meteo"] == []


class TestTheDecisionAndTheSend:
    def test_a_qualifying_forecast_sends_and_records(self, capsys, tmp_path: Path) -> None:
        """alert-cycle: Authorized send runs the full chain, through the real
        adapters."""
        code, output = _run(capsys, tmp_path, mm=30.0, probability=90)

        assert code == 0
        assert "Level      imminent" in output
        assert "Decision   SENT" in output
        assert ALERT_PREFIX in output

        recorded = JsonFileAlertRepository(tmp_path / "state.json").alerts_with_window_start_between(
            "ferrenafe", NOW_DT - timedelta(hours=72), NOW_DT + timedelta(hours=72)
        )
        assert len(recorded) == 1
        assert recorded[0].composer == "template"

    def test_a_second_identical_run_is_deduplicated(self, capsys, tmp_path: Path) -> None:
        """alert-cycle: Dedup short-circuits the cycle. This is what the file
        repository exists for — each run is a fresh process."""
        first_code, _ = _run(capsys, tmp_path, mm=30.0, probability=90)
        second_code, second_output = _run(capsys, tmp_path, mm=30.0, probability=90)

        assert (first_code, second_code) == (0, 0)
        assert "Decision   NOT SENT" in second_output
        assert "not an escalation" in second_output
        assert ALERT_PREFIX not in second_output

        recorded = JsonFileAlertRepository(tmp_path / "state.json").alerts_with_window_start_between(
            "ferrenafe", NOW_DT - timedelta(hours=72), NOW_DT + timedelta(hours=72)
        )
        assert len(recorded) == 1

    def test_a_calm_run_does_not_send(self, capsys, tmp_path: Path) -> None:
        _, output = _run(capsys, tmp_path)

        assert "Decision   NOT SENT — level none" in output
        assert ALERT_PREFIX not in output

    def test_the_red_heat_warning_in_force_does_not_raise_the_level(self, capsys, tmp_path: Path) -> None:
        """design.md 5.1, end to end through the CLI: the real page's warning
        in force on 2026-09-04 was a RED heat warning. It must not produce an
        imminent flood alert."""
        _, output = _run(capsys, tmp_path, senamhi=SENAMHI_ACTIVE_WARNING)

        assert "Level      none" in output
        assert "Decision   NOT SENT" in output

    def test_a_coastal_precipitation_warning_in_force_does_raise_imminent(self, capsys, tmp_path: Path) -> None:
        """Triangulation on the filter: the same code path, with the hazard
        swapped, must still alert. A filter that discarded everything would
        pass the test above."""
        _, output = _run(capsys, tmp_path, senamhi=SENAMHI_PRECIPITATION_COAST)

        assert "Level      imminent" in output
        assert "official SENAMHI warning: orange" in output


class TestDegradedRuns:
    def test_an_unparseable_page_degrades_the_cycle_and_notifies_the_operator(self, capsys, tmp_path: Path) -> None:
        code, output = _run(capsys, tmp_path, senamhi=SENAMHI_BROKEN_STRUCTURE)

        assert code == 0
        assert "senamhi: unavailable (structure_unrecognized" in output
        assert "[degraded]" in output
        assert NOTICE_PREFIX in output
        assert "Notices    1 emitted" in output

    def test_a_degraded_cycle_still_exits_zero(self, capsys, tmp_path: Path) -> None:
        """design.md 9: a degraded cycle is the system working as designed, and
        a non-zero code would break any future cron or CI wrapper."""
        code, _ = _run(capsys, tmp_path, senamhi=SENAMHI_BROKEN_STRUCTURE, mm=24.0, probability=95)

        assert code == 0

    def test_the_outage_notice_is_not_repeated_on_the_next_cycle(self, capsys, tmp_path: Path) -> None:
        """source-outage-notices: No duplicate notice while still down."""
        _run(capsys, tmp_path, senamhi=SENAMHI_BROKEN_STRUCTURE)
        _, second_output = _run(capsys, tmp_path, senamhi=SENAMHI_BROKEN_STRUCTURE)

        assert "Notices    0 emitted" in second_output
        assert NOTICE_PREFIX not in second_output


class TestJsonOutput:
    def test_json_mode_emits_a_parseable_document(self, capsys, tmp_path: Path) -> None:
        _, output = _run(capsys, tmp_path, "--json", mm=30.0, probability=90)

        document = json.loads(output)

        assert document["level"] == "imminent"
        assert document["sent"] is True
        assert document["senamhi_status"] == "available"
        assert document["city"] == "Ferreñafe"

    def test_json_mode_carries_the_structured_reasons_not_only_the_prose(self, capsys, tmp_path: Path) -> None:
        """Calibration logging needs the numbers, so the machine-readable
        output keeps the tagged reasons alongside the rendered lines."""
        _, output = _run(capsys, tmp_path, "--json", mm=30.0, probability=90)

        document = json.loads(output)

        kinds = [reason["kind"] for reason in document["reasons"]]
        assert "forecast_threshold" in kinds
        assert any("mm / " in line for line in document["reasons_en"])

    def test_json_mode_reports_the_coordinates_and_no_placeholder_flag(self, capsys, tmp_path: Path) -> None:
        """A calibration log needs the point the forecast was fetched for. It
        must not carry a placeholder flag: the coordinates are sourced, so the
        field would be dead configuration in every document."""
        _, output = _run(capsys, tmp_path, "--json")

        document = json.loads(output)

        assert (document["latitude"], document["longitude"]) == (-6.636005, -79.789860)
        assert "coordinates_are_placeholder" not in document
        # This document is what a calibration log and the cloud deployment
        # read, so the pair must not travel without its provenance.
        assert document["coordinates_source"] == "public_reference"

    def test_json_mode_reports_when_the_cycle_ran_not_when_its_window_starts(self, capsys, tmp_path: Path) -> None:
        """W4: `evaluated_at` carried `assessment.window.start`.

        On an imminent-from-official verdict the window start is the aviso's
        own start date, which can be days in the past, and it duplicated
        `window_start` exactly — so a document whose stated purpose is
        calibration logging held no record of when the cycle actually ran, the
        one field that correlates a verdict with the data available then.
        """
        _, output = _run(capsys, tmp_path, "--json", senamhi=SENAMHI_PRECIPITATION_COAST)

        document = json.loads(output)

        assert document["evaluated_at"] == NOW_DT.isoformat()
        assert document["evaluated_at"] != document["window_start"]


def _cycle(tmp_path: Path, **fixture_kwargs) -> tuple[CycleResult, AlertConfig]:
    """One cycle through the CLI's own object graph, returned rather than printed.

    `main` prints and returns an exit code, so a test that needs the
    `CycleResult` itself builds the same graph `main` does — the real
    scraper, parser, evaluator, template and file repository, with the two
    fetch leaves on fixtures.
    """
    deps = build_local_deps(
        state_file=tmp_path / "state.json",
        now=lambda: NOW_DT,
        offline_fixtures=_fixtures(tmp_path, **fixture_kwargs),
        notifier_stream=io.StringIO(),
    )
    return RunAlertCycle(deps).execute(), deps.config.load()


class TestTheDocumentCanSeeWhoWroteTheMessage:
    """The machine-readable document could not tell a fallback from a normal run.

    It emits `CycleResult.notices`, which by design carries outage notices
    only — the agent composer's fallback notice is emitted by the adapter
    during composition and deliberately never travels on that field. Under
    `--json` the notifier writes to standard error, so the fallback was
    visible to a human watching that stream and to nobody else. The document
    a calibration log or change 3's Lambda entrypoint parses said nothing at
    all about it, even though the message now carries its own provenance.

    Adding the provenance to the message object is additive and breaks no
    existing reader: `message` gains a key, and no key changes meaning.
    """

    def test_the_message_object_names_the_composer_that_wrote_it(self, capsys, tmp_path: Path) -> None:
        _, output = _run(capsys, tmp_path, "--json", mm=30.0, probability=90)

        document = json.loads(output)

        assert document["message"]["composed_by"] == "template"

    def test_an_agent_written_message_is_reported_as_agent_written(self, tmp_path: Path) -> None:
        """The value the field exists for. `select_composer` can wire the
        agent composer now (this slice), but exercising an *accepted* agent
        draft still needs a live runtime, so the provenance is set on the
        result directly — the exact substitution a live run makes, and the
        one thing a calibration log has to be able to see."""
        result, config = _cycle(tmp_path, mm=30.0, probability=90)
        assert result.message is not None
        agent_written = replace(result, message=replace(result.message, composed_by=ComposerName.AGENT))

        document = json.loads(as_json(agent_written, config))

        assert document["message"]["composed_by"] == "agent"

    def test_a_preview_is_attributed_to_the_template_that_rendered_it(self, capsys, tmp_path: Path) -> None:
        """On a non-sending run no composer is invoked and `as_json` renders
        the preview with the deterministic template, so `template` is the true
        answer rather than a default standing in for a missing one."""
        _, output = _run(capsys, tmp_path, "--json")

        document = json.loads(output)

        assert document["sent"] is False
        assert document["message"]["composed_by"] == "template"


class TestTheJsonDocumentOwnsStandardOutputAlone:
    """`--json` is documented as machine-readable output for calibration
    logging, and `ConsoleNotifier` defaulted to stdout too. So on any run that
    sent an alert or emitted an operator notice, the document was preceded by
    the notifier's block and `json.loads` failed — the flag broke on exactly
    the runs worth logging.
    """

    def test_the_document_parses_on_a_run_that_also_sends(self, capsys, tmp_path: Path) -> None:
        code, out, _ = _run_streams(capsys, tmp_path, "--json", mm=30.0, probability=90)

        document = json.loads(out)

        assert code == 0
        assert document["sent"] is True
        assert document["recipients_count"] == 1

    def test_the_document_parses_on_a_run_that_emits_an_operator_notice(self, capsys, tmp_path: Path) -> None:
        """The other stdout writer. A degraded cycle is the commonest reason
        to be reading a calibration log in the first place."""
        _, out, _ = _run_streams(capsys, tmp_path, "--json", senamhi=SENAMHI_BROKEN_STRUCTURE)

        document = json.loads(out)

        assert document["senamhi_status"] == "unavailable"
        assert document["notices"]

    def test_the_notifier_blocks_are_still_emitted_on_standard_error(self, capsys, tmp_path: Path) -> None:
        """Redirected, not silenced. The dry-run block is the operator's proof
        of what would have been delivered, so `--json` must not cost it."""
        _, out, err = _run_streams(capsys, tmp_path, "--json", mm=30.0, probability=90)

        assert ALERT_PREFIX in err
        assert ALERT_PREFIX not in out

    def test_stdout_holds_the_document_and_nothing_else(self, capsys, tmp_path: Path) -> None:
        _, out, _ = _run_streams(capsys, tmp_path, "--json", mm=30.0, probability=90)

        assert out.lstrip().startswith("{")
        assert out.rstrip().endswith("}")

    def test_the_human_readable_run_still_prints_the_notifier_block_on_stdout(self, capsys, tmp_path: Path) -> None:
        """Triangulation: the split is scoped to `--json`. Without `--json`
        the whole operator view belongs on stdout, block included."""
        _, out, err = _run_streams(capsys, tmp_path, mm=30.0, probability=90)

        assert ALERT_PREFIX in out
        assert ALERT_PREFIX not in err


class TestTheMessageHeaderNamesTheComposer:
    """design.md D24: `SENT [composer: agent]` / `PREVIEW (NOT SENT) [composer:
    template]`. Exercised on a manually-built `CycleResult` — like
    `TestTheDocumentCanSeeWhoWroteTheMessage` does for `as_json` — because
    production wiring cannot yet reach an accepted agent draft without a live
    runtime."""

    def test_a_sent_agent_written_message_names_the_agent(self, tmp_path: Path) -> None:
        result, config = _cycle(tmp_path, mm=30.0, probability=90)
        assert result.message is not None
        agent_written = replace(result, message=replace(result.message, composed_by=ComposerName.AGENT))

        output = render(agent_written, config)

        assert "SENT [composer: agent]" in output

    def test_a_sent_template_written_message_names_the_template(self, capsys, tmp_path: Path) -> None:
        _, output = _run(capsys, tmp_path, mm=30.0, probability=90)

        assert "SENT [composer: template]" in output

    def test_a_preview_names_the_template_regardless_of_configuration(self, capsys, tmp_path: Path) -> None:
        """D24: the preview always reads `template`, because it is rendered
        by calling the template directly on `message_request` — the composer
        itself is never invoked, so a deduplicated cycle stays free."""
        _, output = _run(capsys, tmp_path)

        assert "PREVIEW (NOT SENT) [composer: template]" in output


class TestComposerFlag:
    """`--composer {template,agent}` (design.md D24, local-alert-cli spec
    "CLI agent composer selection is opt-in and defaults off")."""

    def test_absent_flag_uses_the_template_composer_and_never_touches_the_seam(self, capsys, tmp_path: Path) -> None:
        _, output = _run(capsys, tmp_path)

        assert "PREVIEW (NOT SENT) [composer: template]" in output

    def test_composer_agent_selects_the_agent_backed_composer_for_this_run(
        self, capsys, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A calm forecast never authorizes a send, so the agent-backed
        composer built here is never asked to `compose` — this is also the
        proof that wiring an agent composer costs nothing when nothing sends,
        with no network call anywhere in the run."""
        monkeypatch.setenv(
            "RAIN_ALERT_AGENT_RUNTIME_ARN",
            "arn:aws:bedrock-agentcore:us-east-1:example-account-id:runtime/example-runtime-abc123",
        )

        code, output = _run(capsys, tmp_path, "--composer", "agent")

        assert code == 0
        assert "PREVIEW (NOT SENT) [composer: template]" in output

    def test_composer_agent_without_a_runtime_arn_fails_at_wiring_time(self, capsys, tmp_path: Path) -> None:
        """An operator who asks for the agent composer without deploying one
        needs to hear about it before a cycle runs, not on the first send."""
        code = main(
            [
                "--now",
                NOW,
                "--state-file",
                str(tmp_path / "state.json"),
                "--offline-fixtures",
                str(_fixtures(tmp_path)),
                "--composer",
                "agent",
            ]
        )

        assert code != 0
        assert "RAIN_ALERT_AGENT_RUNTIME_ARN" in capsys.readouterr().err

    def test_an_unrecognised_composer_value_is_rejected_by_the_parser(self, capsys, tmp_path: Path) -> None:
        with pytest.raises(SystemExit):
            main(["--now", NOW, "--state-file", str(tmp_path / "state.json"), "--composer", "gpt"])


class TestTheCliCannotSend:
    def test_there_is_deliberately_no_dry_run_flag(self, capsys, tmp_path: Path) -> None:
        """design.md 9: offering `--dry-run` would imply a send mode exists.
        The guarantee is structural — the wiring can only build
        `ConsoleNotifier`, and no sending notifier exists in this codebase."""
        with pytest.raises(SystemExit) as caught:
            main(["--dry-run"])

        assert caught.value.code != 0

    def test_the_wiring_can_only_construct_the_console_notifier(self, tmp_path: Path) -> None:
        from rain_alert.adapters.console_notifier import ConsoleNotifier

        deps = build_local_deps(state_file=tmp_path / "state.json", now=lambda: NOW_DT)

        assert isinstance(deps.notifier, ConsoleNotifier)


class TestWiring:
    def test_without_fixtures_the_real_adapters_are_wired(self, tmp_path: Path) -> None:
        """local-alert-cli: Real-source run. No network call is made here —
        only the object graph is inspected."""
        deps = build_local_deps(state_file=tmp_path / "state.json", now=lambda: NOW_DT)

        assert isinstance(deps.warnings, SenamhiWarningScraper)
        assert isinstance(deps.forecast, OpenMeteoForecastProvider)
        assert isinstance(deps.alerts, JsonFileAlertRepository)

    def test_with_fixtures_the_same_parsers_read_from_disk(self, tmp_path: Path) -> None:
        """Offline mode swaps the fetch leaves only: the real scraper and the
        real payload parser still do the work, so the offline path cannot
        diverge from the live one."""
        deps = build_local_deps(
            state_file=tmp_path / "state.json",
            now=lambda: NOW_DT,
            offline_fixtures=_fixtures(tmp_path),
        )

        assert isinstance(deps.warnings, SenamhiWarningScraper)
        result = deps.forecast.fetch_forecast(deps.config.load().coordinates, 48, NOW_DT)
        assert result.__class__.__name__ == "Available"

    def test_the_notifier_stream_is_injectable_so_the_caller_owns_the_split(self, tmp_path: Path) -> None:
        """The wiring, not the notifier's default, decides where blocks go.
        `ConsoleNotifier()` fell back to `sys.stdout` and so ignored even an
        explicitly injected CLI stream."""
        stream = io.StringIO()

        deps = build_local_deps(state_file=tmp_path / "state.json", now=lambda: NOW_DT, notifier_stream=stream)
        deps.notifier.send_operator_notice(
            OperatorNotice(
                kind=NoticeKind.SOURCE_UNAVAILABLE,
                subject="subject",
                body="body",
                sources=frozenset({SourceName.SENAMHI}),
                occurred_at=NOW_DT,
            )
        )

        assert NOTICE_PREFIX in stream.getvalue()

    def test_a_missing_offline_fixture_directory_is_reported_clearly(self, capsys, tmp_path: Path) -> None:
        code = main(
            ["--now", NOW, "--state-file", str(tmp_path / "s.json"), "--offline-fixtures", str(tmp_path / "no")]
        )

        assert code != 0
        assert "no" in capsys.readouterr().err


class TestTheDefaultClock:
    def test_the_system_clock_is_truncated_to_whole_seconds(self) -> None:
        """Sub-second precision is noise in every consumer: it clutters the
        operator output, and `TimeWindow.key()` — derived from the window
        start — becomes change 3's DynamoDB sort key, where microseconds carry
        no meaning and only make keys harder to read and compare."""
        moment = default_now()

        assert moment.microsecond == 0
        assert moment.tzinfo is UTC

    def test_an_explicit_now_is_still_honoured_exactly(self) -> None:
        """Triangulation: `--now` is for reproducible runs, so it must not be
        rounded."""
        assert parse_now("2026-09-04T12:34:56+00:00") == datetime(2026, 9, 4, 12, 34, 56, tzinfo=UTC)

    def test_a_naive_now_is_read_as_utc(self) -> None:
        assert parse_now("2026-09-04T12:00:00") == NOW_DT

    def test_a_now_in_another_offset_is_converted_to_utc(self) -> None:
        """Lima is UTC-5, and every domain datetime must be UTC (D7)."""
        assert parse_now("2026-09-04T07:00:00-05:00") == NOW_DT

    def test_an_unparseable_now_cannot_start_the_run(self, capsys, tmp_path: Path) -> None:
        code = main(["--now", "yesterday", "--state-file", str(tmp_path / "s.json")])

        assert code != 0
        assert "--now" in capsys.readouterr().err


class RaisingPublisher:
    """Every destination fails eventually. This one always does."""

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error if error is not None else OSError("bucket on fire")

    def publish(self, document: dict[str, Any]) -> None:
        raise self._error


class TestSnapshotPublishing:
    """public-status-page: publishing is reporting, not notifying. If the
    destination is unreachable the alert still goes out — the same posture
    as the operator-notice guard in `agent_composer.py::_fell_back`."""

    def test_a_publisher_that_raises_does_not_fail_the_cycle(self) -> None:
        """The alert reaches the community even when the page cannot be
        updated. A failure in the reporting channel must not cost the
        community its alert."""
        exit_code = main(["--json"], publisher=RaisingPublisher(), deps=build_fake_deps())

        assert exit_code == EXIT_OK

    def test_a_publish_failure_is_announced_on_the_error_stream(self) -> None:
        """Best-effort, but *say so* — the precedent is
        `agent_composer.py::_fell_back`.

        The guard swallowed the exception in silence, so a page frozen at an
        old snapshot looked identical to a page updating correctly: every
        cycle reported success and nobody learned otherwise until a resident
        read a stale level. The guard also covers `_recent_alerts` and
        `build_snapshot`, so a repository read error or a domain bug read as
        "published fine" too.
        """
        out, err = io.StringIO(), io.StringIO()

        main([], publisher=RaisingPublisher(), deps=build_fake_deps(), stdout=out, stderr=err)

        notice = err.getvalue()
        assert "OSError" in notice
        assert len(notice.strip().splitlines()) == 1

    def test_announcing_the_failure_does_not_turn_it_into_one(self) -> None:
        """The other half, pinned separately: a page that cannot be updated is
        worth strictly less than an alert that went out. Saying so must not
        change the exit code a future cron or CI wrapper reads."""
        out, err = io.StringIO(), io.StringIO()

        exit_code = main([], publisher=RaisingPublisher(), deps=build_fake_deps(), stdout=out, stderr=err)

        assert exit_code == EXIT_OK

    def test_the_notice_survives_a_multi_line_exception_as_one_line(self) -> None:
        """`str(exc)` on an S3 error quotes whatever the service sent back.
        The operator reads this in a terminal, where a newline in the middle
        of a notice is how a forged second line gets written — the defect
        `domain/sanitize.py` exists for."""
        out, err = io.StringIO(), io.StringIO()

        main(
            [],
            publisher=RaisingPublisher(OSError("denied\nSources    all good")),
            deps=build_fake_deps(),
            stdout=out,
            stderr=err,
        )

        assert len(err.getvalue().strip().splitlines()) == 1

    def test_nothing_is_announced_when_the_publish_succeeds(self) -> None:
        out, err = io.StringIO(), io.StringIO()

        main([], publisher=RecordingPublisher(), deps=build_fake_deps(), stdout=out, stderr=err)

        assert err.getvalue() == ""

    def test_the_published_document_describes_the_cycle_that_just_ran(self) -> None:
        publisher = RecordingPublisher()

        main([], publisher=publisher, deps=build_fake_deps())

        assert publisher.documents[0]["city"] == "Ferreñafe"
        assert publisher.documents[0]["schema_version"] == 1

    def test_an_injected_deps_graph_with_no_publisher_publishes_nothing(self) -> None:
        """`deps` is how a test exercises the cycle in isolation; a `None`
        publisher there must mean exactly what it says, not "check the real
        environment" — otherwise a test run could publish to whatever the
        developer's shell happens to have configured."""
        exit_code = main([], deps=build_fake_deps())

        assert exit_code == EXIT_OK

    def test_a_configured_local_path_receives_the_snapshot_after_a_real_cycle(
        self, capsys, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The real wiring path, end to end: no injected `deps`, no injected
        `publisher` — only the environment variable an operator would set."""
        snapshot_path = tmp_path / "public" / "status.json"
        monkeypatch.setenv(SNAPSHOT_PATH_ENV_VAR, str(snapshot_path))

        code, _ = _run(capsys, tmp_path)

        assert code == 0
        document = json.loads(snapshot_path.read_text(encoding="utf-8"))
        assert document["city"] == "Ferreñafe"
        assert document["schema_version"] == 1

    def test_no_destination_configured_writes_nothing(self, capsys, tmp_path: Path) -> None:
        """The default: a developer running the cycle locally must not write
        to a public destination by accident."""
        code, _ = _run(capsys, tmp_path)

        assert code == 0
        assert not (tmp_path / "status.json").exists()


def _alert_titled(title: str, *, hours_before_now: float) -> AlertRecord:
    """A minimal, genuine `AlertRecord` for ordering/bounding assertions.

    `title` is the probe: it is otherwise meaningless, chosen so a test can
    read the published order and the kept set straight off the document
    without decoding timestamps.
    """
    window_start = DEFAULT_NOW - timedelta(hours=hours_before_now)
    window = TimeWindow(start=window_start, end=window_start + timedelta(hours=48))
    message = AlertMessage(title=title, body="body", level=Level.IMMINENT, valid_until=window_start)
    return AlertRecord(
        city_slug=DEFAULT_CONFIG.city_slug,
        level=Level.IMMINENT,
        window=window,
        sent_at=window_start,
        message=message,
        reasons=(),
        senamhi_status="available",
        open_meteo_status="available",
        composer="template",
    )


class TestRecentAlertsOrderAndCount:
    """Fix round 1, finding 1: `AlertRepository.alerts_with_window_start_between`
    is documented ascending by window start (`ports/__init__.py`); nothing
    about bounding the query window at `now` reorders that, and it says
    nothing about how many belong on a page. Both have to be resolved
    explicitly by `_recent_alerts`, not left as an accident of what the port
    happens to return."""

    def test_recent_alerts_are_published_newest_first(self) -> None:
        """A reader opening "alertas enviadas" expects the latest alert at
        the top, the way any feed does — the opposite of what the port
        returns unreordered."""
        oldest = _alert_titled("oldest", hours_before_now=30)
        middle = _alert_titled("middle", hours_before_now=20)
        newest = _alert_titled("newest", hours_before_now=10)
        publisher = RecordingPublisher()

        main([], publisher=publisher, deps=build_fake_deps(prior_alerts=[oldest, middle, newest]))

        titles = [entry["title"] for entry in publisher.documents[0]["recent_alerts"]]
        assert titles == ["newest", "middle", "oldest"]

    def test_recent_alerts_are_bounded_to_max_recent_alerts(self) -> None:
        """The dedup lookback window (72h by default) sizes a query for
        dedup correctness, not a page section; without an explicit cap the
        page would grow with however many alerts happened to land in it."""
        spacing_hours = 4
        records = [
            _alert_titled(str(index), hours_before_now=(index + 1) * spacing_hours)
            for index in range(MAX_RECENT_ALERTS + 3)
        ]
        publisher = RecordingPublisher()

        main([], publisher=publisher, deps=build_fake_deps(prior_alerts=records))

        published = publisher.documents[0]["recent_alerts"]
        assert len(published) == MAX_RECENT_ALERTS
        # The kept ones are the most recent: indices 0..MAX_RECENT_ALERTS-1,
        # the smallest `hours_before_now`.
        assert {entry["title"] for entry in published} == {str(i) for i in range(MAX_RECENT_ALERTS)}
