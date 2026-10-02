"""The relay `WarningProvider`: SENAMHI warnings read from one S3 object.

SENAMHI blocks AWS egress (docs/evidence/2026-10-02-senamhi-unreachable-from-aws.md),
so an off-AWS producer pushes the page into S3 and the Lambda reads it from
there. Constants below mirror `contracts/senamhi-relay.json`; a test pins them
equal, because a one-character disagreement between the grant and the reader
fails as `AccessDenied` on every cycle (design.md D37).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from rain_alert.domain.sanitize import sanitize_source_text

#: The single object the Lambda may read. Not configurable by env: the IAM grant
#: names exactly this key, so an override could only ever produce a denial.
OBJECT_KEY = "senamhi/lambayeque/latest.html"

#: At or beyond this age the object is stale, and a stale page is never parsed.
MAX_AGE = timedelta(seconds=10800)

#: Largest object the reader accepts, and the producer uploads (4 MiB).
MAX_OBJECT_BYTES = 4194304

#: Producer-vs-S3 clock disagreement above which the operator is told.
SKEW_TOLERANCE = timedelta(seconds=900)


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
