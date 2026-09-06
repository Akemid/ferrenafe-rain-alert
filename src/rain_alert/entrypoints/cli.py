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
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from rain_alert.adapters.local.json_alert_repository import DEFAULT_STATE_FILE
from rain_alert.adapters.serialization import reason_to_dict
from rain_alert.application.run_alert_cycle import CycleResult, RunAlertCycle
from rain_alert.domain.config import AlertConfig
from rain_alert.domain.reasons import render_reasons_en
from rain_alert.domain.sources import Available, SourceResult
from rain_alert.domain.template import MessageComposer
from rain_alert.entrypoints.wiring import (
    OPEN_METEO_FIXTURE_NAME,
    SENAMHI_FIXTURE_NAME,
    build_local_deps,
)

__all__ = ["OPEN_METEO_FIXTURE_NAME", "SENAMHI_FIXTURE_NAME", "default_now", "main", "parse_now"]

_LABEL_WIDTH = 11
_PLACEHOLDER_MARKER = "(PLACEHOLDER — pending confirmation)"
_PREVIEW_LABEL = "PREVIEW (NOT SENT)"

EXIT_OK = 0
EXIT_CANNOT_START = 2


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
    """
    message = result.message
    header = "SENT" if message is not None else _PREVIEW_LABEL
    if message is None:
        message = MessageComposer().compose(result.message_request)
    return [header, f"title: {message.title}", "body:", *(f"  {line}" for line in message.body.splitlines())]


def render(result: CycleResult, config: AlertConfig) -> str:
    """The operator's view of one cycle (design.md 9)."""
    coordinates = f"({config.coordinates.latitude:.4f}, {config.coordinates.longitude:.4f})"
    placeholder = f"  {_PLACEHOLDER_MARKER}" if config.coordinates_are_placeholder else ""
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
        f"{result.config_city}  {coordinates}{placeholder}",
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
        "coordinates_are_placeholder": config.coordinates_are_placeholder,
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
        "message": {"title": message.title, "body": message.body, "valid_until": message.valid_until.isoformat()},
        "notices": [
            {"kind": notice.kind.value, "sources": sorted(source.value for source in notice.sources)}
            for notice in result.notices
        ],
    }
    return json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True)


def main(argv: Sequence[str] | None = None, stdout: TextIO | None = None, stderr: TextIO | None = None) -> int:
    """Run one cycle and print it. Returns the process exit code."""
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    arguments = _parser().parse_args(argv)

    fixtures: Path | None = arguments.offline_fixtures
    if fixtures is not None and not fixtures.is_dir():
        print(f"--offline-fixtures: {fixtures} is not a directory", file=err)
        return EXIT_CANNOT_START
    try:
        now = default_now() if arguments.now is None else parse_now(arguments.now)
    except ValueError as exc:
        print(f"--now: {exc}", file=err)
        return EXIT_CANNOT_START

    # `--json` promises one machine-readable document, so the document gets
    # stdout to itself and the notifier's blocks go to stderr. Without this
    # the blocks preceded the document and `json.loads` failed on every run
    # that sent or emitted a notice — the runs a calibration log exists for.
    # On the human-readable path the whole operator view belongs together, so
    # the notifier writes to the same stream everything else does.
    deps = build_local_deps(
        state_file=arguments.state_file,
        now=lambda: now,
        offline_fixtures=fixtures,
        notifier_stream=err if arguments.json else out,
    )
    config = deps.config.load()
    result = RunAlertCycle(deps).execute()

    print(as_json(result, config) if arguments.json else render(result, config), file=out)
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
