"""The local alert-cycle CLI (local-alert-cli spec; design.md 9).

    uv run rain-alert-cycle [--json] [--now ISO8601] [--state-file PATH] [--offline-fixtures DIR]

Runs the real cycle against the real sources for calibration, and sends
nothing.

**The dry-run guarantee is structural, not a flag.** `build_local_deps` can
only construct `ConsoleNotifier`, and no notifier capable of delivering
anything exists anywhere in this codebase. There is deliberately **no
`--dry-run` flag**, because offering one would imply a send mode exists.

Two output rules follow the specs rather than convenience:

- **Reasons are rendered in English**, through
  `domain.reasons.render_reasons_en`. This output is the *operator's* audit
  trail and must carry the numbers and the threshold that applied. The
  Spanish recipient wording belongs to the template alone, and appears here
  only inside the message block, verbatim.
- **The message text is printed on every run**, sent or not (D11). On a
  non-send the preview is rendered here from `CycleResult.message_request`
  by the deterministic template, labelled `PREVIEW (NOT SENT)`, so no
  composer is invoked and a deduplicated cycle costs nothing.
- **Under `--json`, standard output carries the document and nothing else.**
  The flag is documented as machine-readable output for calibration logging,
  and `ConsoleNotifier` defaulted to standard output too, so on any run that
  sent an alert or emitted an operator notice the document was preceded by
  the notifier's block and `json.loads` failed — the flag broke on precisely
  the runs worth logging. The notifier is therefore given standard error
  under `--json`. The blocks are redirected, never suppressed: the dry-run
  block is the operator's proof of what would have been delivered.

**Exit code is 0 for any completed cycle**, including `none`, degraded and
deduplicated outcomes. A degraded cycle is the system working as designed,
and a non-zero code would break any future cron or CI wrapper. Non-zero is
reserved for a run that could not start (bad arguments, missing fixtures).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TextIO

from rain_alert.adapters.local.json_alert_repository import DEFAULT_STATE_FILE
from rain_alert.adapters.serialization import reason_to_dict
from rain_alert.application.dependencies import CycleDependencies
from rain_alert.application.run_alert_cycle import CycleResult, RunAlertCycle
from rain_alert.domain.config import AlertConfig
from rain_alert.domain.messages import AlertRecord
from rain_alert.domain.reasons import render_reasons_en
from rain_alert.domain.sanitize import sanitize_source_text
from rain_alert.domain.snapshot import build_snapshot
from rain_alert.domain.sources import Available, SourceResult
from rain_alert.domain.template import MessageComposer
from rain_alert.domain.values import ComposerName
from rain_alert.entrypoints.wiring import (
    OPEN_METEO_FIXTURE_NAME,
    SENAMHI_FIXTURE_NAME,
    build_local_deps,
    select_snapshot_publisher,
)
from rain_alert.ports import SnapshotPublisher

__all__ = ["OPEN_METEO_FIXTURE_NAME", "SENAMHI_FIXTURE_NAME", "default_now", "main", "parse_now"]

_LABEL_WIDTH = 11
_PREVIEW_LABEL = "PREVIEW (NOT SENT)"

EXIT_OK = 0
EXIT_CANNOT_START = 2

#: How many past alerts the public page shows alongside this cycle's verdict.
#: This is a status page, not an archive: enough for a reader to see a short
#: recent history, not the full record — the state file and the `--json`
#: calibration output remain the complete one. Chosen, not derived from
#: `dedup_lookback_hours`, because that window is sized for dedup
#: correctness and can hold far more entries than belong on a page.
MAX_RECENT_ALERTS = 5

#: Prefix for the one line the cycle writes when the public snapshot could not
#: be published.
#:
#: The guard around that call is deliberately forgiving — a page that cannot be
#: updated is worth strictly less than an alert that went out — but it was also
#: silent, and the two are separable. A frozen page and a page updating
#: correctly looked identical from the operator's side: every cycle reported
#: success, and the first person to learn otherwise would have been a resident
#: reading a stale level. The guard also covers `_recent_alerts` and
#: `build_snapshot`, so a repository read error or a domain bug read as
#: "published fine" too.
#:
#: It goes to the error stream, never standard output: `--json` promises one
#: machine-readable document there and nothing else. It does not change the
#: exit code.
SNAPSHOT_FAILURE_PREFIX = "[SNAPSHOT NOT PUBLISHED]"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rain-alert-cycle",
        description="Run one local rain-alert cycle and print the result. Sends nothing.",
    )
    parser.add_argument("--json", action="store_true", help="emit the cycle result as machine-readable JSON")
    parser.add_argument("--now", help="ISO 8601 instant to run the cycle at, for reproducible runs")
    parser.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_STATE_FILE,
        help=f"dedup and outage state file (default: {DEFAULT_STATE_FILE})",
    )
    parser.add_argument(
        "--offline-fixtures",
        type=Path,
        help=(
            f"directory holding {SENAMHI_FIXTURE_NAME} and {OPEN_METEO_FIXTURE_NAME}; "
            "runs the whole cycle with no network access"
        ),
    )
    parser.add_argument(
        "--composer",
        choices=[member.value for member in ComposerName],
        help="force the composer for this run; absent uses whatever configuration says (default: template)",
    )
    return parser


def default_now() -> datetime:
    """The system clock, truncated to whole seconds.

    Sub-second precision is noise in every consumer: it clutters the operator
    output, and `TimeWindow.key()` — derived from the window start — becomes
    change 3's DynamoDB sort key, where microseconds carry no meaning and only
    make keys harder to read and compare.
    """
    return datetime.now(UTC).replace(microsecond=0)


def parse_now(raw: str) -> datetime:
    """`--now` as an aware UTC instant.

    Not rounded: `--now` exists to make a run reproducible, so it is honoured
    exactly. A naive value is read as UTC, and any other offset is converted,
    because every domain datetime must be UTC (D7).

    Raises:
        ValueError: When `raw` is not ISO 8601.
    """
    moment = datetime.fromisoformat(raw)
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def _source_line(name: str, result: SourceResult[Any]) -> str:
    if isinstance(result, Available):
        return f"{name}: available (fetched {result.fetched_at.isoformat()})"
    return f"{name}: unavailable ({result.reason.value}: {result.detail})"


def _source_notes(result: SourceResult[Any]) -> tuple[str, ...]:
    return result.notes if isinstance(result, Available) else ()


def _note_lines(result: CycleResult) -> list[str]:
    """What each source set aside this cycle, prefixed with the source name.

    This is the operator's only view of the multi-hazard filter. `Level none`
    has two very different causes — nothing was in force, or everything in
    force was filtered out — and a partial parse failure looks exactly like a
    calm page without it.
    """
    return [
        f"- {name}: {note}"
        for name, result in (("senamhi", result.senamhi), ("open_meteo", result.open_meteo))
        for note in _source_notes(result)
    ]


def _block(label: str, lines: Sequence[str]) -> list[str]:
    """`label` against the first line, with the rest hanging under it."""
    if not lines:
        return [f"{label:<{_LABEL_WIDTH}}—"]
    head, *tail = lines
    return [f"{label:<{_LABEL_WIDTH}}{head}", *(f"{'':<{_LABEL_WIDTH}}{line}" for line in tail)]


def _message_lines(result: CycleResult) -> list[str]:
    """The message text, on every run (D11).

    On a send this is the message the composer actually produced. On a
    non-send the deterministic template is called here on
    `message_request` — the composer itself is never invoked, so a
    deduplicated cycle stays free.

    The header names the composer that actually wrote the text (design D24):
    `SENT [composer: agent]` on an accepted agent draft, `SENT [composer:
    template]` on a send or a fallback, `PREVIEW (NOT SENT) [composer:
    template]` always — the preview is rendered by calling the template
    directly, never the configured composer, so `template` is the true
    answer here and not a default standing in for a missing one.
    """
    message = result.message
    label = "SENT" if message is not None else _PREVIEW_LABEL
    if message is None:
        message = MessageComposer().compose(result.message_request)
    header = f"{label} [composer: {message.composed_by.value}]"
    return [header, f"title: {message.title}", "body:", *(f"  {line}" for line in message.body.splitlines())]


def render(result: CycleResult, config: AlertConfig) -> str:
    """The operator's view of one cycle (design.md 9)."""
    # Provenance is printed beside the pair, not inferred from it. An operator
    # reading this during a real event has to be able to tell a point taken
    # from a public reference from one confirmed at the location.
    coordinates = (
        f"({config.coordinates.latitude:.4f}, {config.coordinates.longitude:.4f})  [{config.coordinates_source.value}]"
    )
    assessment = result.assessment
    degraded = "   [degraded]" if assessment.degraded else ""

    notices = [f"{len(result.notices)} emitted"] + [
        f"- {notice.kind.value} ({', '.join(sorted(source.value for source in notice.sources))})"
        for notice in result.notices
    ]
    decision = f"{'SENT' if result.sent else 'NOT SENT'} — {result.decision.reason}"
    if result.sent:
        decision += f" ({result.recipients_count} recipient(s))"

    lines = [
        f"{result.config_city}  {coordinates}",
        *_block("Sources", [_source_line("senamhi", result.senamhi), _source_line("open_meteo", result.open_meteo)]),
        *_block("Notes", _note_lines(result)),
        *_block("Level", [f"{assessment.level.value}{degraded}"]),
        *_block("Window", [f"{assessment.window.start.isoformat()} → {assessment.window.end.isoformat()}"]),
        *_block("Reasons", [f"- {line}" for line in render_reasons_en(assessment.reasons)]),
        *_block("Decision", [decision]),
        *_block("Message", _message_lines(result)),
        *_block("Notices", notices),
    ]
    return "\n".join(lines)


def as_json(result: CycleResult, config: AlertConfig) -> str:
    """The same cycle as a machine-readable document, for calibration logging.

    Reasons are emitted twice on purpose: `reasons` keeps the tagged,
    numeric form a threshold review needs, `reasons_en` keeps the rendered
    operator lines so a log is readable without a decoder.
    """
    message = result.message if result.message is not None else MessageComposer().compose(result.message_request)
    document = {
        "city": config.city,
        "city_slug": config.city_slug,
        "latitude": config.coordinates.latitude,
        "longitude": config.coordinates.longitude,
        # This document is what a calibration log or the cloud deployment reads,
        # so the pair must not travel without saying how it was obtained.
        "coordinates_source": config.coordinates_source.value,
        # The cycle clock, not the window start. The two are different facts and
        # the window start can precede the run by days on an official verdict.
        "evaluated_at": result.evaluated_at.isoformat(),
        "level": result.assessment.level.value,
        "degraded": result.assessment.degraded,
        "window_start": result.assessment.window.start.isoformat(),
        "window_end": result.assessment.window.end.isoformat(),
        "senamhi_status": result.assessment.senamhi_status,
        "open_meteo_status": result.assessment.open_meteo_status,
        # Per source rather than flattened: a calibration review needs to know
        # *which* source filtered or lost something, not only that one did.
        "notes": {
            "senamhi": list(_source_notes(result.senamhi)),
            "open_meteo": list(_source_notes(result.open_meteo)),
        },
        "reasons": [reason_to_dict(reason) for reason in result.assessment.reasons],
        "reasons_en": list(render_reasons_en(result.assessment.reasons)),
        "decision": {"send": result.decision.send, "reason": result.decision.reason},
        "sent": result.sent,
        "recipients_count": result.recipients_count,
        # Provenance travels with the message for the same reason the
        # coordinates travel with their source: this document is what a
        # calibration log and change 3's Lambda entrypoint read, and without
        # it a fallback is invisible here. `notices` carries outage notices
        # only, by design — the composer's fallback notice is emitted by the
        # adapter during composition — and under `--json` the notifier writes
        # to standard error, so a fallback was visible to a human watching
        # that stream and to nothing that parses this. Additive: `message`
        # gains a key and no key changes meaning.
        "message": {
            "title": message.title,
            "body": message.body,
            "valid_until": message.valid_until.isoformat(),
            "composed_by": message.composed_by.value,
        },
        "notices": [
            {"kind": notice.kind.value, "sources": sorted(source.value for source in notice.sources)}
            for notice in result.notices
        ],
    }
    return json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True)


def _recent_alerts(deps: CycleDependencies, config: AlertConfig) -> tuple[AlertRecord, ...]:
    """The alerts the public page shows alongside this cycle's verdict.

    The query window is the configured dedup lookback
    (`AlertConfig.dedup_lookback_hours`), ending at the cycle clock — not
    "the last N records" (`AlertRepository` exposes no such query). Bounding
    it at `deps.now()` guarantees nothing returned is newer than this cycle;
    it says nothing about *display* order or count, and both are resolved
    here explicitly rather than left as an accident of what the port
    happens to return:

    - **Newest first.** `AlertRepository.alerts_with_window_start_between`
      is documented ascending by window start (`ports/__init__.py`). A
      reader opening the page's "alertas enviadas" section expects the
      latest alert at the top, the way any feed does, so the query result
      is reversed here.
    - **Bounded to `MAX_RECENT_ALERTS`**, applied after reordering, so the
      kept entries are always the most recent ones — never the oldest
      `MAX_RECENT_ALERTS` of a longer, unordered slice.
    """
    now = deps.now()
    earliest_start = now - timedelta(hours=config.dedup_lookback_hours)
    ascending = deps.alerts.alerts_with_window_start_between(config.city_slug, earliest_start, now)
    return tuple(reversed(ascending))[:MAX_RECENT_ALERTS]


def main(
    argv: Sequence[str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    *,
    publisher: SnapshotPublisher | None = None,
    deps: CycleDependencies | None = None,
) -> int:
    """Run one cycle and print it. Returns the process exit code.

    Args:
        publisher: Where the public snapshot goes (public-status-page).
            `None` (the default) is resolved from the environment via
            `select_snapshot_publisher` — but only when `deps` is also not
            given, i.e. on the real CLI path. A caller that injects `deps`
            is exercising the cycle in isolation, and a `None` publisher
            there means exactly what it says — do not publish — rather than
            "go check the real environment", which would let a test publish
            to whatever a developer's shell happens to have configured.
        deps: The full dependency graph (design.md D6), for exercising the
            cycle end to end without `--offline-fixtures`. `None` (the
            default) builds the real graph via `build_local_deps`, following
            `--now`, `--state-file` and `--offline-fixtures` as before.
    """
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    arguments = _parser().parse_args(argv)

    if deps is None:
        fixtures: Path | None = arguments.offline_fixtures
        if fixtures is not None and not fixtures.is_dir():
            print(f"--offline-fixtures: {fixtures} is not a directory", file=err)
            return EXIT_CANNOT_START
        try:
            now = default_now() if arguments.now is None else parse_now(arguments.now)
        except ValueError as exc:
            print(f"--now: {exc}", file=err)
            return EXIT_CANNOT_START

        # `--json` promises one machine-readable document, so the document
        # gets stdout to itself and the notifier's blocks go to stderr.
        # Without this the blocks preceded the document and `json.loads`
        # failed on every run that sent or emitted a notice — the runs a
        # calibration log exists for. On the human-readable path the whole
        # operator view belongs together, so the notifier writes to the same
        # stream everything else does.
        try:
            deps = build_local_deps(
                state_file=arguments.state_file,
                now=lambda: now,
                offline_fixtures=fixtures,
                notifier_stream=err if arguments.json else out,
                composer_override=None if arguments.composer is None else ComposerName(arguments.composer),
            )
        except ValueError as exc:
            # `select_composer` raises when `--composer agent` (or
            # `RAIN_ALERT_COMPOSER=agent`) names a runtime nothing configured —
            # an operator asking for the agent composer needs to hear about a
            # missing deploy before a cycle runs, not on the first send.
            print(f"--composer: {exc}", file=err)
            return EXIT_CANNOT_START
        if publisher is None:
            publisher = select_snapshot_publisher(os.environ)

    config = deps.config.load()
    result = RunAlertCycle(deps).execute()

    if publisher is not None:
        # Reporting, not notifying (public-status-page). If the destination
        # is unreachable the alert still went out above this line — a page
        # that cannot be updated is worth strictly less than an alert that
        # does not. Same posture as the operator-notice guard in
        # `agent_composer.py::_fell_back`, including the second half of it:
        # best-effort, but *say so*.
        try:
            publisher.publish(build_snapshot(result, config, _recent_alerts(deps, config)))
        except Exception as exc:  # noqa: BLE001 — a reporting fault must not cost the cycle
            print(f"{SNAPSHOT_FAILURE_PREFIX} {sanitize_source_text(f'{type(exc).__name__}: {exc}')}", file=err)

    print(as_json(result, config) if arguments.json else render(result, config), file=out)
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
