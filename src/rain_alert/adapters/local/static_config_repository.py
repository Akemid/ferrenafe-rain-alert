"""`ConfigRepository` built from module constants (design.md 8.3).

The coordinates are an **approximate placeholder**, not a verified location.
Ferreñafe's exact latitude/longitude is still an open decision (design spec
section 12, `state.yaml -> open_decisions.ferrenafe-coordinates`), so the
config says so through `coordinates_are_placeholder=True` and the CLI prints
a marker beside them on every run. Presenting a guess as verified would put
a forecast for the wrong place under the project's own name.

Two environment overrides let an operator supply real coordinates without a
code change. They are treated strictly:

- **Both or neither.** Half an override is not a coordinate; pairing a real
  latitude with a placeholder longitude would forecast for a point in the
  ocean while claiming to be configured.
- **Unparseable or out of range raises.** Falling back to the placeholder
  would hide an operator typo and forecast for the wrong city silently.
- **A complete override clears the placeholder flag**, because an operator
  who supplies coordinates is asserting them.

`checklist` is the one place in this codebase where copy is Spanish by
contract: it is rendered into the community message body (design spec 7.4).
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from rain_alert.domain.config import AlertConfig, RiskThresholds
from rain_alert.domain.values import Coordinates, WarningLevel

LAT_ENV_VAR = "RAIN_ALERT_LAT"
LON_ENV_VAR = "RAIN_ALERT_LON"

#: Approximate, unverified. See the module docstring.
PLACEHOLDER_COORDINATES = Coordinates(latitude=-6.64, longitude=-79.79)

CITY = "Ferreñafe"
CITY_SLUG = "ferrenafe"
REGION = "Lambayeque"
TIMEZONE = "America/Lima"
ACTIVE_CHANNEL = "console"
FORECAST_HOURS = 48
DEDUP_LOOKBACK_HOURS = 72

THRESHOLDS = RiskThresholds(
    prepare_mm_48h=9.5,  # Reque p95 boundary
    prepare_probability_pct=60,
    prepare_probability_pct_degraded=70,  # forecast-only, and SENAMHI unavailable
    prepare_warning_levels=frozenset({WarningLevel.YELLOW, WarningLevel.ORANGE, WarningLevel.RED}),
    imminent_mm_24h=20.0,
    imminent_probability_pct=70,
    imminent_warning_levels=frozenset({WarningLevel.ORANGE, WarningLevel.RED}),
)

#: Recipient-facing, therefore neutral professional Spanish.
CHECKLIST = (
    "Almacena agua potable para al menos dos días.",
    "Protege documentos y aparatos eléctricos por encima del nivel del piso.",
    "Limpia canaletas, techos y desagües cercanos.",
    "Asegura objetos sueltos y calaminas.",
    "Ten a mano una linterna, un botiquín y los teléfonos de emergencia.",
)


def _coordinate(env: Mapping[str, str], name: str) -> float:
    raw = env[name]
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a decimal number, got {raw!r}") from exc


def _resolve_coordinates(env: Mapping[str, str]) -> tuple[Coordinates, bool]:
    """The coordinates to use, and whether they are still a placeholder."""
    if LAT_ENV_VAR not in env or LON_ENV_VAR not in env:
        return PLACEHOLDER_COORDINATES, True
    # `Coordinates.__post_init__` range-checks, so a typo like -600 raises here.
    return Coordinates(latitude=_coordinate(env, LAT_ENV_VAR), longitude=_coordinate(env, LON_ENV_VAR)), False


class StaticConfigRepository:
    """`ConfigRepository` over module constants plus optional env overrides."""

    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self._env = env if env is not None else os.environ

    def load(self) -> AlertConfig:
        """The full configuration for one alert cycle."""
        coordinates, is_placeholder = _resolve_coordinates(self._env)
        return AlertConfig(
            city=CITY,
            city_slug=CITY_SLUG,
            coordinates=coordinates,
            timezone=TIMEZONE,
            region=REGION,
            thresholds=THRESHOLDS,
            checklist=CHECKLIST,
            active_channel=ACTIVE_CHANNEL,
            forecast_hours=FORECAST_HOURS,
            dedup_lookback_hours=DEDUP_LOOKBACK_HOURS,
            coordinates_are_placeholder=is_placeholder,
        )
