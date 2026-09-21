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
    """

    evaluated_at: datetime
    assessment: RiskAssessment
    message: AlertMessage | None
    sent: bool


def _local(moment: datetime, timezone: str) -> str:
    return moment.astimezone(ZoneInfo(timezone)).isoformat()


def build_snapshot(result: _CycleResultLike, config: AlertConfig, recent: tuple[AlertRecord, ...]) -> dict[str, Any]:
    """One cycle, as the page reads it."""
    zone = config.timezone
    assessment = result.assessment
    alert: dict[str, Any] | None = None
    if result.message is not None:
        alert = {
            "title": result.message.title,
            "body": result.message.body,
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
                "title": record.message.title,
                "composed_by": record.message.composed_by.value,
            }
            for record in recent
        ],
    }
