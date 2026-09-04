"""Domain-wide enums and value objects (design.md section 3, D1).

Frozen stdlib dataclasses and `StrEnum` only. No third-party imports are
allowed in this package (see tests/architecture/test_layer_boundaries.py).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Level(StrEnum):
    """Risk level assessed for the current cycle."""

    NONE = "none"
    PREPARE = "prepare"
    IMMINENT = "imminent"


class WarningLevel(StrEnum):
    """Official SENAMHI warning severity."""

    YELLOW = "yellow"
    ORANGE = "orange"
    RED = "red"


class SourceName(StrEnum):
    """External data sources this system consumes."""

    SENAMHI = "senamhi"
    OPEN_METEO = "open_meteo"


class NoticeKind(StrEnum):
    """Operator-facing technical notice types (source-outage-notices spec)."""

    SOURCE_UNAVAILABLE = "source_unavailable"
    SOURCES_RECOVERED = "sources_recovered"
    # change 2 adds: AGENT_FALLBACK_USED = "agent_fallback_used"


class UnavailableReason(StrEnum):
    """Machine-usable reason a `SourceResult` is `Unavailable`."""

    TRANSPORT_ERROR = "transport_error"
    TIMEOUT = "timeout"
    BAD_STATUS = "bad_status"
    MALFORMED_PAYLOAD = "malformed_payload"
    STRUCTURE_UNRECOGNIZED = "structure_unrecognized"
    NO_ROWS_EXTRACTED = "no_rows_extracted"
    INSUFFICIENT_HORIZON = "insufficient_horizon"


LEVEL_RANK: Mapping[Level, int] = {Level.NONE: 0, Level.PREPARE: 1, Level.IMMINENT: 2}


def is_escalation(previous: Level, candidate: Level) -> bool:
    """True when `candidate` ranks strictly higher than `previous`."""
    return LEVEL_RANK[candidate] > LEVEL_RANK[previous]


@dataclass(frozen=True, slots=True)
class Coordinates:
    """Geographic coordinates, range-checked on construction."""

    latitude: float
    longitude: float

    def __post_init__(self) -> None:
        if not -90.0 <= self.latitude <= 90.0:
            raise ValueError(f"latitude out of range [-90, 90]: {self.latitude}")
        if not -180.0 <= self.longitude <= 180.0:
            raise ValueError(f"longitude out of range [-180, 180]: {self.longitude}")


@dataclass(frozen=True, slots=True)
class TimeWindow:
    """A UTC-aware time span; `end` must be strictly after `start`."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("TimeWindow requires timezone-aware datetimes")
        if self.end <= self.start:
            raise ValueError("TimeWindow.end must be strictly after start")

    def overlaps(self, other: TimeWindow) -> bool:
        """True when this window shares at least one instant with `other`."""
        return self.start < other.end and other.start < self.end

    def key(self) -> str:
        """The dedup sort key: the window start as ISO 8601 UTC."""
        return self.start.isoformat()
