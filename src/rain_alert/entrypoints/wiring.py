"""`build_local_deps` — the real side of design.md D6's object graph.

Mirrors `tests.support.wiring.build_fake_deps`: both return the same
`CycleDependencies` type, so **the object graph the CLI runs is the object
graph the tests exercise** — only the leaves differ. That is the reason D6
exists, and it is what makes the CLI's behaviour predictable from the unit
suite.

`--offline-fixtures` swaps the two *fetch* leaves and nothing else: the real
scraper and the real payload parser still do the work.

The dry-run guarantee is structural. This function can only construct
`ConsoleNotifier`, and no notifier capable of delivering anything exists
anywhere in this codebase — which is why there is deliberately no
`--dry-run` flag (design.md 9).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import TextIO

from rain_alert.adapters.agent_composer import AgentBackedComposer
from rain_alert.adapters.agent_prompt import build_prompt
from rain_alert.adapters.agentcore_invoker import AgentRuntimeSettings, BedrockAgentCoreInvoker
from rain_alert.adapters.console_notifier import ConsoleNotifier
from rain_alert.adapters.dynamodb_alert_repository import TABLE_NAME, DynamoDbAlertRepository
from rain_alert.adapters.http import HtmlFetcher, HttpxHtmlFetcher
from rain_alert.adapters.local.json_alert_repository import JsonFileAlertRepository
from rain_alert.adapters.local.json_file_snapshot_publisher import SNAPSHOT_PATH_ENV_VAR, JsonFileSnapshotPublisher
from rain_alert.adapters.local.offline_sources import FileHtmlFetcher, OfflineOpenMeteoProvider
from rain_alert.adapters.local.static_config_repository import StaticConfigRepository
from rain_alert.adapters.local.static_contact_repository import StaticContactRepository
from rain_alert.adapters.open_meteo import OpenMeteoForecastProvider
from rain_alert.adapters.s3_relay_reader import RELAY_BUCKET_ENV_VAR, S3RelayReader, UnconfiguredRelayReader
from rain_alert.adapters.s3_snapshot_publisher import SNAPSHOT_BUCKET_ENV_VAR, SNAPSHOT_KEY_ENV_VAR, S3SnapshotPublisher
from rain_alert.adapters.senamhi_relay import RelayReadRecord, RelayWarningProvider
from rain_alert.adapters.senamhi_scraper import SenamhiWarningScraper
from rain_alert.adapters.ssm_config_repository import SsmConfigRepository
from rain_alert.application.dependencies import CycleDependencies
from rain_alert.domain.config import AlertConfig
from rain_alert.domain.risk import RiskEvaluator
from rain_alert.domain.template import MessageComposer
from rain_alert.domain.values import ComposerName
from rain_alert.entrypoints.run_once import default_now
from rain_alert.ports import ForecastProvider, Notifier, SnapshotPublisher, WarningProvider
from rain_alert.ports import MessageComposer as MessageComposerPort

SENAMHI_FIXTURE_NAME = "senamhi.html"
OPEN_METEO_FIXTURE_NAME = "open_meteo.json"


def select_composer(
    config: AlertConfig,
    *,
    notifier: Notifier,
    now: Callable[[], datetime],
    env: Mapping[str, str] | None = None,
) -> MessageComposerPort:
    """The switch (design D14, D23): a two-branch `if`, read once per cycle.

    `TEMPLATE` returns `domain.template.MessageComposer()` — literally the
    object `main` wires today, so "behaves exactly as `main`" is provable by
    construction. `AGENT` returns a fully-wired `AgentBackedComposer`, built
    from `AgentRuntimeSettings.from_env` (design D23: an ARN is an AWS
    concern and never widens `AlertConfig`).

    Args:
        config: This cycle's configuration; `config.composer` is the switch.
        notifier: The same `Notifier` the cycle delivers the community alert
            through. The agent-backed composer must be handed this exact
            instance, not a second one, because it sends the fallback notice
            itself (D21) and a notice landing anywhere else would be
            invisible to whatever observes this notifier.
        now: The cycle clock (D7), for the fallback notice's timestamp.
        env: Where `AgentRuntimeSettings` reads its two environment
            variables. Defaults to `os.environ`; overridable for tests.
    """
    if config.composer is ComposerName.TEMPLATE:
        return MessageComposer()

    settings = AgentRuntimeSettings.from_env(env if env is not None else os.environ)
    return AgentBackedComposer(
        invoker=BedrockAgentCoreInvoker.from_settings(settings),
        build_prompt=build_prompt,
        notifier=notifier,
        now=now,
    )


def select_snapshot_publisher(env: Mapping[str, str]) -> SnapshotPublisher | None:
    """Where this cycle's public snapshot goes, or `None` for "do not publish".

    Absent by default, on purpose: a developer running the cycle locally
    must not write to a public bucket by accident. The bucket wins when both
    are set, because S3 is the production destination and a leftover local
    path in the environment must not silently take over.

    The return type is deliberately `SnapshotPublisher | None` rather than
    `Any`: it is the one call site in this codebase that assigns a concrete
    adapter to a `Protocol`-typed slot, which is what makes mypy actually
    check both `S3SnapshotPublisher` and `JsonFileSnapshotPublisher` against
    the port they claim to satisfy (design.md D9 — `Protocol`, checked
    statically, never `@runtime_checkable`).

    Args:
        env: Where the three snapshot environment variables are read from.
            The caller passes `os.environ` in production and a plain `dict`
            in tests.
    """
    bucket = env.get(SNAPSHOT_BUCKET_ENV_VAR)
    if bucket:
        return S3SnapshotPublisher(bucket, key=env.get(SNAPSHOT_KEY_ENV_VAR, "status.json"))
    path = env.get(SNAPSHOT_PATH_ENV_VAR)
    return JsonFileSnapshotPublisher(Path(path)) if path else None


def select_warning_provider(
    env: Mapping[str, str],
    *,
    on_read: Callable[[RelayReadRecord], None] = lambda _: None,
) -> WarningProvider:
    """Where this cycle's SENAMHI warnings come from (design D43, proposal R7).

    The variable's *presence* selects the relay, not its truthiness, unlike
    `select_snapshot_publisher`, and on purpose: an empty value is a
    misconfigured relay and must degrade the cycle, never become a direct
    scrape. The relay branch constructs no `HtmlFetcher`, so there is
    structurally no direct-HTTP path to fall back to.

    Args:
        env: `os.environ` in production, a plain `dict` in tests.
        on_read: Receives one `RelayReadRecord` per relay read. Unused when
            the relay is not configured.
    """
    if RELAY_BUCKET_ENV_VAR not in env:
        return SenamhiWarningScraper(HttpxHtmlFetcher())
    bucket = env[RELAY_BUCKET_ENV_VAR]
    reader = S3RelayReader(bucket) if bucket else UnconfiguredRelayReader()
    return RelayWarningProvider(reader, on_read=on_read)


def build_local_deps(
    *,
    state_file: Path,
    now: Callable[[], datetime],
    offline_fixtures: Path | None = None,
    notifier_stream: TextIO | None = None,
    composer_override: ComposerName | None = None,
) -> CycleDependencies:
    """The full dependency graph for a local cycle.

    Args:
        state_file: Where dedup and outage state is persisted. Gitignored.
        now: The clock (D7); the CLI passes `--now` or the system clock.
        offline_fixtures: When given, a directory holding
            `senamhi.html` and `open_meteo.json`; the cycle then makes no
            network call at all.
        notifier_stream: Where `ConsoleNotifier` writes its blocks. `None`
            keeps the notifier's own default of standard output.
        composer_override: The CLI's `--composer` flag (design D24). `None`
            (the default, and every path except that one flag) uses
            `config.composer` unchanged. Absent flag = "whatever config
            says", never a boolean default.

            This is a parameter because the caller, not the notifier, knows
            what else is going to standard output. The CLI's `--json` mode
            emits one machine-readable document for calibration logging, and
            the notifier defaulting to the same stream meant every run that
            actually sent or emitted a notice prefixed that document with a
            dry-run block and broke `json.loads` — on exactly the runs worth
            logging. It stays a stream rather than a boolean because the
            notifier's job is to write somewhere, not to know about `--json`.
    """
    fetcher: HtmlFetcher
    forecast: ForecastProvider
    if offline_fixtures is None:
        fetcher = HttpxHtmlFetcher()
        forecast = OpenMeteoForecastProvider()
    else:
        fetcher = FileHtmlFetcher(offline_fixtures / SENAMHI_FIXTURE_NAME)
        forecast = OfflineOpenMeteoProvider(offline_fixtures / OPEN_METEO_FIXTURE_NAME)

    config_repository = StaticConfigRepository()
    notifier = ConsoleNotifier(notifier_stream)
    # `config.load()` is called a second time inside `RunAlertCycle.execute`
    # for the cycle itself (D14). For `StaticConfigRepository` that is free;
    # a memoizing `ConfigRepository` is the natural fix if a future backend
    # makes it not free, not a change here.
    config = config_repository.load()
    if composer_override is not None:
        config = replace(config, composer=composer_override)
    composer = select_composer(config, notifier=notifier, now=now)

    return CycleDependencies(
        config=config_repository,
        warnings=SenamhiWarningScraper(fetcher),
        forecast=forecast,
        composer=composer,
        contacts=StaticContactRepository(),
        notifier=notifier,
        alerts=JsonFileAlertRepository(state_file),
        evaluator_factory=RiskEvaluator,
        now=now,
    )


def build_cloud_deps(env: Mapping[str, str] | None = None) -> CycleDependencies:
    """The full dependency graph for a scheduled cycle (design.md D6, D29).

    The second leaf set over the same `CycleDependencies` type
    `build_local_deps` returns — `RunAlertCycle`, `select_composer` and every
    port are exactly the same; only the leaves differ. Takes no arguments:
    `lambda_handler.handler` calls this with nothing from `event` (the
    schedule payload carries no fact the cycle needs), and the clock is
    always the real one — there is no `--offline-fixtures`/`--now` equivalent
    here, because a scheduled cycle always runs against the real sources,
    the real table and the real clock.

    Args:
        env: Where `RAIN_ALERT_RELAY_BUCKET` is read. `None` (the handler's
            call) means `os.environ`; tests pass a plain `dict`.
    """
    config_repository = SsmConfigRepository()
    notifier = ConsoleNotifier()
    config = config_repository.load()
    composer = select_composer(config, notifier=notifier, now=default_now)

    return CycleDependencies(
        config=config_repository,
        warnings=select_warning_provider(os.environ if env is None else env),
        forecast=OpenMeteoForecastProvider(),
        composer=composer,
        contacts=StaticContactRepository(),
        notifier=notifier,
        alerts=DynamoDbAlertRepository(TABLE_NAME),
        evaluator_factory=RiskEvaluator,
        now=default_now,
    )
