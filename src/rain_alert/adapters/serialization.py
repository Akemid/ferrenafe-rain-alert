"""The persisted shape of alerts, outages and structured reasons (design.md 4.1).

**This is a carried-forward contract, not a local file format.** Change 3's
DynamoDB `alerts-sent` adapter reuses these attribute names one for one, so
the local JSON file and the DynamoDB item are shape-identical and that
adapter becomes a mechanical mapping with no translation layer:

| Item | `pk` | `sk` | Attributes |
|---|---|---|---|
| Sent alert | `ALERT#<city_slug>` | `<window_start>#<level>` | everything `alert_record_to_dict` emits |
| Active outage | `OUTAGE#<city_slug>` | `CURRENT` | everything `outage_record_to_dict` emits |

Three decisions worth stating:

- **Reasons are tagged with `ReasonKind`**, the domain's own enum, rather
  than a second serialization vocabulary. `AlertRecord.reasons` is the audit
  trail for why the community was alerted, and change 3's `--json` output
  emits it for threshold calibration, so it stays structured and numeric —
  never rendered prose.
- **An unknown tag raises.** A state file written by a newer version must not
  be read as though a reason simply were not there: the audit trail would
  silently lie about why an alert went out. The local state file is
  disposable and gitignored, so failing loudly costs little.
- **`AlertRecord.level` and `AlertRecord.message.level` are stored once.**
  design.md 4.1 lists a single `level` attribute, and `RunAlertCycle` only
  ever produces records where the two agree (the composer is handed the
  decided level and copies it). The message level is therefore restored from
  the record level rather than duplicated in the document.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, assert_never

from rain_alert.domain.messages import AlertMessage, AlertRecord, OutageRecord
from rain_alert.domain.reasons import (
    ForecastThresholdReason,
    NoQualifyingWarningReason,
    Reason,
    ReasonKind,
    SenamhiUnavailableReason,
    WarningReason,
)
from rain_alert.domain.values import Level, SourceName, TimeWindow, WarningLevel


def _moment(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO 8601 string, got {value!r}")
    return datetime.fromisoformat(value)


def reason_to_dict(reason: Reason) -> dict[str, Any]:
    """One structured reason as a tagged document."""
    match reason:
        case WarningReason(level=level, title=title):
            return {"kind": ReasonKind.OFFICIAL_WARNING.value, "level": level.value, "title": title}
        case ForecastThresholdReason(
            accumulated_mm=accumulated,
            hours=hours,
            probability_pct=probability,
            probability_threshold_pct=threshold,
        ):
            return {
                "kind": ReasonKind.FORECAST_THRESHOLD.value,
                "accumulated_mm": accumulated,
                "hours": hours,
                "probability_pct": probability,
                "probability_threshold_pct": threshold,
            }
        case SenamhiUnavailableReason():
            return {"kind": ReasonKind.SENAMHI_UNAVAILABLE.value}
        case NoQualifyingWarningReason():
            return {"kind": ReasonKind.NO_QUALIFYING_WARNING.value}
        case _:  # pragma: no cover - exhaustiveness guard, as in render_reason_en
            # Without this the `match` falls through to an implicit `None`,
            # which `mypy --strict` does not flag. A future variant would
            # write `null` into the state file and into change 3's DynamoDB
            # audit trail, and the *next read* would crash with
            # `AttributeError` — far from the cause, with the audit trail
            # already corrupted.
            assert_never(reason)


def reason_from_dict(document: dict[str, Any]) -> Reason:
    """A tagged document back into its `Reason` variant.

    Raises:
        ValueError: When the tag is missing or unknown — a reason that cannot
            be read must not be silently dropped from the audit trail.
    """
    raw = document.get("kind")
    if not isinstance(raw, str):
        raise ValueError(f"unknown reason kind {raw!r}: the tag is missing or not a string")
    try:
        kind = ReasonKind(raw)
    except ValueError as exc:
        raise ValueError(f"unknown reason kind {raw!r}") from exc

    match kind:
        case ReasonKind.OFFICIAL_WARNING:
            return WarningReason(level=WarningLevel(document["level"]), title=document["title"])
        case ReasonKind.FORECAST_THRESHOLD:
            return ForecastThresholdReason(
                accumulated_mm=float(document["accumulated_mm"]),
                hours=int(document["hours"]),
                probability_pct=int(document["probability_pct"]),
                probability_threshold_pct=(
                    None
                    if document.get("probability_threshold_pct") is None
                    else int(document["probability_threshold_pct"])
                ),
            )
        case ReasonKind.SENAMHI_UNAVAILABLE:
            return SenamhiUnavailableReason()
        case ReasonKind.NO_QUALIFYING_WARNING:
            return NoQualifyingWarningReason()


def alert_record_to_dict(record: AlertRecord) -> dict[str, Any]:
    """A sent-alert record as the document design.md 4.1 pins."""
    return {
        "city_slug": record.city_slug,
        "level": Level(record.level).value,
        # The DynamoDB sort key, so it must be sortable UTC ISO 8601.
        "window_start": record.window.start.isoformat(),
        "window_end": record.window.end.isoformat(),
        "sent_at": record.sent_at.isoformat(),
        "title": record.message.title,
        "body": record.message.body,
        "valid_until": record.message.valid_until.isoformat(),
        "reasons": [reason_to_dict(reason) for reason in record.reasons],
        "senamhi_status": record.senamhi_status,
        "open_meteo_status": record.open_meteo_status,
        "composer": record.composer,
    }


def alert_record_from_dict(document: dict[str, Any]) -> AlertRecord:
    """The inverse of `alert_record_to_dict`."""
    level = Level(document["level"])
    return AlertRecord(
        city_slug=document["city_slug"],
        level=level,
        window=TimeWindow(
            start=_moment(document["window_start"], "window_start"),
            end=_moment(document["window_end"], "window_end"),
        ),
        sent_at=_moment(document["sent_at"], "sent_at"),
        message=AlertMessage(
            title=document["title"],
            body=document["body"],
            level=level,
            valid_until=_moment(document["valid_until"], "valid_until"),
        ),
        reasons=tuple(reason_from_dict(reason) for reason in document.get("reasons", ())),
        senamhi_status=document["senamhi_status"],
        open_meteo_status=document["open_meteo_status"],
        composer=document["composer"],
    )


def outage_record_to_dict(record: OutageRecord) -> dict[str, Any]:
    """The active-outage record as the document design.md 4.1 pins.

    The source set is sorted: a `frozenset` has no order, and an unstable
    document makes every diff of the state file noise.
    """
    return {
        "city_slug": record.city_slug,
        "unavailable_sources": sorted(source.value for source in record.unavailable_sources),
        "opened_at": record.opened_at.isoformat(),
        "notified_at": None if record.notified_at is None else record.notified_at.isoformat(),
    }


def outage_record_from_dict(document: dict[str, Any]) -> OutageRecord:
    """The inverse of `outage_record_to_dict`.

    `notified_at` stays `None` when it was `None`: that state means "still
    owed a notice" (design.md 6), so inventing a timestamp here would lose
    the operator's notice forever.
    """
    notified = document.get("notified_at")
    return OutageRecord(
        city_slug=document["city_slug"],
        unavailable_sources=frozenset(SourceName(name) for name in document["unavailable_sources"]),
        opened_at=_moment(document["opened_at"], "opened_at"),
        notified_at=None if notified is None else _moment(notified, "notified_at"),
    )
