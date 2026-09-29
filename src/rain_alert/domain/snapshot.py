"""The document the public page reads (design 2026-09-21).

Pure: no I/O, no clock, no AWS. Everything it needs is on its inputs, which
is what lets it be tested without a single fake.

**Why the domain and not an adapter.** It decides what this system says to a
community in Spanish, which is the same argument `domain/template.py` makes.
An adapter would have to reach back into the domain for every label anyway.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from rain_alert.domain.config import AlertConfig
from rain_alert.domain.entities import RiskAssessment
from rain_alert.domain.messages import AlertMessage, AlertRecord
from rain_alert.domain.sanitize import sanitize_source_text
from rain_alert.domain.template import LEVEL_LABELS_ES, render_reason_es

#: Bumped when a published field changes meaning or disappears. The page reads
#: this before anything else, so an old page against a new document can say so
#: rather than render a blank.
SNAPSHOT_SCHEMA_VERSION = 1


class _CycleResultLike(Protocol):
    """The subset of `application.run_alert_cycle.CycleResult` this module
    reads.

    `CycleResult` is an application-layer type; importing it here would trip
    `tests/architecture/test_layer_boundaries.py` (`DOMAIN_FORBIDDEN_PREFIXES`
    includes `rain_alert.application`, checked by static AST scan — a
    `TYPE_CHECKING`-guarded import does not escape it). A structural protocol
    lets the real `CycleResult` satisfy this parameter — it already has every
    attribute below — without the domain package knowing how a cycle runs.

    **Declared as read-only properties, not plain attributes.** `CycleResult`
    is `@dataclass(frozen=True, slots=True)` (`application/run_alert_cycle.py`),
    so its fields are read-only. A `Protocol` attribute written as a plain
    annotation (`evaluated_at: datetime`) declares a *settable* member, which
    a frozen dataclass cannot satisfy — mypy rejects it as a structural
    mismatch. This went unnoticed because `files = ["src"]` in `pyproject.toml`
    means mypy never checks `tests/`, and nothing in `src/` called
    `build_snapshot` with a real `CycleResult` until `entrypoints/cli.py`
    (public-status-page, task 5) did. A property getter with no setter is
    exactly "readable", which is all this module ever does with these fields.
    """

    @property
    def evaluated_at(self) -> datetime: ...
    @property
    def assessment(self) -> RiskAssessment: ...
    @property
    def message(self) -> AlertMessage | None: ...
    @property
    def sent(self) -> bool: ...


def _local(moment: datetime, timezone: str) -> str:
    return moment.astimezone(ZoneInfo(timezone)).isoformat()


def _published_text(value: Any, *, field: str) -> str:
    """A read-back `title`/`body`, guarded and sanitized before it reaches
    the public page (scheduled-cycle fix round 1).

    `AlertMessage.title`/`.body` are typed as `str`, but a Python type
    annotation is not runtime-enforced, and change 3's `DynamoDbAlertRepository`
    reads these fields back from a shared, multi-writer DynamoDB table with no
    schema enforcement beyond the adapter's own mapper — unlike the local JSON
    file, a single writer this process itself controls. A hostile or
    future-schema item could carry a non-string value (a stray `Decimal`,
    say); `sanitize_source_text` assumes `str` and a `TypeError` deep inside it
    would name neither the field nor the record. Raising here, by name, is
    what the same rule `render_reason_es` already follows two lines below
    would otherwise assume was already true.
    """
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string to be published, got {type(value).__name__}")
    return sanitize_source_text(value)


def build_snapshot(result: _CycleResultLike, config: AlertConfig, recent: tuple[AlertRecord, ...]) -> dict[str, Any]:
    """One cycle, as the page reads it."""
    zone = config.timezone
    assessment = result.assessment
    alert: dict[str, Any] | None = None
    if result.message is not None:
        alert = {
            "title": _published_text(result.message.title, field="alert.title"),
            "body": _published_text(result.message.body, field="alert.body"),
            "valid_until": _local(result.message.valid_until, zone),
            "composed_by": result.message.composed_by.value,
            "sent": result.sent,
        }
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "city": config.city,
        "evaluated_at": _local(result.evaluated_at, zone),
        "level": assessment.level.value,
        "level_label": LEVEL_LABELS_ES[assessment.level],
        "window": {
            "start": _local(assessment.window.start, zone),
            "end": _local(assessment.window.end, zone),
        },
        "reasons": [line for r in assessment.reasons if (line := render_reason_es(r)) is not None],
        "sources": {
            "senamhi": assessment.senamhi_status,
            "open_meteo": assessment.open_meteo_status,
        },
        "degraded": assessment.degraded,
        "alert": alert,
        "recent_alerts": [
            {
                "level": record.level.value,
                "sent_at": _local(record.sent_at, zone),
                "title": _published_text(record.message.title, field="recent_alerts.title"),
                # `AlertRecord.composer` already holds this value (D13,
                # `messages.py`) — read directly rather than through
                # `record.message.composed_by.value`, a second traversal to
                # the same fact.
                "composed_by": record.composer,
            }
            for record in recent
        ],
    }
