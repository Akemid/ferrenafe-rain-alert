"""Seed (or clear) the demo alert history the live agent demo reads.

WRITES TO DYNAMODB: `--day N` puts one sent-alert item into the alerts table
and `--reset` deletes the demo partition. Both touch ONLY the `demo-ferrenafe`
partition (`ALERT#demo-ferrenafe`); any other partition, the real one
included, is refused before a client is even built. Run by hand:

    AWS_PROFILE=ferrenafe AWS_DEFAULT_REGION=us-east-2 \\
        uv run python scripts/seed_demo_history.py --day 1
    uv run python scripts/seed_demo_history.py --reset

`--dry-run` prints what would be written or deleted and touches nothing.

The three days are a fixed escalating scenario (yellow, orange, red) of plain
weather values. Each day becomes the same item the application writes after a
send: the record is built from the real domain types, the message from the
real template composer, and the item is written by `DynamoDbAlertRepository`
(`pk`, `sk`, `expires_at` included). Items expire by TTL 30 days after
`sent_at`, so a forgotten demo clears itself.

Dates: each day's window starts at 00:00 America/Lima and the alert is sent at
08:00 Lima the same day, so replaying the days in order with `as_of` at each
window start yields one more consecutive day each time (design D3).

Exit status: 0 on success, 1 when AWS rejects a call, 2 on a usage error or a
refused partition. Output never contains an ARN or an account id.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from rain_alert.adapters.dynamodb_alert_repository import (
    TABLE_NAME,
    DynamoDbAlertRepository,
    alert_item_pk,
)
from rain_alert.adapters.local.static_config_repository import CHECKLIST
from rain_alert.domain.messages import AlertRecord, ForecastSummary, MessageRequest, WarningSummary
from rain_alert.domain.reasons import ForecastThresholdReason, WarningReason
from rain_alert.domain.template import MessageComposer
from rain_alert.domain.values import Level, TimeWindow, WarningLevel

DEMO_PARTITION = "demo-ferrenafe"
DEFAULT_REGION = "us-east-2"

_CITY = "Ferreñafe"
_TIMEZONE = "America/Lima"
#: 00:00 America/Lima (UTC-5, no DST) is 05:00 UTC.
_WINDOW_START_HOUR_UTC = 5
#: 08:00 America/Lima.
_SENT_HOUR_UTC = 13


class RefusedPartition(Exception):
    """A partition other than the demo one was named."""


@dataclass(frozen=True, slots=True)
class ScenarioDay:
    """One day of the demo scenario, as plain weather values."""

    day: int
    date: str
    level: Level
    warning_level: WarningLevel
    title: str
    senamhi_id: str
    mm_24h: float
    mm_48h: float
    probability_pct: int
    peak_utc: str
    window_days: int


SCENARIO: tuple[ScenarioDay, ...] = (
    ScenarioDay(
        1,
        "2026-10-20",
        Level.PREPARE,
        WarningLevel.YELLOW,
        "LLUVIAS MODERADAS EN LA COSTA NORTE",
        "501",
        14.2,
        22.6,
        70,
        "2026-10-20T22:00:00+00:00",
        2,
    ),
    ScenarioDay(
        2,
        "2026-10-21",
        Level.IMMINENT,
        WarningLevel.ORANGE,
        "LLUVIAS INTENSAS EN LA COSTA NORTE",
        "502",
        31.5,
        52.8,
        85,
        "2026-10-21T21:00:00+00:00",
        2,
    ),
    ScenarioDay(
        3,
        "2026-10-22",
        Level.IMMINENT,
        WarningLevel.RED,
        "LLUVIAS EXTREMAS EN LA COSTA NORTE",
        "503",
        68.4,
        104.9,
        95,
        "2026-10-22T20:00:00+00:00",
        2,
    ),
)


def require_demo_partition(partition: str) -> None:
    """The one guard every write and delete passes through."""
    if partition != DEMO_PARTITION:
        raise RefusedPartition(f"refusing partition {partition!r}: this script only touches {DEMO_PARTITION!r}")


def build_record(day: ScenarioDay, partition: str = DEMO_PARTITION) -> AlertRecord:
    """The record the application would have written after sending `day`'s alert."""
    require_demo_partition(partition)
    midnight = datetime.fromisoformat(day.date).replace(hour=_WINDOW_START_HOUR_UTC, tzinfo=UTC)
    window = TimeWindow(start=midnight, end=midnight + timedelta(days=day.window_days))
    sent_at = midnight.replace(hour=_SENT_HOUR_UTC)
    reasons = (
        WarningReason(level=day.warning_level, title=day.title),
        ForecastThresholdReason(accumulated_mm=day.mm_48h, hours=48, probability_pct=day.probability_pct),
    )
    request = MessageRequest(
        city=_CITY,
        timezone=_TIMEZONE,
        level=day.level,
        window=window,
        reasons=reasons,
        forecast=ForecastSummary(
            mm_24h=day.mm_24h,
            mm_48h=day.mm_48h,
            peak_at=datetime.fromisoformat(day.peak_utc),
            peak_probability_pct=day.probability_pct,
        ),
        warning=WarningSummary(source_id=day.senamhi_id, level=day.warning_level, title=day.title, window=window),
        senamhi_status="available",
        open_meteo_status="available",
        checklist=CHECKLIST,
    )
    return AlertRecord(
        city_slug=partition,
        level=day.level,
        window=window,
        sent_at=sent_at,
        message=MessageComposer().compose(request),
        reasons=reasons,
        senamhi_status="available",
        open_meteo_status="available",
        composer="template",
    )


def seed_day(day: ScenarioDay, *, partition: str = DEMO_PARTITION, client: Any) -> AlertRecord:
    """Write `day`'s record. Refuses a foreign partition before any call."""
    require_demo_partition(partition)
    record = build_record(day, partition)
    DynamoDbAlertRepository(TABLE_NAME, client=client).record_alert(record)
    return record


def demo_keys(client: Any, partition: str = DEMO_PARTITION) -> list[dict[str, Any]]:
    """Every key of the demo partition, across pages."""
    require_demo_partition(partition)
    keys: list[dict[str, Any]] = []
    for page in client.get_paginator("query").paginate(
        TableName=TABLE_NAME,
        KeyConditionExpression="pk = :pk",
        ExpressionAttributeValues={":pk": {"S": alert_item_pk(partition)}},
        ProjectionExpression="pk, sk",
        ConsistentRead=True,
    ):
        keys.extend({"pk": item["pk"], "sk": item["sk"]} for item in page.get("Items", ()))
    return keys


def reset(client: Any, partition: str = DEMO_PARTITION) -> int:
    """Delete the whole demo partition; returns how many items went."""
    keys = demo_keys(client, partition)
    for key in keys:
        client.delete_item(TableName=TABLE_NAME, Key=key)
    return len(keys)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed or clear the demo alert history (demo-ferrenafe only).")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--day", type=int, choices=[d.day for d in SCENARIO], help="seed that scenario day")
    action.add_argument("--reset", action="store_true", help="delete the whole demo partition")
    parser.add_argument(
        "--partition", default=DEMO_PARTITION, help=f"must be {DEMO_PARTITION!r}; anything else is refused"
    )
    parser.add_argument("--dry-run", action="store_true", help="print the plan, touch nothing")
    return parser


def main(argv: Sequence[str] | None = None, client: Any | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        require_demo_partition(args.partition)
    except RefusedPartition as refusal:
        print(f"error: {refusal}", file=sys.stderr)
        return 2

    if args.dry_run:
        if args.reset:
            print(f"dry run: would delete every item of {alert_item_pk(args.partition)}")
        else:
            record = build_record(SCENARIO[args.day - 1], args.partition)
            print(
                f"dry run: would write day {args.day} to {alert_item_pk(args.partition)} sent_at={record.sent_at.isoformat()}"
            )
        return 0

    if client is None:
        import boto3

        client = boto3.client(
            "dynamodb",
            region_name=os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION") or DEFAULT_REGION,
        )
    try:
        if args.reset:
            print(f"deleted {reset(client, args.partition)} item(s) from {alert_item_pk(args.partition)}")
        else:
            record = seed_day(SCENARIO[args.day - 1], partition=args.partition, client=client)
            print(f"wrote day {args.day} to {alert_item_pk(args.partition)} sent_at={record.sent_at.isoformat()}")
    except Exception as error:  # noqa: BLE001 - one masked line; AWS messages can quote ARNs
        print(f"error: {type(error).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
