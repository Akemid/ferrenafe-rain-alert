"""The DynamoDB reader, driven by botocore's `Stubber`.

No network and no credentials: the client is a real botocore client with
placeholder keys, and the `Stubber` validates every request against the
service model, so a misspelled parameter fails here rather than in the account.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from botocore.session import get_session
from botocore.stub import Stubber
from history import TABLE_NAME, AlertRow, DynamoAlertReader, client_config


def _client() -> Any:
    return get_session().create_client(
        "dynamodb",
        region_name="us-east-2",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )


def _item(sent_at: str, level: str, reasons: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "pk": {"S": "ALERT#ferrenafe"},
        "sk": {"S": f"2026-10-11T00:00:00+00:00#{level}"},
        "sent_at": {"S": sent_at},
        "level": {"S": level},
        "reasons": {"L": [{"M": r} for r in (reasons or [])]},
    }


def _warning(level: str) -> dict[str, Any]:
    return {"kind": {"S": "official_warning"}, "level": {"S": level}}


def _forecast() -> dict[str, Any]:
    return {"kind": {"S": "forecast_threshold"}, "accumulated_mm": {"N": "31.5"}}


AS_OF = datetime(2026, 10, 12, 15, tzinfo=UTC)

EXPECTED_QUERY = {
    "TableName": TABLE_NAME,
    "KeyConditionExpression": "pk = :pk AND sk >= :lo",
    "ExpressionAttributeValues": {":pk": {"S": "ALERT#ferrenafe"}, ":lo": {"S": "2026-10-04T15:00:00+00:00"}},
    "ProjectionExpression": "pk, sk, sent_at, #lvl, reasons",
    "ExpressionAttributeNames": {"#lvl": "level"},
    "Select": "SPECIFIC_ATTRIBUTES",
    "ScanIndexForward": False,
    "ConsistentRead": True,
    "Limit": 50,
}


def test_the_query_is_one_projected_read_of_the_bound_partition() -> None:
    client = _client()
    with Stubber(client) as stubber:
        stubber.add_response("query", {"Items": []}, EXPECTED_QUERY)

        rows = DynamoAlertReader(client).read("ferrenafe", AS_OF)

        stubber.assert_no_pending_responses()
    assert rows == []


def test_the_query_bounds_the_sort_key_eight_days_back_in_utc() -> None:
    client = _client()
    with Stubber(client) as stubber:
        stubber.add_response("query", {"Items": []}, EXPECTED_QUERY)

        DynamoAlertReader(client).read("ferrenafe", AS_OF.astimezone(timezone(timedelta(hours=-5))))

        stubber.assert_no_pending_responses()

def test_items_become_rows_with_the_top_warning_level_taken_from_the_reasons() -> None:
    client = _client()
    items = [
        _item("2026-10-11T13:00:00+00:00", "prepare", [_forecast(), _warning("yellow"), _warning("orange")]),
        _item("2026-10-10T13:00:00+00:00", "imminent", [_forecast()]),
    ]
    with Stubber(client) as stubber:
        stubber.add_response("query", {"Items": items}, EXPECTED_QUERY)

        rows = DynamoAlertReader(client).read("ferrenafe", AS_OF)

    assert rows == [
        AlertRow(datetime(2026, 10, 11, 13, tzinfo=UTC), "prepare", "orange"),
        AlertRow(datetime(2026, 10, 10, 13, tzinfo=UTC), "imminent", None),
    ]


@pytest.mark.parametrize(
    "broken",
    [
        {"pk": {"S": "ALERT#ferrenafe"}, "level": {"S": "prepare"}},
        _item("not a date", "prepare"),
        _item("2026-10-11T13:00:00+00:00", "catastrophic"),
        _item("2026-10-11T13:00:00", "prepare"),
        _item("2026-10-11T13:00:00+00:00", "prepare", [_warning("purple")]),
    ],
    ids=["missing sent_at", "bad sent_at", "unknown level", "naive sent_at", "unknown warning"],
)
def test_a_malformed_item_is_skipped_and_the_rest_survive(broken: dict[str, Any], caplog: pytest.LogCaptureFixture) -> None:
    client = _client()
    good = _item("2026-10-11T13:00:00+00:00", "prepare")
    with Stubber(client) as stubber:
        stubber.add_response("query", {"Items": [broken, good]}, EXPECTED_QUERY)

        with caplog.at_level("WARNING", logger="history"):
            rows = DynamoAlertReader(client).read("ferrenafe", AS_OF)

    assert rows == [AlertRow(datetime(2026, 10, 11, 13, tzinfo=UTC), "prepare", None)]
    assert "skipped 1 malformed" in caplog.text
    assert "not a date" not in caplog.text
    assert "catastrophic" not in caplog.text
    assert "purple" not in caplog.text


def test_the_client_is_bounded_so_a_slow_store_cannot_stall_the_alert() -> None:
    config = client_config()

    assert config.connect_timeout == 1
    assert config.read_timeout == 2
    assert config.retries is not None
    assert config.retries["total_max_attempts"] == 2


def test_no_client_is_built_until_the_first_read() -> None:
    reader = DynamoAlertReader()

    assert reader._client is None

def test_the_client_region_comes_from_aws_region_because_botocore_ignores_it(monkeypatch: pytest.MonkeyPatch) -> None:
    created: dict[str, Any] = {}

    class _Session:
        def create_client(self, service: str, **kwargs: Any) -> Any:
            created.update(kwargs)
            stub = _client()
            stubber = Stubber(stub)
            stubber.add_response("query", {"Items": []}, EXPECTED_QUERY)
            stubber.activate()
            return stub

    monkeypatch.setattr("history.get_session", lambda: _Session())
    monkeypatch.setenv("AWS_REGION", "us-east-2")
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)

    DynamoAlertReader().read("ferrenafe", AS_OF)

    assert created["region_name"] == "us-east-2"
