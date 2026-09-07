"""build_fake_deps — the fake side of design.md D6's `CycleDependencies`
object graph (design.md section 10). Both `tests.support.wiring` and
`entrypoints.wiring` return the same `CycleDependencies` type; only the
leaves differ, which is the reason D6 exists.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from rain_alert.application.dependencies import CycleDependencies
from rain_alert.domain.config import AlertConfig, RiskThresholds
from rain_alert.domain.entities import Contact, Forecast, HourlyPoint, Warning
from rain_alert.domain.messages import AlertRecord, OutageRecord
from rain_alert.domain.sources import Available, SourceResult
from rain_alert.domain.values import Coordinates, WarningLevel
from tests.support.fakes import (
    CallLog,
    FakeAlertRepository,
    FakeConfigRepository,
    FakeContactRepository,
    FakeForecastProvider,
    FakeMessageComposer,
    FakeNotifier,
    FakeWarningProvider,
    LoggingRiskEvaluator,
)

DEFAULT_NOW = datetime(2026, 9, 3, 12, tzinfo=UTC)

DEFAULT_THRESHOLDS = RiskThresholds(
    prepare_mm_48h=9.5,
    prepare_probability_pct=60,
    prepare_probability_pct_degraded=70,
    prepare_warning_levels=frozenset({WarningLevel.YELLOW, WarningLevel.ORANGE, WarningLevel.RED}),
    imminent_mm_24h=20.0,
    imminent_probability_pct=70,
    imminent_warning_levels=frozenset({WarningLevel.ORANGE, WarningLevel.RED}),
)

DEFAULT_CONFIG = AlertConfig(
    city="Ferreñafe",
    city_slug="ferrenafe",
    coordinates=Coordinates(latitude=-6.636005, longitude=-79.789860),
    timezone="America/Lima",
    region="Lambayeque",
    thresholds=DEFAULT_THRESHOLDS,
    checklist=("Store water", "Secure loose objects", "Clear roof drains"),
    active_channel="console",
    forecast_hours=48,
    dedup_lookback_hours=72,
)


def _calm_forecast(now: datetime) -> Available[Forecast]:
    """48 contiguous hours of zero precipitation — clears no threshold."""
    points = tuple(HourlyPoint(at=now + timedelta(hours=h), precipitation_mm=0.0, probability_pct=0) for h in range(48))
    return Available(data=Forecast(location=DEFAULT_CONFIG.coordinates, points=points), fetched_at=now)


def build_fake_deps(
    *,
    config: AlertConfig | None = None,
    warnings: SourceResult[tuple[Warning, ...]] | None = None,
    forecast: SourceResult[Forecast] | None = None,
    contacts: tuple[Contact, ...] | None = None,
    active_outage: OutageRecord | None = None,
    prior_alerts: list[AlertRecord] | None = None,
    now: datetime | None = None,
) -> CycleDependencies:
    """One `CycleDependencies` with recording-spy leaves and calm defaults.

    Override only the leaves a test cares about, e.g.
    `build_fake_deps(forecast=Unavailable(...))`. Spy leaves (`.notifier`,
    `.alerts`, `.composer`, `.warnings`, `.forecast`, `.config`) stay
    accessible on the returned object for per-port argument assertions.

    **Every leaf shares one `CallLog`**, so cross-port order is observable:
    reach it from any spy (`deps.notifier.log`) and assert on `.steps` or
    `.position_of(...)`. Without it the `alert-cycle` ordering guarantees —
    the steps run in a stated order, and an alert is recorded only after the
    notifier delivered it — could not fail a test.
    """
    resolved_now = now if now is not None else DEFAULT_NOW
    resolved_config = config if config is not None else DEFAULT_CONFIG
    resolved_warnings = warnings if warnings is not None else Available(data=(), fetched_at=resolved_now)
    resolved_forecast = forecast if forecast is not None else _calm_forecast(resolved_now)
    resolved_contacts = contacts if contacts is not None else (Contact("operator-local", "console", "stdout", None),)
    log = CallLog()

    return CycleDependencies(
        config=FakeConfigRepository(resolved_config, log=log),
        warnings=FakeWarningProvider(resolved_warnings, log=log),
        forecast=FakeForecastProvider(resolved_forecast, log=log),
        composer=FakeMessageComposer(log=log),
        contacts=FakeContactRepository(resolved_contacts, log=log),
        notifier=FakeNotifier(log=log),
        alerts=FakeAlertRepository(alerts=list(prior_alerts or []), active_outage=active_outage, log=log),
        evaluator_factory=lambda thresholds: LoggingRiskEvaluator(thresholds, log=log),
        now=lambda: resolved_now,
    )
