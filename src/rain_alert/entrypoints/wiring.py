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

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TextIO

from rain_alert.adapters.console_notifier import ConsoleNotifier
from rain_alert.adapters.http import HtmlFetcher, HttpxHtmlFetcher
from rain_alert.adapters.local.json_alert_repository import JsonFileAlertRepository
from rain_alert.adapters.local.offline_sources import FileHtmlFetcher, OfflineOpenMeteoProvider
from rain_alert.adapters.local.static_config_repository import StaticConfigRepository
from rain_alert.adapters.local.static_contact_repository import StaticContactRepository
from rain_alert.adapters.open_meteo import OpenMeteoForecastProvider
from rain_alert.adapters.senamhi_scraper import SenamhiWarningScraper
from rain_alert.application.dependencies import CycleDependencies
from rain_alert.domain.risk import RiskEvaluator
from rain_alert.domain.template import MessageComposer
from rain_alert.ports import ForecastProvider

SENAMHI_FIXTURE_NAME = "senamhi.html"
OPEN_METEO_FIXTURE_NAME = "open_meteo.json"


def build_local_deps(
    *,
    state_file: Path,
    now: Callable[[], datetime],
    offline_fixtures: Path | None = None,
    notifier_stream: TextIO | None = None,
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

    return CycleDependencies(
        config=StaticConfigRepository(),
        warnings=SenamhiWarningScraper(fetcher),
        forecast=forecast,
        composer=MessageComposer(),
        contacts=StaticContactRepository(),
        notifier=ConsoleNotifier(notifier_stream),
        alerts=JsonFileAlertRepository(state_file),
        evaluator_factory=RiskEvaluator,
        now=now,
    )
