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

Plus one row the eight-row table left implicit: an active record whose set is
unchanged but whose `notified_at` is `None` is still owed its notice, so it is
notified now and stamped. Dedup on set equality alone would have lost that
notice forever.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from rain_alert.domain.messages import OperatorNotice, OutageRecord
from rain_alert.domain.sanitize import sanitize_source_text
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


#: Per-source failure detail is third-party text — an exception message, and
#: sometimes a server's own response — so it is bounded before it reaches an
#: operator's log, the same treatment `ssm_config_repository.py::_echo` gives
#: an operator-supplied parameter value. 160 characters is enough for an HTTP
#: status with a host, or a TLS error with a reason, and far short of a page
#: of HTML pasted into CloudWatch.
_MAX_DETAIL_LENGTH = 160


def _failure_lines(sources: frozenset[SourceName], results: Mapping[SourceName, SourceResult[Any]]) -> str:
    """One line per source, naming the reason and the detail it carried.

    `Unavailable` has always held both — `adapters/http.py` translates every
    transport fault into one precisely so the cause survives the seam — and
    this notice discarded them, so an operator read "senamhi became
    unavailable" and could not tell a timeout from a block from a parse
    failure. The first live Lambda invocation degraded on SENAMHI three times
    out of three, and the logs could not say why.
    """
    lines = []
    for source in sorted(sources, key=lambda s: s.value):
        result = results.get(source)
        if not isinstance(result, Unavailable):
            continue
        detail = sanitize_source_text(result.detail)[:_MAX_DETAIL_LENGTH]
        lines.append(f"- {source.value}: {result.reason.value} — {detail}")
    return "\n".join(lines)


def _unavailable_notice(
    sources: frozenset[SourceName], now: datetime, results: Mapping[SourceName, SourceResult[Any]]
) -> OperatorNotice:
    names = _source_list(sources)
    body = f"{names} became unavailable as of {now.isoformat()}."
    lines = _failure_lines(sources, results)
    if lines:
        body = f"{body}\n{lines}"
    return OperatorNotice(
        kind=NoticeKind.SOURCE_UNAVAILABLE,
        subject=f"Source(s) unavailable: {names}",
        body=body,
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
    results: Mapping[SourceName, SourceResult[Any]] = {SourceName.SENAMHI: senamhi, SourceName.OPEN_METEO: open_meteo}
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
        if active is not None and active.notified_at is None:
            # The set is unchanged, so this is the dedup branch — but dedup on
            # set equality alone would drop this outage's notice forever. A
            # record can be persisted with `notified_at=None` (change 3's
            # adapter writes state and sends notices as separate steps), so an
            # un-notified record means "still owed a notice", not "already
            # told the operator".
            notice = _unavailable_notice(unavailable_now, now, results)
            notified = OutageRecord(
                city_slug=active.city_slug,
                unavailable_sources=active.unavailable_sources,
                opened_at=active.opened_at,
                notified_at=now,
            )
            return OutageDecision(
                notices=(notice,), next_state=notified, state_changed=True, suppress_community_alert=suppress
            )
        return OutageDecision(notices=(), next_state=active, state_changed=False, suppress_community_alert=suppress)

    notices: list[OperatorNotice] = []
    if recovered:
        notices.append(_recovery_notice(recovered, now))
    unavailable_notice_sources = unavailable_now if recovered else newly_down
    notices.append(_unavailable_notice(unavailable_notice_sources, now, results))

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
