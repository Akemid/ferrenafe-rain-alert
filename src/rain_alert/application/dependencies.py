"""CycleDependencies — bundles all seven ports plus the evaluator factory
and clock into one frozen object (design.md D6, D7).

Seven ports plus an evaluator, two policies and a clock would give
`RunAlertCycle` ten constructor parameters; this bundle keeps constructor
injection while making CLI and test wiring a single object.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from rain_alert.domain.config import RiskThresholds
from rain_alert.domain.risk import RiskEvaluator
from rain_alert.ports import (
    AlertRepository,
    ConfigRepository,
    ContactRepository,
    ForecastProvider,
    MessageComposer,
    Notifier,
    WarningProvider,
)


@dataclass(frozen=True, slots=True)
class CycleDependencies:
    """The full object graph `RunAlertCycle` needs. Both
    `tests.support.wiring.build_fake_deps()` and
    `entrypoints.wiring.build_local_deps()` return this same type — only the
    leaves differ."""

    config: ConfigRepository
    warnings: WarningProvider
    forecast: ForecastProvider
    composer: MessageComposer
    contacts: ContactRepository
    notifier: Notifier
    alerts: AlertRepository
    evaluator_factory: Callable[[RiskThresholds], RiskEvaluator]
    now: Callable[[], datetime]  # D7 — never datetime.now() called directly
