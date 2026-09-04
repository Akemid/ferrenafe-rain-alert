"""Configuration types (design.md section 5). Thresholds and coordinates
never appear as literals in `domain/risk.py` — they arrive through these
types from `ConfigRepository`."""

from rain_alert.domain.config import AlertConfig, RiskThresholds
from rain_alert.domain.values import Coordinates, WarningLevel


class TestRiskThresholds:
    def test_risk_thresholds_carries_expected_fields(self) -> None:
        thresholds = RiskThresholds(
            prepare_mm_48h=9.5,
            prepare_probability_pct=60,
            prepare_probability_pct_degraded=70,
            prepare_warning_levels=frozenset({WarningLevel.YELLOW, WarningLevel.ORANGE, WarningLevel.RED}),
            imminent_mm_24h=20.0,
            imminent_probability_pct=70,
            imminent_warning_levels=frozenset({WarningLevel.ORANGE, WarningLevel.RED}),
        )
        assert thresholds.prepare_mm_48h == 9.5
        assert thresholds.imminent_warning_levels == frozenset({WarningLevel.ORANGE, WarningLevel.RED})


class TestAlertConfig:
    def test_alert_config_carries_expected_fields(self) -> None:
        thresholds = RiskThresholds(
            prepare_mm_48h=9.5,
            prepare_probability_pct=60,
            prepare_probability_pct_degraded=70,
            prepare_warning_levels=frozenset({WarningLevel.YELLOW, WarningLevel.ORANGE, WarningLevel.RED}),
            imminent_mm_24h=20.0,
            imminent_probability_pct=70,
            imminent_warning_levels=frozenset({WarningLevel.ORANGE, WarningLevel.RED}),
        )
        config = AlertConfig(
            city="Ferreñafe",
            city_slug="ferrenafe",
            coordinates=Coordinates(latitude=-6.64, longitude=-79.79),
            timezone="America/Lima",
            region="Lambayeque",
            thresholds=thresholds,
            checklist=("Store water", "Secure loose objects"),
            active_channel="console",
            forecast_hours=48,
            dedup_lookback_hours=72,
            coordinates_are_placeholder=True,
        )
        assert config.city_slug == "ferrenafe"
        assert config.coordinates_are_placeholder is True
        assert config.forecast_hours == 48
