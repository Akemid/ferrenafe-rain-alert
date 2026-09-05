"""`SourceResult` — availability as a tagged union, not optional data (D2).

`Unavailable` has no `data` attribute, so unavailable data is unreadable by
construction; `match` narrows cleanly between the two shapes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from rain_alert.domain.values import SourceName, UnavailableReason


@dataclass(frozen=True, slots=True)
class Available[T]:
    """A source that answered successfully.

    `notes` carries what the source *set aside* on the way to answering:
    rows it could not parse, warnings its own filter removed. A source can
    succeed and still owe the operator an explanation — a page whose only
    warning in force was filtered out and a page with genuinely nothing in
    force produce identical `data`, and only one of them means the system is
    idle. Operator-facing English, never recipient-facing text.

    It defaults to empty so every existing construction site is unaffected: a
    source with nothing to report says nothing.
    """

    data: T
    fetched_at: datetime
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Unavailable:
    """A source that could not be read this cycle."""

    source: SourceName
    reason: UnavailableReason
    detail: str
    observed_at: datetime


type SourceResult[T] = Available[T] | Unavailable


def status_of(result: SourceResult[Any]) -> str:
    """`"available"` or `"unavailable"`, for logging and CLI display."""
    return "available" if isinstance(result, Available) else "unavailable"
