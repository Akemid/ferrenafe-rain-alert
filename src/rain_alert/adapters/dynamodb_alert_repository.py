"""`DynamoDbAlertRepository` — the `AlertRepository` over DynamoDB
(design.md D26, D27, D28; `alert-persistence` spec).

The item is `adapters/serialization.py`'s document, unchanged, plus
`pk`/`sk`/`expires_at`. This is a carried-forward contract, not a new
decision — `serialization.py`'s own module docstring already pins it. That
is what makes this adapter a mechanical mapping with no translation layer.

| Item | `pk` | `sk` | Attributes |
|---|---|---|---|
| Sent alert | `ALERT#<city_slug>` | `<window_start ISO>#<level>` | `alert_record_to_dict` plus `expires_at` (N) |
| Active outage | `OUTAGE#<city_slug>` | `CURRENT` | `outage_record_to_dict`, **no `expires_at`** |

**The sort key is `TimeWindow.key()` plus the level, never `key()` alone.**
`should_send` authorises escalation: a standing `prepare` for window W can be
followed by an `imminent` for the *same* W. With `key()` alone as the sort
key, the escalation would silently overwrite the `prepare` record and the
audit trail would lose the fact that the community was warned twice.
`tests/unit/adapters/test_dynamodb_alert_repository.py::TestRecordAlertIsUnconditional`
proves this both ways: the naive key collides, the real one does not, and an
end-to-end test against `moto` shows both records surviving.

**`ALERT_RETENTION_DAYS = 30`, not the 365 design.md D27 proposed.** The
design and the `alert-persistence` spec were written in parallel and the
design never fed the spec; the spec is what `sdd-verify` checks, and it pins
30 days — well above the 72-hour `dedup_lookback_hours` window, long enough
for the page's recent-alerts history, short enough that the table does not
grow unbounded. Do not "correct" this back to 365: the spec is authoritative
and 30 already clears every relation D27's own reasoning names.

**The upper sentinel is `￿` (U+FFFF), never ASCII `~`.**
`latest_start.isoformat()` alone excludes `...#imminent`, which sorts after
it lexically, so the query's inclusive upper bound needs a sentinel that
sorts after every possible level value. U+FFFF is chosen over `~` because it
holds regardless of what a future `Level` member spells; a unit test asserts
every current member sorts below it, and a second test demonstrates a
plausible future value that would break an ASCII `~` bound while the
U+FFFF sentinel still holds.

**`ConsistentRead: True`.** Eventual consistency here means a cycle can miss
the alert the previous cycle wrote and re-alert the community.

**`record_alert` is a plain `PutItem` with no `ConditionExpression`.** It
runs *after* `notifier.send_alert`, so raising on a duplicate would mean the
alert went out and was not recorded — the next cycle would re-alert.
Last-write-wins is correct here because the key **is** the identity of the
send.

**The outage item carries no `expires_at` at all — not `None`, absent.**
DynamoDB TTL only deletes items that *have* the attribute, so omitting it
entirely is what makes the active-outage record permanent until explicitly
cleared.

**Pagination is not optional.** Both `Query` (here) and `GetParametersByPath`
(`ssm_config_repository.py`) paginate, driven by the `boto3` paginator, per
design.md D28: a first page that happens to hold everything is what makes a
missing continuation loop invisible until the data grows.

**No credentials offline, exactly like `BedrockAgentCoreInvoker` (D28).**
`client: Any | None = None`, built lazily on the first call, so wiring never
constructs a client and the default test suite needs no region and no
credentials.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

from rain_alert.adapters.serialization import (
    alert_record_from_dict,
    alert_record_to_dict,
    outage_record_from_dict,
    outage_record_to_dict,
)
from rain_alert.domain.messages import AlertRecord, OutageRecord
from rain_alert.domain.values import Level

#: The dedup query's inclusive upper-bound sentinel. U+FFFF, never ASCII `~`
#: — see the module docstring's "upper sentinel" note.
UPPER_BOUND_SENTINEL = "￿"

#: **30 days, per the `alert-persistence` spec — supersedes design.md D27's
#: proposed 365.** The design and the spec were written in parallel and the
#: design never fed back into the spec; `sdd-verify` checks against the spec,
#: so 30 is the value that is actually enforced. Recorded here, not only in
#: `openspec/`, so a later "simplification" back to 365 is caught by someone
#: reading this constant, not only by someone reading the change history.
#: The relation this constant must clear (asserted, not merely commented):
#: `ALERT_RETENTION_DAYS * 86400 > dedup_lookback_hours * 3600`, i.e. well
#: above the 72-hour dedup lookback.
ALERT_RETENTION_DAYS = 30

_OUTAGE_SORT_KEY = "CURRENT"


def alert_item_pk(city_slug: str) -> str:
    return f"ALERT#{city_slug}"


def alert_item_sk(window_start: datetime, level: Level) -> str:
    """The sort key: window start ISO 8601, then the level. The level suffix
    is what stops an escalation for the same window from overwriting the
    prior send — see the module docstring."""
    return f"{window_start.isoformat()}#{level.value}"


def outage_item_pk(city_slug: str) -> str:
    return f"OUTAGE#{city_slug}"


def outage_item_sk() -> str:
    """The reserved literal sort key for the single active-outage item."""
    return _OUTAGE_SORT_KEY


def upper_bound_sort_key(latest_start: datetime) -> str:
    """An inclusive upper bound on `alert_item_sk` for any `Level` at
    `latest_start`. Required because `latest_start.isoformat()` alone would
    exclude a same-window escalation, whose suffix sorts after the bare
    timestamp."""
    return f"{latest_start.isoformat()}#{UPPER_BOUND_SENTINEL}"


def compute_expires_at(sent_at: datetime) -> int:
    """The alert item's TTL attribute: `ALERT_RETENTION_DAYS` after
    `sent_at`, as whole epoch seconds. TTL is not a correctness mechanism
    (design.md D27) — dedup correctness comes from the key-range condition,
    evaluated on every read regardless of TTL."""
    return int(sent_at.timestamp()) + ALERT_RETENTION_DAYS * 86400


def _decimalize(value: Any) -> Any:
    """Recursively replace every `float` with a `Decimal`.

    `boto3`'s `TypeSerializer` refuses raw `float` (DynamoDB's number type has
    no binary-float representation, and a silent round-trip through one would
    corrupt `ForecastThresholdReason.accumulated_mm`). `str(value)` is the
    precision-preserving path into `Decimal` — `Decimal(value)` from a float
    directly would carry the float's own binary-representation error into the
    stored number.
    """
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {key: _decimalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decimalize(item) for item in value]
    return value


def alert_record_to_item(record: AlertRecord) -> dict[str, Any]:
    """The sent-alert document, plus `pk`/`sk`/`expires_at`. No translation
    layer: `serialization.py`'s functions are reused verbatim."""
    item = alert_record_to_dict(record)
    item["pk"] = alert_item_pk(record.city_slug)
    item["sk"] = alert_item_sk(record.window.start, record.level)
    item["expires_at"] = compute_expires_at(record.sent_at)
    return item


def alert_record_from_item(item: dict[str, Any]) -> AlertRecord:
    """The inverse of `alert_record_to_item`. Extra keys (`pk`, `sk`,
    `expires_at`) are ignored by `alert_record_from_dict`, which reads only
    the document attributes it knows."""
    return alert_record_from_dict(item)


def outage_record_to_item(record: OutageRecord) -> dict[str, Any]:
    """The active-outage document, plus `pk`/`sk`. **No `expires_at` key is
    ever set** — DynamoDB TTL only deletes items that carry the attribute, so
    its total absence is what makes the outage record permanent."""
    item = outage_record_to_dict(record)
    item["pk"] = outage_item_pk(record.city_slug)
    item["sk"] = outage_item_sk()
    return item


def outage_record_from_item(item: dict[str, Any]) -> OutageRecord:
    return outage_record_from_dict(item)


class DynamoDbAlertRepository:
    """`AlertRepository` over `boto3`'s low-level `dynamodb` client.

    Args:
        table_name: The DynamoDB table name (`ferrenafe-alerts-sent`, per
            design.md D26).
        client: Injected for testability (D28). `None` (the default) defers
            construction to the first call, so the offline suite never
            builds a real client and never needs a region configured.
    """

    def __init__(self, table_name: str, *, client: Any | None = None) -> None:
        self._table_name = table_name
        self._client = client

    def _resolved_client(self) -> Any:
        if self._client is None:
            self._client = boto3.client("dynamodb")
        return self._client

    # --- community-alert dedup ---

    def alerts_with_window_start_between(
        self, city_slug: str, earliest_start: datetime, latest_start: datetime
    ) -> tuple[AlertRecord, ...]:
        deserializer = TypeDeserializer()
        client = self._resolved_client()
        paginator = client.get_paginator("query")

        items: list[dict[str, Any]] = []
        for page in paginator.paginate(
            TableName=self._table_name,
            KeyConditionExpression="pk = :pk AND sk BETWEEN :lo AND :hi",
            ExpressionAttributeValues={
                ":pk": {"S": alert_item_pk(city_slug)},
                ":lo": {"S": earliest_start.isoformat()},
                ":hi": {"S": upper_bound_sort_key(latest_start)},
            },
            ConsistentRead=True,
            ScanIndexForward=True,
        ):
            for raw_item in page.get("Items", ()):
                items.append({key: deserializer.deserialize(value) for key, value in raw_item.items()})

        return tuple(alert_record_from_item(item) for item in items)

    def record_alert(self, record: AlertRecord) -> None:
        serializer = TypeSerializer()
        item = _decimalize(alert_record_to_item(record))
        self._resolved_client().put_item(
            TableName=self._table_name,
            Item={key: serializer.serialize(value) for key, value in item.items()},
        )

    # --- outage-notice dedup state ---

    def get_active_outage(self, city_slug: str) -> OutageRecord | None:
        deserializer = TypeDeserializer()
        response = self._resolved_client().get_item(
            TableName=self._table_name,
            Key={
                "pk": {"S": outage_item_pk(city_slug)},
                "sk": {"S": outage_item_sk()},
            },
            ConsistentRead=True,
        )
        raw_item = response.get("Item")
        if raw_item is None:
            return None
        item = {key: deserializer.deserialize(value) for key, value in raw_item.items()}
        return outage_record_from_item(item)

    def save_active_outage(self, record: OutageRecord) -> None:
        serializer = TypeSerializer()
        item = outage_record_to_item(record)
        self._resolved_client().put_item(
            TableName=self._table_name,
            Item={key: serializer.serialize(value) for key, value in item.items()},
        )

    def clear_active_outage(self, city_slug: str) -> None:
        self._resolved_client().delete_item(
            TableName=self._table_name,
            Key={
                "pk": {"S": outage_item_pk(city_slug)},
                "sk": {"S": outage_item_sk()},
            },
        )
