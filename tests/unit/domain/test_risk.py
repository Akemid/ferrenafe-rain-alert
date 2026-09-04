"""RiskEvaluator — risk-evaluation spec, design.md section 5.

Table-driven; `ids=` name the spec scenarios so a failure names the exact
requirement it broke (design.md section 10, Testing Architecture).
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from rain_alert.domain.config import RiskThresholds
from rain_alert.domain.entities import Forecast, HourlyPoint, Warning
from rain_alert.domain.reasons import ForecastThresholdReason, ReasonKind, render_reasons_en
from rain_alert.domain.risk import RiskEvaluator
from rain_alert.domain.sources import Available, SourceResult, Unavailable
from rain_alert.domain.values import (
    LEVEL_RANK,
    Coordinates,
    Level,
    SourceName,
    TimeWindow,
    UnavailableReason,
    WarningLevel,
)

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
    expected_reason_kinds: tuple[ReasonKind, ...] | None = None


CASES = [
    Case(
        scenario_id="imminent-from-official-warning",
        warnings=_warnings(WarningLevel.ORANGE),
        forecast=_forecast(24, mm_total=0.0, probability_pct=0),
        expected_level=Level.IMMINENT,
        expected_degraded=False,
        expected_reason_kinds=(ReasonKind.OFFICIAL_WARNING,),
    ),
    Case(
        scenario_id="imminent-from-forecast-alone",
        warnings=_no_warnings(),
        forecast=_forecast(24, mm_total=29.8, probability_pct=70),
        expected_level=Level.IMMINENT,
        expected_degraded=False,
        expected_reason_kinds=(ReasonKind.FORECAST_THRESHOLD,),
    ),
    Case(
        scenario_id="prepare-on-threshold",
        warnings=_warnings(WarningLevel.YELLOW),
        forecast=_forecast(48, mm_total=10.0, probability_pct=60),
        expected_level=Level.PREPARE,
        expected_degraded=False,
        # Both halves of the combined rule must be in the audit trail:
        # dropping the official-warning reason previously failed nothing.
        expected_reason_kinds=(ReasonKind.OFFICIAL_WARNING, ReasonKind.FORECAST_THRESHOLD),
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
        expected_reason_kinds=(ReasonKind.SENAMHI_UNAVAILABLE, ReasonKind.FORECAST_THRESHOLD),
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
    if case.expected_reason_kinds is not None:
        assert tuple(reason.kind for reason in assessment.reasons) == case.expected_reason_kinds


class TestForecastOnlyPrepareWithASilentButHealthySenamhi:
    """The fifth branch (owner-approved 2026-09-04).

    Without it, breaking the SENAMHI scraper made the system *more* likely to
    warn: 24 mm / 48 h at 95% yielded `none` when SENAMHI was available and
    silent, but `prepare` when SENAMHI was unavailable. A broken source must
    never buy extra sensitivity.
    """

    def test_qualifying_forecast_at_the_stricter_threshold_prepares(self) -> None:
        evaluator = RiskEvaluator(DEFAULT_THRESHOLDS)

        assessment = evaluator.evaluate(_no_warnings(), _forecast(48, mm_total=24.0, probability_pct=95), NOW)

        assert assessment.level == Level.PREPARE
        assert assessment.degraded is False
        assert tuple(reason.kind for reason in assessment.reasons) == (
            ReasonKind.NO_QUALIFYING_WARNING,
            ReasonKind.FORECAST_THRESHOLD,
        )

    def test_reasons_state_there_is_no_official_warning_and_the_trigger_was_forecast_only(self) -> None:
        evaluator = RiskEvaluator(DEFAULT_THRESHOLDS)

        assessment = evaluator.evaluate(_no_warnings(), _forecast(48, mm_total=24.0, probability_pct=95), NOW)
        rendered = render_reasons_en(assessment.reasons)

        assert any("no qualifying official SENAMHI warning" in line for line in rendered)
        assert any("forecast-only" in line for line in rendered)
        assert any(
            f"required threshold {DEFAULT_THRESHOLDS.prepare_probability_pct_degraded}%" in line for line in rendered
        )

    def test_the_same_forecast_below_the_stricter_threshold_stays_none(self) -> None:
        evaluator = RiskEvaluator(DEFAULT_THRESHOLDS)

        assessment = evaluator.evaluate(_no_warnings(), _forecast(48, mm_total=24.0, probability_pct=65), NOW)

        assert assessment.level == Level.NONE

    @pytest.mark.parametrize(
        ("mm_total", "probability_pct"),
        [
            (0.0, 0),
            (9.0, 65),
            (9.5, 60),
            (15.0, 65),
            (15.0, 70),
            (24.0, 95),
            (30.0, 100),
        ],
        ids=[
            "calm",
            "below-mm-and-below-70",
            "at-mm-below-70",
            "above-mm-below-70",
            "above-mm-at-70",
            "well-above-both",
            "extreme",
        ],
    )
    def test_a_healthy_silent_senamhi_is_never_less_sensitive_than_an_unavailable_one(
        self, mm_total: float, probability_pct: int
    ) -> None:
        """Monotonicity guard: breaking the scraper must never raise the level."""
        evaluator = RiskEvaluator(DEFAULT_THRESHOLDS)
        forecast = _forecast(48, mm_total=mm_total, probability_pct=probability_pct)

        healthy_and_silent = evaluator.evaluate(_no_warnings(), forecast, NOW)
        senamhi_unavailable = evaluator.evaluate(_unavailable_warnings(), forecast, NOW)

        assert LEVEL_RANK[healthy_and_silent.level] >= LEVEL_RANK[senamhi_unavailable.level]


class TestShortHorizonForecast:
    def test_reason_reports_the_horizon_actually_evaluated(self) -> None:
        """W1: a 24-point forecast must not be reported as a 48-hour reading."""
        evaluator = RiskEvaluator(DEFAULT_THRESHOLDS)

        assessment = evaluator.evaluate(
            _warnings(WarningLevel.YELLOW), _forecast(24, mm_total=12.0, probability_pct=80), NOW
        )

        assert assessment.level == Level.PREPARE
        forecast_reasons = [r for r in assessment.reasons if isinstance(r, ForecastThresholdReason)]
        assert [reason.hours for reason in forecast_reasons] == [24]
        assert not any("48 h" in rendered for rendered in render_reasons_en(assessment.reasons))

    def test_single_hour_cloudburst_reaches_imminent(self) -> None:
        """C2: a one-point forecast used to abort evaluation with
        `TimeWindow.end must be strictly after start`."""
        evaluator = RiskEvaluator(DEFAULT_THRESHOLDS)

        assessment = evaluator.evaluate(_no_warnings(), _forecast(1, mm_total=25.0, probability_pct=80), NOW)

        assert assessment.level == Level.IMMINENT
        assert assessment.window.end == NOW + timedelta(hours=1)


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
