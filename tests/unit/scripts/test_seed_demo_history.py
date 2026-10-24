"""Offline tests for `scripts/seed_demo_history.py`, the demo-history seeder.

The script is imported by path and never collected by pytest. DynamoDB is
`moto`; nothing here touches a real account. The properties that matter are
the refusals: the script may only ever touch the `demo-ferrenafe` partition,
and a refusal must write (and delete) nothing.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from moto import mock_aws

from rain_alert.adapters.dynamodb_alert_repository import TABLE_NAME, alert_record_from_item
from rain_alert.domain.values import Level, WarningLevel

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "seed_demo_history.py"
DEMO_PK = "ALERT#demo-ferrenafe"
REAL_PK = "ALERT#ferrenafe"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("seed_demo_history", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script() -> ModuleType:
    return _load()


@pytest.fixture
def client() -> Iterator[Any]:
    with mock_aws():
        import boto3

        dynamodb = boto3.client("dynamodb", region_name="us-east-2")
        dynamodb.create_table(
            TableName=TABLE_NAME,
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield dynamodb


def _items(client: Any, pk: str) -> list[dict[str, Any]]:
    response = client.query(
        TableName=TABLE_NAME,
        KeyConditionExpression="pk = :pk",
        ExpressionAttributeValues={":pk": {"S": pk}},
        ScanIndexForward=True,
    )
    return list(response["Items"])


def _put_real_item(client: Any) -> None:
    client.put_item(TableName=TABLE_NAME, Item={"pk": {"S": REAL_PK}, "sk": {"S": "2026-10-01T05:00:00+00:00#prepare"}})


class TestSeedDay:
    def test_day_one_writes_exactly_one_item_to_the_demo_partition(
        self, script: ModuleType, client: Any, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = script.main(["--day", "1"], client=client)

        assert code == 0
        items = _items(client, DEMO_PK)
        assert len(items) == 1
        assert items[0]["sk"]["S"] == "2026-10-20T05:00:00+00:00#prepare"
        assert _items(client, REAL_PK) == []
        assert "day 1" in capsys.readouterr().out

    def test_the_item_has_the_shape_the_application_writes(self, script: ModuleType, client: Any) -> None:
        script.main(["--day", "2"], client=client)

        [item] = _items(client, DEMO_PK)
        # The attributes the agent's projected Query reads (design D8) are all present.
        assert {"pk", "sk", "sent_at", "level", "reasons", "expires_at"} <= set(item)
        assert item["level"]["S"] == "imminent"
        assert item["sent_at"]["S"] == "2026-10-21T13:00:00+00:00"
        # Round-trips through the application's own deserializer into a valid record.
        from boto3.dynamodb.types import TypeDeserializer

        deserializer = TypeDeserializer()
        record = alert_record_from_item({key: deserializer.deserialize(value) for key, value in item.items()})
        assert record.city_slug == "demo-ferrenafe"
        assert record.level is Level.IMMINENT
        assert record.composer == "template"
        kinds = [(reason["M"]["kind"]["S"], reason["M"].get("level", {}).get("S")) for reason in item["reasons"]["L"]]
        assert ("official_warning", WarningLevel.ORANGE.value) in kinds

    def test_each_day_is_its_own_item_and_reseeding_is_idempotent(self, script: ModuleType, client: Any) -> None:
        for day in (1, 2, 3, 3):
            assert script.main(["--day", str(day)], client=client) == 0

        keys = [item["sk"]["S"] for item in _items(client, DEMO_PK)]
        assert keys == [
            "2026-10-20T05:00:00+00:00#prepare",
            "2026-10-21T05:00:00+00:00#imminent",
            "2026-10-22T05:00:00+00:00#imminent",
        ]

    def test_the_scenario_escalates_over_three_days(self, script: ModuleType) -> None:
        scenario = [(day.level, day.warning_level) for day in script.SCENARIO]

        assert scenario == [
            (Level.PREPARE, WarningLevel.YELLOW),
            (Level.IMMINENT, WarningLevel.ORANGE),
            (Level.IMMINENT, WarningLevel.RED),
        ]

    def test_dry_run_prints_the_plan_and_writes_nothing(
        self, script: ModuleType, client: Any, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = script.main(["--day", "1", "--dry-run"], client=client)

        assert code == 0
        assert _items(client, DEMO_PK) == []
        assert "dry run" in capsys.readouterr().out.lower()


class TestReset:
    def test_reset_deletes_every_demo_item_and_only_those(self, script: ModuleType, client: Any) -> None:
        for day in (1, 2, 3):
            script.main(["--day", str(day)], client=client)
        _put_real_item(client)

        code = script.main(["--reset"], client=client)

        assert code == 0
        assert _items(client, DEMO_PK) == []
        assert len(_items(client, REAL_PK)) == 1

    def test_reset_of_an_empty_partition_succeeds(self, script: ModuleType, client: Any) -> None:
        assert script.main(["--reset"], client=client) == 0

    def test_dry_run_reset_deletes_nothing(self, script: ModuleType, client: Any) -> None:
        script.main(["--day", "1"], client=client)

        assert script.main(["--reset", "--dry-run"], client=client) == 0
        assert len(_items(client, DEMO_PK)) == 1


class TestRefusals:
    @pytest.mark.parametrize("slug", ["ferrenafe", "lima", "demo-ferrenafe-2", "DEMO-FERRENAFE", ""])
    def test_a_non_demo_partition_is_refused_with_zero_writes(
        self, script: ModuleType, client: Any, capsys: pytest.CaptureFixture[str], slug: str
    ) -> None:
        code = script.main(["--day", "1", "--partition", slug], client=client)

        assert code != 0
        assert _items(client, f"ALERT#{slug}") == []
        assert _items(client, DEMO_PK) == []
        assert "demo-ferrenafe" in capsys.readouterr().err

    def test_reset_of_the_real_partition_is_refused_and_deletes_nothing(self, script: ModuleType, client: Any) -> None:
        _put_real_item(client)

        code = script.main(["--reset", "--partition", "ferrenafe"], client=client)

        assert code != 0
        assert len(_items(client, REAL_PK)) == 1

    def test_no_action_is_a_usage_error(self, script: ModuleType, client: Any) -> None:
        with pytest.raises(SystemExit) as exit_info:
            script.main([], client=client)

        assert exit_info.value.code == 2

    @pytest.mark.parametrize("day", ["0", "4", "x"])
    def test_a_day_outside_the_scenario_is_a_usage_error(self, script: ModuleType, client: Any, day: str) -> None:
        with pytest.raises(SystemExit) as exit_info:
            script.main(["--day", day], client=client)

        assert exit_info.value.code == 2
        assert _items(client, DEMO_PK) == []

    def test_the_guard_also_stops_a_direct_call_with_a_foreign_slug(self, script: ModuleType, client: Any) -> None:
        with pytest.raises(script.RefusedPartition):
            script.seed_day(script.SCENARIO[0], partition="ferrenafe", client=client)

        assert _items(client, REAL_PK) == []
