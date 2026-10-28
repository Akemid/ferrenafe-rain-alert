"""The tool the model sees: one argument, a bound partition, no way to fail loudly."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import pytest
from botocore.exceptions import ClientError, ConnectTimeoutError
from history import AlertRow, HistoryBinding, build_tool, clamp_n, empty_history

BINDING = HistoryBinding("ferrenafe", datetime(2026, 10, 12, 15, tzinfo=UTC), "imminent", "red")
ROWS = [
    AlertRow(datetime(2026, 10, 11, 13, tzinfo=UTC), "prepare", "orange"),
    AlertRow(datetime(2026, 10, 10, 13, tzinfo=UTC), "prepare", "yellow"),
]


class RecordingSource:
    """A store that records every partition it was asked for, and nothing else.

    It deliberately exposes `read` only, plus write-shaped methods that fail the
    test if anything reaches them."""

    def __init__(self, rows: list[AlertRow] | None = None, error: Exception | None = None) -> None:
        self.rows = rows if rows is not None else []
        self.error = error
        self.reads: list[str] = []
        self.writes: list[str] = []

    def read(self, partition: str, as_of: datetime) -> list[AlertRow]:
        self.reads.append(partition)
        if self.error is not None:
            raise self.error
        return self.rows

    def put_item(self, **_: Any) -> None:
        self.writes.append("put_item")

    def delete_item(self, **_: Any) -> None:
        self.writes.append("delete_item")


def test_the_tool_is_named_for_the_contract_and_takes_only_n() -> None:
    tool = build_tool(BINDING, RecordingSource())

    assert tool.tool_name == "get_recent_alerts"
    properties = tool.tool_spec["inputSchema"]["json"]["properties"]
    assert set(properties) == {"n"}


def test_the_description_tells_the_model_what_it_returns_and_what_it_must_not_do() -> None:
    description = build_tool(BINDING, RecordingSource()).tool_spec["description"]

    assert "consecutive_days" in description
    assert "partition" not in description.lower()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(3, 3), (1, 1), (5, 5), (100, 5), (0, 1), (-3, 1), ("x", 1), (None, 1), (2.9, 1), (True, 1), ([4], 1)],
)
def test_n_is_clamped_into_one_to_five_and_never_raises(raw: object, expected: int) -> None:
    assert clamp_n(raw) == expected


def test_the_tool_reads_only_the_bound_partition() -> None:
    source = RecordingSource(ROWS)
    tool = build_tool(BINDING, source)

    tool(n=3)

    assert source.reads == ["ferrenafe"]


def test_a_hostile_argument_cannot_redirect_the_read() -> None:
    """The function has no partition parameter, so a model that invents one gets
    a refusal from Python itself, and the store is never reached."""
    source = RecordingSource(ROWS)
    tool = build_tool(BINDING, source)

    with pytest.raises(TypeError):
        tool(n=1, partition="other-city")  # type: ignore[call-arg]

    assert source.reads == []


def test_the_result_is_the_facts_with_n_trimming_only_the_list() -> None:
    result = build_tool(BINDING, RecordingSource(ROWS))(n=1)

    assert [a["sent_local"] for a in result["alerts"]] == ["11/10/2026 08:00"]
    assert result["consecutive_days"] == 3
    assert result["previous_level"] == "prepare"
    assert result["escalated"] is True
    assert result["trend"] == "rising"


def test_the_second_call_in_a_request_returns_the_cached_result_without_another_read() -> None:
    source = RecordingSource(ROWS)
    tool = build_tool(BINDING, source)

    first = tool(n=1)
    second = tool(n=5)

    assert second == first
    assert source.reads == ["ferrenafe"]


@pytest.mark.parametrize(
    "error",
    [
        ClientError({"Error": {"Code": "AccessDeniedException", "Message": "secret detail"}}, "Query"),
        ConnectTimeoutError(endpoint_url="https://dynamodb.invalid"),
        KeyError("sent_at"),
        ValueError("malformed"),
    ],
    ids=["ClientError", "BotoCoreError", "KeyError", "ValueError"],
)
def test_a_failed_read_degrades_to_the_empty_history(error: Exception) -> None:
    source = RecordingSource(error=error)

    assert build_tool(BINDING, source)(n=3) == empty_history()
    assert source.writes == []


def test_the_failure_log_carries_the_error_code_and_not_the_message(caplog: pytest.LogCaptureFixture) -> None:
    error = ClientError({"Error": {"Code": "AccessDeniedException", "Message": "arn:aws:iam::123:secret"}}, "Query")

    with caplog.at_level(logging.WARNING, logger="history"):
        build_tool(BINDING, RecordingSource(error=error))(n=3)

    assert "AccessDeniedException" in caplog.text
    assert "secret" not in caplog.text


def test_no_binding_means_empty_history_and_no_read() -> None:
    source = RecordingSource(ROWS)

    assert build_tool(None, source)(n=3) == empty_history()
    assert source.reads == []


def test_even_an_unexpected_exception_degrades_to_the_empty_history() -> None:
    """The spec says any failure, not a list of four: the tool sits inside an
    authorized send, and a bug here must cost the continuity sentence, never the
    alert. The failure is still logged, with its class, so it is not silent."""
    assert build_tool(BINDING, RecordingSource(error=RuntimeError("bug")))(n=3) == empty_history()


def test_the_empty_history_has_the_contract_shape() -> None:
    assert empty_history() == {
        "alerts": [],
        "consecutive_days": 0,
        "previous_level": None,
        "escalated": False,
        "trend": "none",
    }
