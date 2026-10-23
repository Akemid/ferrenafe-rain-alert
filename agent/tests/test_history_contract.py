"""The agent's half of `contracts/recent-alerts.json` (design D2).

`agent/history.py` and `rain_alert/domain/continuity.py` compute the same
continuity facts on opposite sides of an import boundary that must not be
crossed. The contract file holds the constants and the fixture cases; this
suite asserts the agent side, and the application suite asserts the other.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import history
import pytest
from history import AlertRow, HistoryBinding, summarize

CONTRACT_PATH = Path(__file__).resolve().parents[2] / "contracts" / "recent-alerts.json"


def _contract() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    return loaded


def _instant(text: str) -> datetime:
    return datetime.fromisoformat(text)


CASES: list[dict[str, Any]] = _contract()["cases"]


def test_the_constants_match_the_contract() -> None:
    contract = _contract()

    assert contract["table_name"] == history.TABLE_NAME
    assert contract["key_prefix"] == history.KEY_PREFIX
    assert contract["lookback_days"] == history.LOOKBACK_DAYS
    assert contract["max_n"] == history.MAX_N
    assert contract["tool_name"] == history.TOOL_NAME
    assert contract["local_format"] == history.LOCAL_FORMAT
    assert sorted(history.ALLOWED_PARTITIONS) == sorted(contract["allowed_partitions"])
    assert contract["utc_offset_hours"] == history.LIMA_OFFSET_HOURS
    assert contract["timezone"] == "America/Lima"


def test_the_contract_has_a_case_for_every_rule_it_pins() -> None:
    names = {case["name"] for case in CASES}

    assert {
        "empty_history",
        "three_consecutive_days_rising",
        "gap_breaks_the_streak",
        "same_day_duplicates_count_once",
        "lima_date_not_utc_date",
        "sent_at_not_before_as_of_is_excluded",
        "seven_day_lookback_is_inclusive_and_cuts_older",
    } <= names


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_summarize_matches_the_contract_case(case: dict[str, Any]) -> None:
    rows = [
        AlertRow(sent_at=_instant(r["sent_at"]), level=r["level"], warning_level=r["warning_level"])
        for r in case["records"]
    ]
    binding = HistoryBinding(
        partition="ferrenafe",
        as_of=_instant(case["as_of"]),
        current_level=case["current_level"],
        current_warning_level=case["current_warning_level"],
    )

    result = summarize(rows, binding, n=_contract()["max_n"])

    assert result == case["expected"]


def test_summarize_ignores_the_order_the_store_returned() -> None:
    case = next(c for c in CASES if c["name"] == "three_consecutive_days_rising")
    rows = [
        AlertRow(sent_at=_instant(r["sent_at"]), level=r["level"], warning_level=r["warning_level"])
        for r in reversed(case["records"])
    ]
    binding = HistoryBinding("ferrenafe", _instant(case["as_of"]), case["current_level"], case["current_warning_level"])

    assert summarize(rows, binding, n=5) == case["expected"]


def test_n_trims_the_listed_alerts_but_never_the_facts() -> None:
    case = next(c for c in CASES if c["name"] == "three_consecutive_days_rising")
    rows = [
        AlertRow(sent_at=_instant(r["sent_at"]), level=r["level"], warning_level=r["warning_level"])
        for r in case["records"]
    ]
    binding = HistoryBinding("ferrenafe", _instant(case["as_of"]), case["current_level"], case["current_warning_level"])

    result = summarize(rows, binding, n=1)

    assert len(result["alerts"]) == 1
    assert result["alerts"][0] == case["expected"]["alerts"][0]
    assert result["consecutive_days"] == case["expected"]["consecutive_days"]
