"""Recent-alert history for the composer agent: binding, facts, reader, tool.

**The model chooses a count, never a place.** The tool the agent sees takes one
argument, `n`. Which city's alerts are read comes from the *binding* the
application wrote into the payload (`HISTORY_KEY`), parsed here before any
model turn and closed over by the tool. Nothing the model emits, and nothing in
the fenced prompt, can name a partition (design D1).

**Every continuity fact is computed here, in code** (design D3). The model is
handed `consecutive_days`, `previous_level`, `escalated` and `trend`; it is
never asked to count days or compare levels. The same facts are computed a
second time on the application side (`rain_alert/domain/continuity.py`) because
`agent/` must not import `rain_alert`; `contracts/recent-alerts.json` pins both
copies with one fixture (design D2). The constants below are therefore
duplicated on purpose, and `tests/test_history_contract.py` fails if they drift.

**History is optional, and its absence is never an error** (design D6, D7). A
missing or malformed binding, a store that is slow or denied, a record that does
not parse: each degrades to the empty history. A missing history must never cost
the community its alert.

**What the tool never returns** (design D4): a prior alert's title, body or
rainfall amount. Titles are scraped text, and amounts would widen what the
validator has to accept. The query itself projects only the attributes it needs,
and the IAM grant (design D8) denies the rest even if this code were wrong.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, Protocol

from botocore.config import Config
from botocore.session import get_session

logger = logging.getLogger(__name__)

TABLE_NAME = "ferrenafe-alerts-sent"
KEY_PREFIX = "ALERT#"
TOOL_NAME = "get_recent_alerts"
LOOKBACK_DAYS = 7
MAX_N = 5
LOCAL_FORMAT = "%d/%m/%Y %H:%M"

#: The two partitions a binding may name. Anything else is "no history": the
#: payload is written by an IAM-authenticated invoker, but the parser does not
#: rely on that, and the IAM grant (design D8) allows only these two leading keys.
ALLOWED_PARTITIONS = frozenset({"ferrenafe", "demo-ferrenafe"})

#: America/Lima is UTC-5 all year: Peru has not observed daylight saving since
#: 1994. A fixed offset keeps this module free of a system tz database, which a
#: slim runtime image may not ship; the contract fixture pins the agreement with
#: the application's `ZoneInfo("America/Lima")`.
LIMA_OFFSET_HOURS = -5
LIMA = timezone(timedelta(hours=LIMA_OFFSET_HOURS))

_LEVEL_RANK = {"none": 0, "prepare": 1, "imminent": 2}
_WARNING_RANK = {"yellow": 1, "orange": 2, "red": 3}


@dataclass(frozen=True, slots=True)
class HistoryBinding:
    """What the application bound for this request. The model never sees it."""

    partition: str
    as_of: datetime
    current_level: str
    current_warning_level: str | None


@dataclass(frozen=True, slots=True)
class AlertRow:
    """One stored alert, reduced to the three facts the summary may use."""

    sent_at: datetime
    level: str
    warning_level: str | None


def parse_binding(payload: object) -> HistoryBinding | None:
    """The binding in `payload`, or `None` when there is no usable one.

    Total: any input, however malformed, returns `None` rather than raising.
    `as_of` must carry an offset (a naive instant is ambiguous across the
    Lima date boundary) and is normalized to UTC.
    """
    if not isinstance(payload, dict):
        return None
    partition = payload.get("partition")
    if not isinstance(partition, str) or partition not in ALLOWED_PARTITIONS:
        return None
    as_of = _parse_instant(payload.get("as_of"))
    if as_of is None:
        return None
    level = payload.get("current_level")
    if not isinstance(level, str) or level not in _LEVEL_RANK:
        return None
    warning = payload.get("current_warning_level")
    if warning is not None and (not isinstance(warning, str) or warning not in _WARNING_RANK):
        return None
    return HistoryBinding(partition=partition, as_of=as_of, current_level=level, current_warning_level=warning)


def _parse_instant(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        instant = datetime.fromisoformat(value)
    except ValueError:
        return None
    if instant.tzinfo is None or instant.utcoffset() is None:
        return None
    return instant.astimezone(UTC)


def _rank(level: str, warning_level: str | None) -> tuple[int, int]:
    return (_LEVEL_RANK[level], _WARNING_RANK.get(warning_level or "", 0))


def summarize(rows: list[AlertRow], binding: HistoryBinding, n: int) -> dict[str, Any]:
    """The tool result: facts computed from `rows`, newest alerts first.

    Only alerts sent in `[as_of - LOOKBACK_DAYS, as_of)` count. `consecutive_days`
    is the run of consecutive Lima dates ending at `as_of`'s own date: today
    counts, and each earlier date needs at least one qualifying alert (design
    D3). `n` trims the listed alerts only; the facts always use every
    qualifying alert, so a smaller `n` cannot change what the model is told.
    """
    cutoff = binding.as_of - timedelta(days=LOOKBACK_DAYS)
    qualifying = sorted(
        (row for row in rows if cutoff <= row.sent_at < binding.as_of),
        key=lambda row: row.sent_at,
        reverse=True,
    )
    if not qualifying:
        return empty_history()

    today = binding.as_of.astimezone(LIMA).date()
    alert_dates = {row.sent_at.astimezone(LIMA).date() for row in qualifying}
    consecutive = 1
    while today - timedelta(days=consecutive) in alert_dates:
        consecutive += 1

    previous = qualifying[0]
    current_rank = _rank(binding.current_level, binding.current_warning_level)
    previous_rank = _rank(previous.level, previous.warning_level)
    if current_rank > previous_rank:
        trend = "rising"
    elif current_rank < previous_rank:
        trend = "falling"
    else:
        trend = "steady"

    return {
        "alerts": [
            {
                "sent_local": row.sent_at.astimezone(LIMA).strftime(LOCAL_FORMAT),
                "level": row.level,
                "warning_level": row.warning_level,
            }
            for row in qualifying[:n]
        ],
        "consecutive_days": consecutive,
        "previous_level": previous.level,
        "escalated": trend == "rising",
        "trend": trend,
    }


def empty_history() -> dict[str, Any]:
    """The result when there is nothing to report, or nothing could be read."""
    return {"alerts": [], "consecutive_days": 0, "previous_level": None, "escalated": False, "trend": "none"}


class AlertSource(Protocol):
    """The one read the tool needs. Narrow on purpose: a fake is one method."""

    def read(self, partition: str) -> list[AlertRow]: ...


#: Rows read per request. Cycles are six-hourly and deduplicated, so a week of
#: history is a handful of items; the cap bounds the worst case rather than
#: serving as a page size, and no pagination loop is written for it.
QUERY_LIMIT = 50


def client_config() -> Config:
    """Tight bounds, because this read sits inside an authorized send.

    Connect 1 s and read 2 s with two attempts in total: a store that cannot
    answer in about that time is treated as having no history (design D6)
    instead of eating the cycle's time budget.
    """
    return Config(connect_timeout=1, read_timeout=2, retries={"max_attempts": 1, "total_max_attempts": 2})


class DynamoAlertReader:
    """Reads one partition of the alerts table. Read-only by construction: the
    only call this class can make is `Query`.

    Like the application's adapters, the client is built lazily so that
    importing this module (and constructing the agent) needs no region and no
    credentials.
    """

    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    def read(self, partition: str) -> list[AlertRow]:
        """Every stored alert of `partition`, newest sort key first.

        Projects only `pk, sk, sent_at, level, reasons`, which is also the whole
        of what the IAM grant allows (design D8). `level` is a DynamoDB
        reserved word, hence the expression-attribute name. The reasons are
        projected only to extract the top warning level; their titles and
        amounts are dropped here and never leave this function.

        Raises:
            KeyError, ValueError: A stored item is malformed. The tool turns
                either into the empty history rather than guessing.
        """
        if self._client is None:
            self._client = get_session().create_client("dynamodb", config=client_config())
        response = self._client.query(
            TableName=TABLE_NAME,
            KeyConditionExpression="pk = :pk",
            ExpressionAttributeValues={":pk": {"S": f"{KEY_PREFIX}{partition}"}},
            ProjectionExpression="pk, sk, sent_at, #lvl, reasons",
            ExpressionAttributeNames={"#lvl": "level"},
            Select="SPECIFIC_ATTRIBUTES",
            ScanIndexForward=False,
            ConsistentRead=True,
            Limit=QUERY_LIMIT,
        )
        return [_row_from_item(item) for item in response.get("Items", [])]


def _row_from_item(item: dict[str, Any]) -> AlertRow:
    sent_at = _parse_instant(item["sent_at"]["S"])
    if sent_at is None:
        raise ValueError("sent_at is not an offset-aware ISO 8601 instant")
    level = item["level"]["S"]
    if level not in _LEVEL_RANK:
        raise ValueError(f"unknown level {level!r}")
    return AlertRow(sent_at=sent_at, level=level, warning_level=_top_warning_level(item.get("reasons", {})))


def _top_warning_level(reasons: dict[str, Any]) -> str | None:
    """The highest official-warning level among the stored reasons, if any."""
    top: str | None = None
    for entry in reasons.get("L", []):
        fields = entry["M"]
        if fields.get("kind", {}).get("S") != "official_warning":
            continue
        level = fields["level"]["S"]
        if level not in _WARNING_RANK:
            raise ValueError(f"unknown warning level {level!r}")
        if top is None or _WARNING_RANK[level] > _WARNING_RANK[top]:
            top = level
    return top
