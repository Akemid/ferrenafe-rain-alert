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
    """A source that answered successfully."""

    data: T
    fetched_at: datetime


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
