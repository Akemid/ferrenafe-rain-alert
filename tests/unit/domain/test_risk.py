"""RiskEvaluator — risk-evaluation spec, design.md section 5.

Table-driven; `ids=` name the spec scenarios so a failure names the exact
requirement it broke (design.md section 10, Testing Architecture).
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from rain_alert.domain.config import RiskThresholds
from rain_alert.domain.entities import Forecast, HourlyPoint, Warning
from rain_alert.domain.risk import RiskEvaluator
from rain_alert.domain.sources import Available, SourceResult, Unavailable
from rain_alert.domain.values import Coordinates, Level, SourceName, TimeWindow, UnavailableReason, WarningLevel

NOW = datetime(2026, 9, 3, 12, tzinfo=UTC)
COORDS = Coordinates(latitude=-6.64, longitude=-79.79)

DEFAULT_THRESHOLDS = RiskThresholds(
    prepare_mm_48h=9.5,
    prepare_probability_pct=60,
    prepare_probability_pct_degraded=70,
    prepare_warning_levels=frozenset({WarningLevel.YELLOW, WarningLevel.ORANGE, WarningLevel.RED}),
    imminent_mm_24h=20.0,
    imminent_probability_pct=70,
    imminent_warning_levels=frozenset({WarningLevel.ORANGE, WarningLevel.RED}),
)


def _forecast(hours: int, *, mm_total: float, probability_pct: int) -> Available[Forecast]:
    mm_per_hour = mm_total / hours
    points = tuple(
        HourlyPoint(at=NOW + timedelta(hours=h), precipitation_mm=mm_per_hour, probability_pct=probability_pct)
        for h in range(hours)
    )
    return Available(data=Forecast(location=COORDS, points=points), fetched_at=NOW)


def _unavailable_forecast() -> Unavailable:
    return Unavailable(
        source=SourceName.OPEN_METEO,
        reason=UnavailableReason.TIMEOUT,
        detail="connect timed out",
        observed_at=NOW,
    )


def _warnings(*levels: WarningLevel) -> Available[tuple[Warning, ...]]:
    window = TimeWindow(start=NOW, end=NOW + timedelta(hours=6))
    warnings = tuple(
        Warning(
            source_id=f"senamhi-{i}",
            title="Aviso meteorológico",
            level=level,
            region="Lambayeque",
            window=window,
            emitted_at=NOW,
        )
        for i, level in enumerate(levels)
    )
    return Available(data=warnings, fetched_at=NOW)


def _no_warnings() -> Available[tuple[Warning, ...]]:
    return Available(data=(), fetched_at=NOW)


def _unavailable_warnings() -> Unavailable:
    return Unavailable(
        source=SourceName.SENAMHI,
        reason=UnavailableReason.STRUCTURE_UNRECOGNIZED,
        detail="header signature not matched",
        observed_at=NOW,
    )


@dataclass(frozen=True)
class Case:
    scenario_id: str
    warnings: SourceResult[tuple[Warning, ...]]
    forecast: SourceResult[Forecast]
    expected_level: Level
    expected_degraded: bool
    reason_substring: str | None = None


CASES = [
    Case(
        scenario_id="imminent-from-official-warning",
        warnings=_warnings(WarningLevel.ORANGE),
        forecast=_forecast(24, mm_total=0.0, probability_pct=0),
        expected_level=Level.IMMINENT,
        expected_degraded=False,
    ),
    Case(
        scenario_id="imminent-from-forecast-alone",
        warnings=_no_warnings(),
        forecast=_forecast(24, mm_total=29.8, probability_pct=70),
        expected_level=Level.IMMINENT,
        expected_degraded=False,
    ),
    Case(
        scenario_id="prepare-on-threshold",
        warnings=_warnings(WarningLevel.YELLOW),
        forecast=_forecast(48, mm_total=10.0, probability_pct=60),
        expected_level=Level.PREPARE,
        expected_degraded=False,
    ),
    Case(
        scenario_id="below-prepare-threshold",
        warnings=_warnings(WarningLevel.YELLOW),
        forecast=_forecast(48, mm_total=9.0, probability_pct=60),
        expected_level=Level.NONE,
        expected_degraded=False,
    ),
    Case(
        scenario_id="degraded-prepare-requires-70-percent",
        warnings=_unavailable_warnings(),
        forecast=_forecast(48, mm_total=15.0, probability_pct=65),
        expected_level=Level.NONE,
        expected_degraded=True,
    ),
    Case(
        scenario_id="degraded-reasons-disclose-the-outage",
        warnings=_unavailable_warnings(),
        forecast=_forecast(48, mm_total=15.0, probability_pct=75),
        expected_level=Level.PREPARE,
        expected_degraded=True,
        reason_substring="official SENAMHI source unavailable",
    ),
    Case(
        scenario_id="imminent-still-fires",
        warnings=_warnings(WarningLevel.ORANGE),
        forecast=_unavailable_forecast(),
        expected_level=Level.IMMINENT,
        expected_degraded=True,
    ),
    Case(
        scenario_id="prepare-cannot-fire-without-forecast-data",
        warnings=_warnings(WarningLevel.YELLOW),
        forecast=_unavailable_forecast(),
        expected_level=Level.NONE,
        expected_degraded=True,
    ),
    Case(
        scenario_id="dual-outage",
        warnings=_unavailable_warnings(),
        forecast=_unavailable_forecast(),
        expected_level=Level.NONE,
        expected_degraded=True,
    ),
]


@pytest.mark.parametrize("case", CASES, ids=[case.scenario_id for case in CASES])
def test_risk_evaluation_scenarios(case: Case) -> None:
    evaluator = RiskEvaluator(DEFAULT_THRESHOLDS)

    assessment = evaluator.evaluate(case.warnings, case.forecast, NOW)

    assert assessment.level == case.expected_level
    assert assessment.degraded is case.expected_degraded
    if case.reason_substring is not None:
        assert any(case.reason_substring in reason for reason in assessment.reasons)


def test_threshold_source_reflects_the_injected_config_not_a_literal() -> None:
    """A forecast that clears neither production threshold clears a
    deliberately lenient fake threshold set — proving the level came from
    the injected RiskThresholds, not a hard-coded literal."""
    lenient_thresholds = RiskThresholds(
        prepare_mm_48h=1.0,
        prepare_probability_pct=1,
        prepare_probability_pct_degraded=1,
        prepare_warning_levels=frozenset({WarningLevel.YELLOW, WarningLevel.ORANGE, WarningLevel.RED}),
        imminent_mm_24h=1000.0,
        imminent_probability_pct=100,
        imminent_warning_levels=frozenset({WarningLevel.ORANGE, WarningLevel.RED}),
    )
    warnings = _warnings(WarningLevel.YELLOW)
    forecast = _forecast(48, mm_total=2.0, probability_pct=5)

    default_assessment = RiskEvaluator(DEFAULT_THRESHOLDS).evaluate(warnings, forecast, NOW)
    lenient_assessment = RiskEvaluator(lenient_thresholds).evaluate(warnings, forecast, NOW)

    assert default_assessment.level == Level.NONE
    assert lenient_assessment.level == Level.PREPARE
