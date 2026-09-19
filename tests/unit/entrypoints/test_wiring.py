"""`select_composer` — the composition-root switch (design.md D14, D23)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rain_alert.adapters.agent_composer import AgentBackedComposer
from rain_alert.adapters.agentcore_invoker import AGENT_RUNTIME_ARN_ENV_VAR, BedrockAgentCoreInvoker
from rain_alert.domain.config import AlertConfig, CoordinatesSource, RiskThresholds
from rain_alert.domain.template import MessageComposer as TemplateMessageComposer
from rain_alert.domain.values import ComposerName, Coordinates, WarningLevel
from rain_alert.entrypoints.wiring import select_composer
from tests.support.fakes import FakeNotifier

NOW = datetime(2026, 9, 3, 12, tzinfo=UTC)
FAKE_ARN = "arn:aws:bedrock-agentcore:us-east-1:example-account-id:runtime/example-runtime-abc123"


def _config(composer: ComposerName) -> AlertConfig:
    thresholds = RiskThresholds(
        prepare_mm_48h=9.5,
        prepare_probability_pct=60,
        prepare_probability_pct_degraded=70,
        prepare_warning_levels=frozenset({WarningLevel.YELLOW}),
        imminent_mm_24h=20.0,
        imminent_probability_pct=70,
        imminent_warning_levels=frozenset({WarningLevel.RED}),
    )
    return AlertConfig(
        city="Ferreñafe",
        city_slug="ferrenafe",
        coordinates=Coordinates(latitude=-6.636005, longitude=-79.789860),
        coordinates_source=CoordinatesSource.PUBLIC_REFERENCE,
        timezone="America/Lima",
        region="Lambayeque",
        thresholds=thresholds,
        checklist=(),
        active_channel="console",
        forecast_hours=48,
        dedup_lookback_hours=72,
        composer=composer,
    )


def test_template_selects_the_deterministic_composer() -> None:
    composer = select_composer(_config(ComposerName.TEMPLATE), notifier=FakeNotifier(), now=lambda: NOW)

    assert isinstance(composer, TemplateMessageComposer)


def test_template_selection_needs_no_agent_runtime_arn(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default path must not depend on an environment variable only the
    agent path needs — a clean `template`-only deploy must not fail wiring."""
    monkeypatch.delenv(AGENT_RUNTIME_ARN_ENV_VAR, raising=False)

    composer = select_composer(_config(ComposerName.TEMPLATE), notifier=FakeNotifier(), now=lambda: NOW)

    assert isinstance(composer, TemplateMessageComposer)


def test_agent_selects_a_fully_wired_agent_backed_composer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(AGENT_RUNTIME_ARN_ENV_VAR, FAKE_ARN)

    composer = select_composer(_config(ComposerName.AGENT), notifier=FakeNotifier(), now=lambda: NOW)

    assert isinstance(composer, AgentBackedComposer)
    assert isinstance(composer._invoker, BedrockAgentCoreInvoker)  # noqa: SLF001 — the property under test


def test_agent_selection_fails_loudly_with_no_runtime_arn_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """An operator who flips `RAIN_ALERT_COMPOSER` to `agent` without also
    setting the ARN needs to hear about it at wiring time, not on the first
    cycle six hours later."""
    monkeypatch.delenv(AGENT_RUNTIME_ARN_ENV_VAR, raising=False)

    with pytest.raises(ValueError, match=AGENT_RUNTIME_ARN_ENV_VAR):
        select_composer(_config(ComposerName.AGENT), notifier=FakeNotifier(), now=lambda: NOW)
