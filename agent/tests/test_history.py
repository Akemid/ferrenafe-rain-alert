"""The history binding: what the application hands the agent, and what the
agent is willing to believe about it.

The binding is parsed defensively because a malformed one must never cost the
alert (design D1, D6): every refusal here is "no history", never an exception.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from history import ALLOWED_PARTITIONS, HistoryBinding, parse_binding

VALID: dict[str, Any] = {
    "partition": "ferrenafe",
    "as_of": "2026-10-12T15:00:00Z",
    "current_level": "imminent",
    "current_warning_level": "red",
}


def test_a_valid_binding_is_parsed() -> None:
    binding = parse_binding(VALID)

    assert binding == HistoryBinding(
        partition="ferrenafe",
        as_of=datetime(2026, 10, 12, 15, tzinfo=UTC),
        current_level="imminent",
        current_warning_level="red",
    )


def test_the_warning_level_is_optional() -> None:
    assert parse_binding({**VALID, "current_warning_level": None}) is not None
    without = {k: v for k, v in VALID.items() if k != "current_warning_level"}
    parsed = parse_binding(without)
    assert parsed is not None
    assert parsed.current_warning_level is None


def test_only_the_two_known_partitions_are_readable() -> None:
    assert {"ferrenafe", "demo-ferrenafe"} == ALLOWED_PARTITIONS
    assert parse_binding({**VALID, "partition": "demo-ferrenafe"}) is not None


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        "history",
        42,
        {},
        {**VALID, "partition": "other-city"},
        {**VALID, "partition": "ferrenafe#x"},
        {**VALID, "partition": ["ferrenafe"]},
        {**VALID, "partition": None},
        {**VALID, "as_of": "yesterday"},
        {**VALID, "as_of": "2026-10-12T15:00:00"},
        {**VALID, "as_of": 1760281200},
        {**VALID, "current_level": "catastrophic"},
        {**VALID, "current_level": None},
        {**VALID, "current_warning_level": "purple"},
        {k: v for k, v in VALID.items() if k != "as_of"},
        {k: v for k, v in VALID.items() if k != "partition"},
    ],
    ids=lambda p: repr(p)[:60],
)
def test_an_invalid_or_missing_binding_is_no_history_and_never_raises(payload: object) -> None:
    assert parse_binding(payload) is None


def test_a_non_utc_offset_is_normalized_to_utc() -> None:
    binding = parse_binding({**VALID, "as_of": "2026-10-12T10:00:00-05:00"})

    assert binding is not None
    assert binding.as_of == datetime(2026, 10, 12, 15, tzinfo=UTC)
    assert binding.as_of.utcoffset() == datetime(2026, 1, 1, tzinfo=UTC).utcoffset()
