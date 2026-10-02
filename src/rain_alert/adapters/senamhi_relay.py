"""The relay `WarningProvider`: SENAMHI warnings read from one S3 object.

SENAMHI blocks AWS egress (docs/evidence/2026-10-02-senamhi-unreachable-from-aws.md),
so an off-AWS producer pushes the page into S3 and the Lambda reads it from
there. Constants below mirror `contracts/senamhi-relay.json`; a test pins them
equal, because a one-character disagreement between the grant and the reader
fails as `AccessDenied` on every cycle (design.md D37).
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from rain_alert.adapters.http import FetchError
from rain_alert.adapters.senamhi_scraper import parse_notes, parse_warnings_page
from rain_alert.domain.entities import Warning
from rain_alert.domain.sanitize import sanitize_source_text
from rain_alert.domain.sources import Available, SourceResult, Unavailable
from rain_alert.domain.values import SourceName, UnavailableReason

#: The single object the Lambda may read. Not configurable by env: the IAM grant
#: names exactly this key, so an override could only ever produce a denial.
OBJECT_KEY = "senamhi/lambayeque/latest.html"

#: At or beyond this age the object is stale, and a stale page is never parsed.
MAX_AGE = timedelta(seconds=10800)

#: Largest object the reader accepts, and the producer uploads (4 MiB).
MAX_OBJECT_BYTES = 4194304

#: Producer-vs-S3 clock disagreement above which the operator is told.
SKEW_TOLERANCE = timedelta(seconds=900)

#: How far ahead of the cycle clock a `LastModified` may sit and still be clock
#: jitter. Beyond it the timestamp cannot be trusted and the object is unreadable.
FUTURE_TOLERANCE = timedelta(seconds=60)


@dataclass(frozen=True, slots=True)
class RelayObject:
    """What one read of the relay object produced.

    `last_modified` is S3's server clock and is the only authority on
    freshness (R1). `claimed_fetched_at` is what the producer says it fetched
    at: informational, used only for the skew note.
    """

    text: str
    last_modified: datetime
    version_id: str | None
    claimed_fetched_at: datetime | None


class RelayObjectReader(Protocol):
    """The adapter-internal seam under `RelayWarningProvider`, like `HtmlFetcher`.

    Raises:
        FetchError: `RELAY_MISSING` or `RELAY_UNREADABLE`.
    """

    def read(self) -> RelayObject: ...


def relay_age(last_modified: datetime, now: datetime) -> timedelta:
    """How old the object is by the cycle clock, never negative.

    `last_modified` is S3's own clock, and `now` is the injected cycle clock
    truncated to whole seconds, so a just-written object can sit up to a
    second ahead of it. That is age zero, not a negative age.
    """
    return max(now - last_modified, timedelta(0))


def is_stale(age: timedelta, max_age: timedelta) -> bool:
    """Whether `age` has reached the limit. Inclusive: exactly `max_age` is stale."""
    return age >= max_age


def skew(claimed: datetime | None, last_modified: datetime) -> timedelta | None:
    """The producer's claimed fetch time minus S3's `LastModified`, signed.

    `None` when the producer made no claim. Positive means the producer's
    clock runs ahead of S3's. The claim never decides freshness (R1).
    """
    if claimed is None:
        return None
    return claimed - last_modified


def _signed_minutes(delta: timedelta) -> str:
    return f"{round(delta.total_seconds() / 60):+d}m"


def skew_note(claimed: datetime | None, last_modified: datetime, tolerance: timedelta) -> str | None:
    """An operator note when the producer clock disagrees beyond `tolerance`, else `None`."""
    drift = skew(claimed, last_modified)
    if drift is None or abs(drift) <= tolerance:
        return None
    return f"producer clock skew {_signed_minutes(drift)} versus S3 LastModified"


#: The operator notice caps every per-source detail at 160 characters. Capping
#: here too keeps the VersionId, which leads, ahead of any truncation.
MAX_DETAIL_LENGTH = 160

#: S3 version ids are about 32 characters. The cap only bounds a hostile value.
_MAX_VERSION_ID_LENGTH = 100


def _duration(delta: timedelta) -> str:
    """`4h12m`, `3h`, `45m`: whole minutes, hours omitted when zero."""
    minutes = int(delta.total_seconds() // 60)
    hours, minutes = divmod(minutes, 60)
    if hours == 0:
        return f"{minutes}m"
    return f"{hours}h" if minutes == 0 else f"{hours}h{minutes}m"


def relay_detail(
    *,
    version_id: str | None,
    age: timedelta,
    max_age: timedelta,
    last_modified: datetime,
    skew: timedelta | None,
) -> str:
    """The operator-facing detail for a stale relay object.

    Only the VersionId and numbers appear: never page content. The VersionId
    is S3-supplied, so it is sanitized and bounded like any other source text.
    """
    parts: list[str] = []
    if version_id is not None:
        parts.append(f"version={sanitize_source_text(version_id)[:_MAX_VERSION_ID_LENGTH]}")
    parts.append(f"age={_duration(age)}")
    parts.append(f"limit={_duration(max_age)}")
    parts.append(f"modified={last_modified.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}")
    if skew is not None:
        parts.append(f"skew={_signed_minutes(skew)}")
    return " ".join(parts)[:MAX_DETAIL_LENGTH]


def _is_aware(moment: datetime) -> bool:
    return moment.tzinfo is not None and moment.utcoffset() is not None


def _whole_seconds(delta: timedelta) -> int:
    return int(delta.total_seconds())


def _optional_seconds(delta: timedelta | None) -> int | None:
    return None if delta is None else _whole_seconds(delta)


@dataclass(frozen=True, slots=True)
class RelayReadRecord:
    """The facts of one relay read, handed to `on_read` exactly once.

    `degraded` is `1` for **every** `Unavailable` the provider returns,
    including a parse failure of a fresh object (design.md D40). It is the
    value the CloudWatch metric filter publishes, so the object facts stay
    `None` when the read never produced an object.
    """

    degraded: int
    reason: UnavailableReason | None
    version_id: str | None
    last_modified: datetime | None
    age_seconds: int | None
    skew_seconds: int | None


class RelayWarningProvider:
    """`WarningProvider` over one S3 object that an off-AWS producer keeps fresh."""

    def __init__(
        self,
        reader: RelayObjectReader,
        *,
        on_read: Callable[[RelayReadRecord], None] = lambda _: None,
        max_age: timedelta = MAX_AGE,
        skew_tolerance: timedelta = SKEW_TOLERANCE,
        future_tolerance: timedelta = FUTURE_TOLERANCE,
    ) -> None:
        self._reader = reader
        self._on_read = on_read
        self._max_age = max_age
        self._skew_tolerance = skew_tolerance
        self._future_tolerance = future_tolerance

    def fetch_current_warnings(self, region: str, now: datetime) -> SourceResult[tuple[Warning, ...]]:
        """Read, freshness-check and parse the relay object. No exception escapes.

        Timestamps must be timezone-aware. A naive cycle clock or a naive
        `last_modified` is `RELAY_UNREADABLE`; a naive `claimed_fetched_at` is
        informational only and is ignored (no skew computed).

        Every path emits exactly one `RelayReadRecord` through `on_read`, so a
        degraded cycle is always visible to the alarm.
        """
        if not _is_aware(now):
            return self._failed(now, UnavailableReason.RELAY_UNREADABLE, "cycle clock has no timezone")
        try:
            obj = self._reader.read()
        except FetchError as exc:
            return self._failed(now, exc.reason, exc.detail)
        except Exception as exc:  # noqa: BLE001 - nothing may escape: the cycle has no handler
            return self._failed(now, UnavailableReason.RELAY_UNREADABLE, f"unexpected {type(exc).__name__}: {exc}")

        if not _is_aware(obj.last_modified):
            return self._failed(now, UnavailableReason.RELAY_UNREADABLE, "relay LastModified has no timezone")
        ahead = obj.last_modified - now
        if ahead > self._future_tolerance:
            version = (
                f"version={sanitize_source_text(obj.version_id)[:_MAX_VERSION_ID_LENGTH]} " if obj.version_id else ""
            )
            detail = f"{version}relay LastModified is {_duration(ahead)} ahead of the cycle clock"[:MAX_DETAIL_LENGTH]
            return self._failed(now, UnavailableReason.RELAY_UNREADABLE, detail, obj)
        # A naive producer claim is informational and cannot be compared with
        # S3's clock, so it is ignored: no skew is computed or reported.
        claimed = obj.claimed_fetched_at if obj.claimed_fetched_at and _is_aware(obj.claimed_fetched_at) else None
        age = relay_age(obj.last_modified, now)
        drift = skew(claimed, obj.last_modified)
        if is_stale(age, self._max_age):
            detail = relay_detail(
                version_id=obj.version_id,
                age=age,
                max_age=self._max_age,
                last_modified=obj.last_modified,
                skew=drift,
            )
            return self._failed(now, UnavailableReason.STALE_RELAY, detail, obj, age, drift)

        try:
            outcome = parse_warnings_page(obj.text, region, now)
        except FetchError as exc:
            return self._failed(now, exc.reason, exc.detail, obj, age, drift)
        except Exception as exc:  # noqa: BLE001 - a parser bug must degrade the cycle, not abort it
            return self._failed(
                now, UnavailableReason.TRANSPORT_ERROR, f"unexpected {type(exc).__name__}: {exc}", obj, age, drift
            )

        notes = parse_notes(outcome)
        extra = skew_note(claimed, obj.last_modified, self._skew_tolerance)
        if extra is not None:
            notes = (*notes, extra)
        self._emit(
            RelayReadRecord(
                degraded=0,
                reason=None,
                version_id=obj.version_id,
                last_modified=obj.last_modified,
                age_seconds=_whole_seconds(age),
                skew_seconds=_optional_seconds(drift),
            )
        )
        return Available(data=outcome.warnings, fetched_at=min(obj.last_modified, now), notes=notes)

    def _failed(
        self,
        now: datetime,
        reason: UnavailableReason,
        detail: str,
        obj: RelayObject | None = None,
        age: timedelta | None = None,
        drift: timedelta | None = None,
    ) -> Unavailable:
        self._emit(
            RelayReadRecord(
                degraded=1,
                reason=reason,
                version_id=obj.version_id if obj is not None else None,
                last_modified=obj.last_modified if obj is not None else None,
                age_seconds=_optional_seconds(age),
                skew_seconds=_optional_seconds(drift),
            )
        )
        return Unavailable(source=SourceName.SENAMHI, reason=reason, detail=detail, observed_at=now)

    def _emit(self, record: RelayReadRecord) -> None:
        """Hand the record to `on_read`; a failing callback never escapes.

        The callback is observability. If it raised, the cycle would abort and
        skip Open-Meteo, so the failure is reduced to one line on stderr.
        """
        try:
            self._on_read(record)
        except Exception as exc:  # noqa: BLE001 - observability must not abort the cycle
            print(f"senamhi relay on_read callback failed: {type(exc).__name__}", file=sys.stderr)
