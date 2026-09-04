"""evaluate_outage — the dual-outage rule (design.md section 6;
source-outage-notices spec). Pure: the active/next `OutageRecord` is passed
in and returned as data. The state lives nowhere in the domain —
`RunAlertCycle` reads and writes it via `AlertRepository`.

Eight-row state-transition table (design.md section 6) is the full spec of
this function's behaviour:

| Unavailable now | Active record        | Notices                                  | next_state          |
|---|---|---|---|
| ∅       | none                  | —                                         | none                 |
| ∅       | present               | one SOURCES_RECOVERED                    | cleared              |
| {A}     | none                  | one SOURCE_UNAVAILABLE naming A          | open, notified now   |
| {A}     | same set, notified    | — (dedup)                                | unchanged            |
| {A, B}  | none                  | one SOURCE_UNAVAILABLE naming both       | open dual, notified  |
| {A, B}  | same set, notified    | — (dedup)                                | unchanged            |
| {A}     | {A, B}                | SOURCES_RECOVERED(B) + SOURCE_UNAVAILABLE(A) | narrowed to {A}  |
| {A, B}  | {A}                   | one SOURCE_UNAVAILABLE naming the newly-down B | widened to {A, B} |
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from rain_alert.domain.messages import OperatorNotice, OutageRecord
from rain_alert.domain.sources import SourceResult, Unavailable
from rain_alert.domain.values import NoticeKind, SourceName


@dataclass(frozen=True, slots=True)
class OutageDecision:
    """The notices to send and the next outage state to persist."""

    notices: tuple[OperatorNotice, ...]
    next_state: OutageRecord | None  # None => clear
    state_changed: bool
    suppress_community_alert: bool  # True iff both sources unavailable


def _source_list(sources: frozenset[SourceName]) -> str:
    return ", ".join(sorted(source.value for source in sources))


def _recovery_notice(sources: frozenset[SourceName], now: datetime) -> OperatorNotice:
    names = _source_list(sources)
    return OperatorNotice(
        kind=NoticeKind.SOURCES_RECOVERED,
        subject=f"Source(s) recovered: {names}",
        body=f"{names} became available again as of {now.isoformat()}.",
        sources=sources,
        occurred_at=now,
    )


def _unavailable_notice(sources: frozenset[SourceName], now: datetime) -> OperatorNotice:
    names = _source_list(sources)
    return OperatorNotice(
        kind=NoticeKind.SOURCE_UNAVAILABLE,
        subject=f"Source(s) unavailable: {names}",
        body=f"{names} became unavailable as of {now.isoformat()}.",
        sources=sources,
        occurred_at=now,
    )


def evaluate_outage(
    senamhi: SourceResult[Any],
    open_meteo: SourceResult[Any],
    active: OutageRecord | None,
    city_slug: str,
    now: datetime,
) -> OutageDecision:
    """Decide notices and the next outage state for this cycle."""
    unavailable_now = frozenset(
        source
        for source, result in ((SourceName.SENAMHI, senamhi), (SourceName.OPEN_METEO, open_meteo))
        if isinstance(result, Unavailable)
    )
    previously_unavailable = active.unavailable_sources if active is not None else frozenset[SourceName]()
    recovered = previously_unavailable - unavailable_now
    newly_down = unavailable_now - previously_unavailable
    suppress = len(unavailable_now) >= 2

    if not unavailable_now:
        if active is None:
            return OutageDecision(notices=(), next_state=None, state_changed=False, suppress_community_alert=False)
        notice = _recovery_notice(recovered, now)
        return OutageDecision(notices=(notice,), next_state=None, state_changed=True, suppress_community_alert=False)

    if not recovered and not newly_down:
        return OutageDecision(notices=(), next_state=active, state_changed=False, suppress_community_alert=suppress)

    notices: list[OperatorNotice] = []
    if recovered:
        notices.append(_recovery_notice(recovered, now))
    unavailable_notice_sources = unavailable_now if recovered else newly_down
    notices.append(_unavailable_notice(unavailable_notice_sources, now))

    opened_at = active.opened_at if active is not None else now
    next_state = OutageRecord(
        city_slug=city_slug,
        unavailable_sources=unavailable_now,
        opened_at=opened_at,
        notified_at=now,
    )
    return OutageDecision(
        notices=tuple(notices), next_state=next_state, state_changed=True, suppress_community_alert=suppress
    )
