"""`ContinuitySummary` and `summarize` (alert-cycle spec; design D2, D3).

The application's half of `contracts/recent-alerts.json`. `agent/history.py`
computes the same facts on the other side of an import boundary that must not
be crossed; the contract file holds the fixture cases and both suites assert
them, so neither copy can change alone.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from rain_alert.domain.continuity import (
    EMPTY_CONTINUITY,
    LOOKBACK_DAYS,
    MAX_PRIOR_ALERTS,
    ContinuitySummary,
    Trend,
    summarize,
    top_warning_level,
)
from rain_alert.domain.messages import AlertMessage, AlertRecord
from rain_alert.domain.reasons import ForecastThresholdReason, WarningReason
from rain_alert.domain.values import Level, TimeWindow, WarningLevel

CONTRACT_PATH = Path(__file__).resolve().parents[3] / "contracts" / "recent-alerts.json"
LIMA = ZoneInfo("America/Lima")
LOCAL_FORMAT = "%d/%m/%Y %H:%M"


def _contract() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    return loaded


CASES: list[dict[str, Any]] = _contract()["cases"]


def _record(sent_at: datetime, level: Level, warning_level: WarningLevel | None = None) -> AlertRecord:
    reasons: tuple[WarningReason | ForecastThresholdReason, ...] = (
        ForecastThresholdReason(accumulated_mm=12.4, hours=24, probability_pct=60),
    )
    if warning_level is not None:
        reasons = (WarningReason(level=warning_level, title="Aviso de prueba"), *reasons)
    return AlertRecord(
        city_slug="ferrenafe",
        level=level,
        window=TimeWindow(start=sent_at, end=sent_at + timedelta(hours=48)),
        sent_at=sent_at,
        message=AlertMessage(title="t", body="b", level=level, valid_until=sent_at + timedelta(hours=48)),
        reasons=reasons,
        senamhi_status="available",
        open_meteo_status="available",
        composer="template",
    )


def _instant(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _warning(value: str | None) -> WarningLevel | None:
    return None if value is None else WarningLevel(value)


def _from_case(case: dict[str, Any]) -> ContinuitySummary:
    records = tuple(
        _record(_instant(row["sent_at"]), Level(row["level"]), _warning(row["warning_level"]))
        for row in case["records"]
    )
    return summarize(
        records,
        as_of=_instant(case["as_of"]),
        tz=LIMA,
        current_level=Level(case["current_level"]),
        current_warning_level=_warning(case["current_warning_level"]),
    )


def test_the_constants_match_the_contract() -> None:
    contract = _contract()

    assert contract["lookback_days"] == LOOKBACK_DAYS
    assert contract["max_n"] == MAX_PRIOR_ALERTS
    assert contract["timezone"] == "America/Lima"
    assert contract["local_format"] == LOCAL_FORMAT


def test_the_contract_fixture_is_not_empty() -> None:
    """Guards the parametrization: zero cases would make the next test vacuous."""
    assert len(CASES) >= 10


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_the_summary_agrees_with_the_contract_fixture(case: dict[str, Any]) -> None:
    expected = case["expected"]

    summary = _from_case(case)

    assert summary.consecutive_days == expected["consecutive_days"]
    assert (None if summary.previous_level is None else summary.previous_level.value) == expected["previous_level"]
    assert summary.escalated == expected["escalated"]
    assert summary.trend.value == expected["trend"]
    assert [
        {
            "sent_local": prior.sent_at.astimezone(LIMA).strftime(LOCAL_FORMAT),
            "level": prior.level.value,
            "warning_level": None if prior.warning_level is None else prior.warning_level.value,
        }
        for prior in summary.prior_alerts
    ] == expected["alerts"]


class TestTheEmptySummary:
    def test_no_records_is_the_empty_summary(self) -> None:
        summary = summarize(
            (), as_of=_instant("2026-10-12T15:00:00Z"), tz=LIMA, current_level=Level.PREPARE, current_warning_level=None
        )

        assert summary == EMPTY_CONTINUITY
        assert summary.consecutive_days == 0
        assert summary.previous_level is None
        assert summary.escalated is False
        assert summary.trend is Trend.NONE
        assert summary.prior_alerts == ()

    def test_only_records_outside_the_window_is_the_empty_summary(self) -> None:
        as_of = _instant("2026-10-12T15:00:00Z")
        too_old = _record(as_of - timedelta(days=LOOKBACK_DAYS, minutes=1), Level.PREPARE)
        not_before = _record(as_of, Level.PREPARE)

        summary = summarize(
            (too_old, not_before), as_of=as_of, tz=LIMA, current_level=Level.PREPARE, current_warning_level=None
        )

        assert summary == EMPTY_CONTINUITY


class TestPurity:
    def test_the_same_records_give_equal_summaries_and_are_not_mutated(self) -> None:
        records = tuple(
            _record(_instant(row["sent_at"]), Level(row["level"]), _warning(row["warning_level"]))
            for row in CASES[1]["records"]
        )
        before = list(records)
        kwargs: dict[str, Any] = {
            "as_of": _instant(CASES[1]["as_of"]),
            "tz": LIMA,
            "current_level": Level(CASES[1]["current_level"]),
            "current_warning_level": _warning(CASES[1]["current_warning_level"]),
        }

        first = summarize(records, **kwargs)
        second = summarize(records, **kwargs)

        assert first == second
        assert first.consecutive_days == 3  # production code ran on non-trivial data
        assert list(records) == before

    def test_the_input_order_does_not_matter(self) -> None:
        records = tuple(
            _record(_instant(row["sent_at"]), Level(row["level"]), _warning(row["warning_level"]))
            for row in CASES[1]["records"]
        )
        kwargs: dict[str, Any] = {
            "as_of": _instant(CASES[1]["as_of"]),
            "tz": LIMA,
            "current_level": Level.IMMINENT,
            "current_warning_level": WarningLevel.RED,
        }

        assert summarize(records, **kwargs) == summarize(tuple(reversed(records)), **kwargs)


class TestTheTopWarningLevel:
    def test_the_highest_official_warning_wins(self) -> None:
        reasons = (
            WarningReason(level=WarningLevel.YELLOW, title="a"),
            WarningReason(level=WarningLevel.RED, title="b"),
            WarningReason(level=WarningLevel.ORANGE, title="c"),
            ForecastThresholdReason(accumulated_mm=1.0, hours=24, probability_pct=60),
        )

        assert top_warning_level(reasons) is WarningLevel.RED

    def test_no_official_warning_is_none(self) -> None:
        reasons = (ForecastThresholdReason(accumulated_mm=1.0, hours=24, probability_pct=60),)

        assert top_warning_level(reasons) is None
        assert top_warning_level((WarningReason(level=WarningLevel.ORANGE, title="x"),)) is WarningLevel.ORANGE


def test_a_record_written_in_utc_counts_on_its_lima_date() -> None:
    """03:30Z on the 11th is 22:30 on the 10th in Lima, so it is yesterday for an `as_of` on the 11th."""
    as_of = datetime(2026, 10, 11, 15, tzinfo=UTC)
    record = _record(datetime(2026, 10, 11, 3, 30, tzinfo=UTC), Level.PREPARE)

    summary = summarize((record,), as_of=as_of, tz=LIMA, current_level=Level.PREPARE, current_warning_level=None)

    assert summary.consecutive_days == 2
    assert summary.prior_alerts[0].sent_at.astimezone(LIMA).strftime(LOCAL_FORMAT) == "10/10/2026 22:30"
