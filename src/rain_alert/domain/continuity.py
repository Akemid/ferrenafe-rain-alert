"""`ContinuitySummary`: what the community has already been told, as facts.

A pure value derived from recorded alerts (alert-cycle spec; design D2, D3).
It imports stdlib and domain types only: no port, no clock, no I/O. The
application reads the records through `AlertRepository` and hands them here.

**One of two copies, pinned by one fixture.** `agent/history.py` computes the
same facts on the other side of an import boundary that must not be crossed
(`agent/` never imports `rain_alert`). `contracts/recent-alerts.json` holds the
fixture cases and both test suites assert them, so neither copy can change
alone. The semantics here are therefore exactly the agent's:

- Only alerts with `as_of - LOOKBACK_DAYS <= sent_at < as_of` count.
- `consecutive_days` is the run of consecutive local calendar dates ending at
  `as_of`'s own local date. Today counts whenever there is a qualifying alert;
  every earlier date needs at least one. Several alerts on one date count once.
- `trend` compares the current level and warning level with those of the most
  recent qualifying alert, and `escalated` is `trend == RISING`.

**What a summary is for.** The validator accepts a day count or a prior date in
an agent's body only when it equals something here (design D10). The empty
summary therefore allows none, which is also what a failed history read yields
(design D7): the send is already authorized, history must never block it, and a
model that claims continuity without any is rejected rather than believed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from enum import StrEnum

from rain_alert.domain.messages import AlertRecord
from rain_alert.domain.reasons import Reason, WarningReason
from rain_alert.domain.values import LEVEL_RANK, Level, WarningLevel

#: Pinned by `contracts/recent-alerts.json` (`lookback_days`).
LOOKBACK_DAYS = 7

_WARNING_RANK: Mapping[WarningLevel, int] = {
    WarningLevel.YELLOW: 1,
    WarningLevel.ORANGE: 2,
    WarningLevel.RED: 3,
}


class Trend(StrEnum):
    """How the current situation compares with the most recent earlier alert."""

    RISING = "rising"
    STEADY = "steady"
    FALLING = "falling"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class PriorAlert:
    """One earlier alert, reduced to the facts a body may restate.

    `sent_at` is the stored instant. The validator renders it in the request's
    own timezone, so the summary carries no zone-dependent text.
    """

    sent_at: datetime
    level: Level
    warning_level: WarningLevel | None


@dataclass(frozen=True, slots=True)
class ContinuitySummary:
    """Continuity facts for one message request. The default is the empty summary."""

    consecutive_days: int = 0
    previous_level: Level | None = None
    escalated: bool = False
    trend: Trend = Trend.NONE
    #: Every qualifying alert, newest first. The agent's tool lists at most five
    #: of them; accepting the superset is deliberate, because what a body may
    #: restate is bounded by what the model was shown, never by this tuple.
    prior_alerts: tuple[PriorAlert, ...] = ()


EMPTY_CONTINUITY = ContinuitySummary()


def top_warning_level(reasons: Iterable[Reason]) -> WarningLevel | None:
    """The highest official-warning level among `reasons`, if any."""
    levels = [reason.level for reason in reasons if isinstance(reason, WarningReason)]
    return max(levels, key=_WARNING_RANK.__getitem__, default=None)


def _rank(level: Level, warning_level: WarningLevel | None) -> tuple[int, int]:
    return (LEVEL_RANK[level], 0 if warning_level is None else _WARNING_RANK[warning_level])


def summarize(
    records: Iterable[AlertRecord],
    *,
    as_of: datetime,
    tz: tzinfo,
    current_level: Level,
    current_warning_level: WarningLevel | None,
) -> ContinuitySummary:
    """The summary of `records` as seen from `as_of`, in the city's timezone `tz`.

    Total over any iterable of records and independent of their order. Records
    of another city are the caller's concern: the repository query is already
    keyed by city.
    """
    cutoff = as_of - timedelta(days=LOOKBACK_DAYS)
    qualifying = sorted(
        (record for record in records if cutoff <= record.sent_at < as_of),
        key=lambda record: record.sent_at,
        reverse=True,
    )
    if not qualifying:
        return EMPTY_CONTINUITY

    today = as_of.astimezone(tz).date()
    alert_dates = {record.sent_at.astimezone(tz).date() for record in qualifying}
    consecutive = 1
    while today - timedelta(days=consecutive) in alert_dates:
        consecutive += 1

    prior = [
        PriorAlert(sent_at=record.sent_at, level=record.level, warning_level=top_warning_level(record.reasons))
        for record in qualifying
    ]
    previous = prior[0]
    current_rank = _rank(current_level, current_warning_level)
    previous_rank = _rank(previous.level, previous.warning_level)
    if current_rank > previous_rank:
        trend = Trend.RISING
    elif current_rank < previous_rank:
        trend = Trend.FALLING
    else:
        trend = Trend.STEADY

    return ContinuitySummary(
        consecutive_days=consecutive,
        previous_level=previous.level,
        escalated=trend is Trend.RISING,
        trend=trend,
        prior_alerts=tuple(prior),
    )
