"""`select_composer` — the composition-root switch (design.md D14, D23)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import rain_alert.adapters.dynamodb_alert_repository as dynamodb_alert_repository
import rain_alert.adapters.s3_snapshot_publisher as s3_snapshot_publisher
import rain_alert.entrypoints.wiring as wiring
from rain_alert.adapters.agent_composer import AgentBackedComposer
from rain_alert.adapters.agentcore_invoker import AGENT_RUNTIME_ARN_ENV_VAR, BedrockAgentCoreInvoker
from rain_alert.adapters.console_notifier import ConsoleNotifier
from rain_alert.adapters.dynamodb_alert_repository import TABLE_NAME, DynamoDbAlertRepository
from rain_alert.adapters.local.json_file_snapshot_publisher import SNAPSHOT_PATH_ENV_VAR, JsonFileSnapshotPublisher
from rain_alert.adapters.local.static_contact_repository import StaticContactRepository
from rain_alert.adapters.open_meteo import OpenMeteoForecastProvider
from rain_alert.adapters.s3_snapshot_publisher import (
    SNAPSHOT_BUCKET_ENV_VAR,
    SNAPSHOT_KEY_ENV_VAR,
    S3SnapshotPublisher,
)
from rain_alert.adapters.senamhi_scraper import SenamhiWarningScraper
from rain_alert.domain.config import AlertConfig, CoordinatesSource, RiskThresholds
from rain_alert.domain.template import MessageComposer as TemplateMessageComposer
from rain_alert.domain.values import ComposerName, Coordinates, WarningLevel
from rain_alert.entrypoints.wiring import select_composer, select_snapshot_publisher
from tests.support.fakes import FakeNotifier

NOW = datetime(2026, 9, 3, 12, tzinfo=UTC)
FAKE_ARN = "arn:aws:bedrock-agentcore:us-east-1:example-account-id:runtime/example-runtime-abc123"


class _SpyS3Client:
    """Records what it was asked to put. No `unittest.mock`, per project rule."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {}


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


class TestSelectSnapshotPublisher:
    """`select_snapshot_publisher` (public-status-page): "do not publish" is
    the default, on purpose — a developer running the cycle locally must not
    write to a public bucket by accident."""

    def test_nothing_is_published_when_no_destination_is_configured(self) -> None:
        assert select_snapshot_publisher({}) is None

    def test_a_bucket_selects_the_s3_publisher(self) -> None:
        publisher = select_snapshot_publisher({SNAPSHOT_BUCKET_ENV_VAR: "ferrenafe-status"})

        assert isinstance(publisher, S3SnapshotPublisher)

    def test_the_key_env_var_is_honoured_when_the_bucket_is_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Asserted through observable behaviour — the `Key=` `publish()`
        actually sends — rather than through `S3SnapshotPublisher`'s private
        `_key` attribute, which could be renamed without this test noticing
        a real regression. `boto3.client` is monkeypatched as the
        client-factory seam (same pattern as
        `test_agentcore_invoker.py::test_the_real_client_is_built_...`), so
        no real client is ever built."""
        spy = _SpyS3Client()
        monkeypatch.setattr(s3_snapshot_publisher.boto3, "client", lambda *args, **kwargs: spy)
        publisher = select_snapshot_publisher(
            {SNAPSHOT_BUCKET_ENV_VAR: "ferrenafe-status", SNAPSHOT_KEY_ENV_VAR: "snapshots/latest.json"}
        )
        assert isinstance(publisher, S3SnapshotPublisher)

        publisher.publish({})

        assert spy.calls[0]["Key"] == "snapshots/latest.json"

    def test_a_path_selects_the_local_file_publisher(self, tmp_path: Path) -> None:
        publisher = select_snapshot_publisher({SNAPSHOT_PATH_ENV_VAR: str(tmp_path / "status.json")})

        assert isinstance(publisher, JsonFileSnapshotPublisher)

    def test_the_bucket_wins_when_both_are_configured(self, tmp_path: Path) -> None:
        """S3 is the production destination; a leftover local path in the
        environment must not silently take over."""
        publisher = select_snapshot_publisher(
            {SNAPSHOT_BUCKET_ENV_VAR: "ferrenafe-status", SNAPSHOT_PATH_ENV_VAR: str(tmp_path / "status.json")}
        )

        assert isinstance(publisher, S3SnapshotPublisher)


class _SpyDynamoDbQueryPaginator:
    """Records every `paginate()` call's kwargs before returning one empty
    page. Same wrap-and-delegate spy shape
    `test_dynamodb_alert_repository.py`'s `_RecordingQueryPaginator` already
    uses, minimal here since only the `TableName=` kwarg is under test."""

    def __init__(self, calls: list[dict[str, Any]]) -> None:
        self._calls = calls

    def paginate(self, **kwargs: Any) -> Any:
        self._calls.append(kwargs)
        return [{"Items": []}]


class _SpyDynamoDbClient:
    """A hand-written spy standing in for `boto3.client("dynamodb")`,
    recording the `TableName=` a `Query` was actually sent with. No
    `unittest.mock`, per project rule."""

    def __init__(self) -> None:
        self.query_calls: list[dict[str, Any]] = []

    def get_paginator(self, operation_name: str) -> Any:
        assert operation_name == "query"
        return _SpyDynamoDbQueryPaginator(self.query_calls)


class _FakeSsmConfigRepository:
    """Stands in for `SsmConfigRepository` so `build_cloud_deps` is tested
    with no SSM client ever constructed (D28's offline guarantee, same
    posture as `build_local_deps`'s own tests)."""

    def __init__(self, config: AlertConfig) -> None:
        self._config = config

    def load(self) -> AlertConfig:
        return self._config


class TestBuildCloudDeps:
    """`build_cloud_deps` (design.md D6, D29) — the second leaf set over
    `CycleDependencies`. Only the leaves differ from `build_local_deps`;
    `RunAlertCycle` and `select_composer` are exactly the same."""

    def test_build_cloud_deps_constructs_only_console_notifier(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The cloud twin of `test_the_wiring_can_only_construct_the_console_notifier`."""
        fake_config = _config(ComposerName.TEMPLATE)
        monkeypatch.setattr(wiring, "SsmConfigRepository", lambda: _FakeSsmConfigRepository(fake_config))

        deps = wiring.build_cloud_deps()

        assert isinstance(deps.notifier, ConsoleNotifier)
        assert isinstance(deps.warnings, SenamhiWarningScraper)
        assert isinstance(deps.forecast, OpenMeteoForecastProvider)
        assert isinstance(deps.contacts, StaticContactRepository)
        assert isinstance(deps.alerts, DynamoDbAlertRepository)

    def test_build_cloud_deps_wires_the_alerts_repository_to_the_correct_table(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fix round 1, item 2: which physical table the cloud graph reads
        and writes is the single most consequential fact in
        `build_cloud_deps` — a wrong table means dedup is silently inert in
        production, the exact failure D29's architecture test exists to
        prevent for imports and this test exists to prevent for
        configuration. Asserted through observable behaviour — the
        `TableName=` a real `Query` call actually carries — rather than
        through `DynamoDbAlertRepository`'s private `_table_name`
        attribute, following the same remedy
        `test_the_key_env_var_is_honoured_when_the_bucket_is_set` already
        applies to `S3SnapshotPublisher`'s private `_key`."""
        fake_config = _config(ComposerName.TEMPLATE)
        monkeypatch.setattr(wiring, "SsmConfigRepository", lambda: _FakeSsmConfigRepository(fake_config))
        spy = _SpyDynamoDbClient()
        monkeypatch.setattr(dynamodb_alert_repository.boto3, "client", lambda *args, **kwargs: spy)

        deps = wiring.build_cloud_deps()
        deps.alerts.alerts_with_window_start_between(
            "ferrenafe", datetime(2026, 9, 3, tzinfo=UTC), datetime(2026, 9, 4, tzinfo=UTC)
        )

        assert spy.query_calls[0]["TableName"] == TABLE_NAME

    def test_build_cloud_deps_reuses_select_composer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`select_composer` is invoked with the loaded config exactly as
        `build_local_deps` does; no second switch is introduced."""
        fake_config = _config(ComposerName.TEMPLATE)
        monkeypatch.setattr(wiring, "SsmConfigRepository", lambda: _FakeSsmConfigRepository(fake_config))
        recorded: list[AlertConfig] = []
        real_select_composer = wiring.select_composer

        def _spy(config: AlertConfig, **kwargs: object) -> object:
            recorded.append(config)
            return real_select_composer(config, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(wiring, "select_composer", _spy)

        deps = wiring.build_cloud_deps()

        assert recorded == [fake_config]
        assert isinstance(deps.composer, TemplateMessageComposer)
