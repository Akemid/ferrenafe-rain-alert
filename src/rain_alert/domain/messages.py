"""Message and record types (design.md section 3, mirrors design spec 7.2/7.4
so change 2's LLM composer maps to `MessageRequest` 1:1)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from rain_alert.domain.reasons import Reason
from rain_alert.domain.values import ComposerName, Level, NoticeKind, SourceName, TimeWindow, WarningLevel


@dataclass(frozen=True, slots=True)
class ForecastSummary:
    """The subset of `Forecast` the composer needs — never the raw series."""

    mm_24h: float
    mm_48h: float
    peak_at: datetime
    peak_probability_pct: int


@dataclass(frozen=True, slots=True)
class WarningSummary:
    """The subset of `Warning` the composer needs."""

    source_id: str
    level: WarningLevel
    title: str
    window: TimeWindow


@dataclass(frozen=True, slots=True)
class MessageRequest:
    """The composer's ONLY input; the decided level is already fixed (D11)."""

    city: str
    timezone: str
    level: Level
    window: TimeWindow
    reasons: tuple[Reason, ...]
    forecast: ForecastSummary | None
    warning: WarningSummary | None
    senamhi_status: str
    open_meteo_status: str
    checklist: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AlertMessage:
    """A composed, ready-to-send community message.

    `composed_by` is provenance, and it rides on the message on purpose
    (D13). The `MessageComposer` port must not change and `RunAlertCycle`
    must not grow a branch per composer, so the return value is the only
    channel left from composer to use case; "who wrote this text" is also
    genuinely a property of the text.

    It is **defaulted**, so the field is purely additive for every
    construction site already merged, and the default is the conservative
    value — a composer that forgets to set it under-claims agent involvement
    rather than over-claiming it. Value equality now includes it, which is
    what makes "the fallback is byte-identical to the template's output *and*
    attributed to the template" a single assertion.
    """

    title: str
    body: str
    level: Level
    valid_until: datetime
    composed_by: ComposerName = ComposerName.TEMPLATE


@dataclass(frozen=True, slots=True)
class AlertRecord:
    """A durable record of a sent community alert (dedup lookback data)."""

    city_slug: str
    level: Level
    window: TimeWindow
    sent_at: datetime
    message: AlertMessage
    reasons: tuple[Reason, ...]
    senamhi_status: str
    open_meteo_status: str
    composer: str  # "template" | "agent"


@dataclass(frozen=True, slots=True)
class OutageRecord:
    """At most one active outage per city (design.md section 6)."""

    city_slug: str
    unavailable_sources: frozenset[SourceName]
    opened_at: datetime
    notified_at: datetime | None

    def is_dual(self) -> bool:
        """True when both known sources are unavailable at once."""
        return len(self.unavailable_sources) >= 2


@dataclass(frozen=True, slots=True)
class OperatorNotice:
    """A technical notice for the operator, distinct from a community alert."""

    kind: NoticeKind
    subject: str
    body: str
    sources: frozenset[SourceName]
    occurred_at: datetime
