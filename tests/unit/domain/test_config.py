"""`AlertConfig.composer` — the kill switch (design.md D23).

`ComposerName.TEMPLATE` is the shipped default: the change lands inert, and
the owner flips the switch. See `entrypoints/wiring.py::select_composer` for
where the value is consumed.
"""

from __future__ import annotations

from rain_alert.domain.config import AlertConfig, CoordinatesSource, RiskThresholds
from rain_alert.domain.values import ComposerName, Coordinates, WarningLevel


def _config(**overrides: object) -> AlertConfig:
    thresholds = RiskThresholds(
        prepare_mm_48h=9.5,
        prepare_probability_pct=60,
        prepare_probability_pct_degraded=70,
        prepare_warning_levels=frozenset({WarningLevel.YELLOW}),
        imminent_mm_24h=20.0,
        imminent_probability_pct=70,
        imminent_warning_levels=frozenset({WarningLevel.RED}),
    )
    defaults: dict[str, object] = {
        "city": "Ferreñafe",
        "city_slug": "ferrenafe",
        "coordinates": Coordinates(latitude=-6.636005, longitude=-79.789860),
        "coordinates_source": CoordinatesSource.PUBLIC_REFERENCE,
        "timezone": "America/Lima",
        "region": "Lambayeque",
        "thresholds": thresholds,
        "checklist": (),
        "active_channel": "console",
        "forecast_hours": 48,
        "dedup_lookback_hours": 72,
    }
    defaults.update(overrides)
    return AlertConfig(**defaults)  # type: ignore[arg-type]


def test_composer_defaults_to_the_template() -> None:
    """The change ships defaulting to `template` (D23): merging it changes
    nothing observable until the owner flips the switch."""
    config = _config()

    assert config.composer is ComposerName.TEMPLATE


def test_composer_can_be_set_to_agent() -> None:
    config = _config(composer=ComposerName.AGENT)

    assert config.composer is ComposerName.AGENT
