"""`run_once` — the shared post-dependency sequence both entry points call
(design.md D29; `scheduled-execution` spec, "Both entry points execute the
same post-dependency sequence").

Extracted verbatim from `cli.py:396-409` as it stood before this change:
`config.load()`, `RunAlertCycle(deps).execute()`, and the forgiving
snapshot-publish guard labelled by `SNAPSHOT_FAILURE_PREFIX`. Neither
`cli.main` nor `lambda_handler.handler` may reimplement this sequence
independently.

**`default_now`, `MAX_RECENT_ALERTS`/`_recent_alerts` and `as_json` live here
too, not only the four extracted lines.** All three were previously defined
in `cli.py`, and all three are needed, unchanged, by the code that moved:
`_recent_alerts` is called from inside the extracted publish guard, and
`as_json` is the one `cli.py`-defined function `lambda_handler.handler` must
also call to log the same document (design.md D29, "logs the same
machine-readable document the CLI's `--json` mode emits"). But
`entrypoints/lambda_handler.py` is forbidden from importing `entrypoints.cli`
at all (D29's own architecture test,
`tests/architecture/test_layer_boundaries.py`), so `cli.py` could no longer
be the sole owner of a function the handler must call too — moving them here
is what keeps that boundary real rather than worked around. `cli.py`
re-exports `default_now` and `as_json` so `cli.default_now`/`cli.as_json`,
their `__all__` entry and every docstring citation of them still resolve
unchanged, exactly as it already did for `default_now` alone.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, TextIO

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
from rain_alert.ports import SnapshotPublisher

__all__ = ["MAX_RECENT_ALERTS", "SNAPSHOT_FAILURE_PREFIX", "as_json", "default_now", "run_once", "source_notes"]

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
#: It goes to the `report` stream, never standard output: under the CLI's
#: `--json` mode standard output carries one machine-readable document and
#: nothing else, and the scheduled path's `report` stream is a Lambda's
#: `stderr`, kept separate from the one `as_json` line on stdout for the same
#: reason. It does not change the exit code.
SNAPSHOT_FAILURE_PREFIX = "[SNAPSHOT NOT PUBLISHED]"


def default_now() -> datetime:
    """The system clock, truncated to whole seconds.

    Sub-second precision is noise in every consumer: it clutters the operator
    output, and `TimeWindow.key()` — derived from the window start — becomes
    change 3's DynamoDB sort key, where microseconds carry no meaning and only
    make keys harder to read and compare.
    """
    return datetime.now(UTC).replace(microsecond=0)


def source_notes(result: SourceResult[Any]) -> tuple[str, ...]:
    return result.notes if isinstance(result, Available) else ()


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


def as_json(result: CycleResult, config: AlertConfig) -> str:
    """The same cycle as a machine-readable document, for calibration
    logging and for the scheduled path's CloudWatch Logs record
    (`scheduled-execution` spec, "The handler logs the same machine-readable
    document the CLI's `--json` mode emits").

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
            "senamhi": list(source_notes(result.senamhi)),
            "open_meteo": list(source_notes(result.open_meteo)),
        },
        "reasons": [reason_to_dict(reason) for reason in result.assessment.reasons],
        "reasons_en": list(render_reasons_en(result.assessment.reasons)),
        "decision": {"send": result.decision.send, "reason": result.decision.reason},
        "sent": result.sent,
        "recipients_count": result.recipients_count,
        # Provenance travels with the message for the same reason the
        # coordinates travel with their source: this document is what a
        # calibration log and the Lambda entry point read, and without it a
        # fallback is invisible here. `notices` carries outage notices only,
        # by design — the composer's fallback notice is emitted by the
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


def run_once(
    deps: CycleDependencies,
    publisher: SnapshotPublisher | None,
    *,
    report: TextIO,
) -> tuple[CycleResult, AlertConfig]:
    """One rain-alert cycle: load configuration, run the use case, and
    publish the public snapshot behind a forgiving guard.

    Both `cli.main` and `lambda_handler.handler` call this and only this —
    neither entry point reimplements the sequence (`scheduled-execution`
    spec). It performs no argument parsing, no human-readable rendering and
    returns no exit code; those stay the CLI's own (design.md D29).

    Args:
        deps: The full dependency graph (design.md D6).
        publisher: Where the public snapshot goes, or `None` for "do not
            publish" — the same posture `cli.main` already had.
        report: Where the `SNAPSHOT_FAILURE_PREFIX` line goes on a publish
            failure. The CLI passes its error stream; the scheduled path
            passes Lambda's `stderr`.

    Returns:
        The `CycleResult` and the `AlertConfig` the cycle ran with — the
        caller needs both for `render`/`as_json`, and returning the config
        here removes a third `config.load()` on a path where that call can
        be a real round trip (design.md D29).
    """
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
            print(f"{SNAPSHOT_FAILURE_PREFIX} {sanitize_source_text(f'{type(exc).__name__}: {exc}')}", file=report)

    return result, config
