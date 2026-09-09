"""`ConfigRepository` built from module constants (design.md 8.3).

The default coordinates are the centre of the city of Ferreñafe, the
provincial capital, which is where the community this system serves lives.
They are `6°38'10"S 79°47'23"W`, which two independent public references
agree on:

- https://en.wikipedia.org/wiki/Ferre%C3%B1afe_District
- https://www.deperu.com/infoperu/lambayeque/ferrenafe/

**Sourced is not surveyed.** These come from public references, not from a
reading taken on the ground, and they name the administrative centre rather
than any particular point on the river. They are good enough to calibrate
against — the whole city sits well inside one Open-Meteo grid cell — and they
should be confirmed against a real reading before the deployment change
writes them into cloud configuration.

Two environment overrides let an operator supply a better point without a
code change. They are treated strictly, and that matters more now that the
default is no longer self-evidently provisional:

- **Both or neither.** Half an override is not a coordinate; pairing an
  operator's latitude with the default longitude would forecast for a point
  neither of them chose while claiming to be configured.
- **Unparseable or out of range raises.** Falling back to the default would
  hide an operator typo and forecast for the wrong city silently.

`checklist` is the one place in this codebase where copy is Spanish by
contract: it is rendered into the community message body (design spec 7.4).
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from rain_alert.domain.config import AlertConfig, CoordinatesSource, RiskThresholds
from rain_alert.domain.values import Coordinates, WarningLevel

LAT_ENV_VAR = "RAIN_ALERT_LAT"
LON_ENV_VAR = "RAIN_ALERT_LON"

#: The city centre, `6°38'10"S 79°47'23"W`. Sourced from public references,
#: not surveyed on the ground. See the module docstring.
SOURCED_CITY_CENTRE_COORDINATES = Coordinates(latitude=-6.636005, longitude=-79.789860)

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


def _resolve_coordinates(env: Mapping[str, str]) -> tuple[Coordinates, CoordinatesSource]:
    """The operator's pair if both variables are set, else the city centre.

    The provenance travels with the pair rather than being inferred later,
    because the same two numbers mean different things depending on who chose
    them and nothing downstream can tell them apart.
    """
    if LAT_ENV_VAR not in env or LON_ENV_VAR not in env:
        return SOURCED_CITY_CENTRE_COORDINATES, CoordinatesSource.PUBLIC_REFERENCE
    # `Coordinates.__post_init__` range-checks, so a typo like -600 raises here.
    supplied = Coordinates(latitude=_coordinate(env, LAT_ENV_VAR), longitude=_coordinate(env, LON_ENV_VAR))
    return supplied, CoordinatesSource.OPERATOR_SUPPLIED


class StaticConfigRepository:
    """`ConfigRepository` over module constants plus optional env overrides."""

    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self._env = env if env is not None else os.environ

    def load(self) -> AlertConfig:
        """The full configuration for one alert cycle."""
        coordinates, source = _resolve_coordinates(self._env)
        return AlertConfig(
            city=CITY,
            city_slug=CITY_SLUG,
            coordinates=coordinates,
            coordinates_source=source,
            timezone=TIMEZONE,
            region=REGION,
            thresholds=THRESHOLDS,
            checklist=CHECKLIST,
            active_channel=ACTIVE_CHANNEL,
            forecast_hours=FORECAST_HOURS,
            dedup_lookback_hours=DEDUP_LOOKBACK_HOURS,
        )
