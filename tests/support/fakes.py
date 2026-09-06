"""Hand-written recording spies for all seven ports (design.md section 10).

`unittest.mock` is deliberately avoided: `Mock` satisfies any `Protocol` and
would hide exactly the drift these tests exist to catch.

Every fake records its own calls **and appends to one `CallLog` shared by the
whole object graph**, so a test can assert order across ports and not only
within one. The per-spy lists answer "what was this port given"; the shared log
answers "what happened before what".

The shared log exists because the `alert-cycle` requirement is about ordering —
the steps run in a stated order, and an alert is recorded *only after* the
notifier delivered it — and private per-spy lists made those two guarantees
unassertable. Moving `record_alert` above `send_alert` in the use case left the
entire suite green, which is precisely the failure the requirement exists to
prevent: an alert recorded but never delivered makes the next cycle believe the
community was told, so the genuine alert is deduplicated away.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from rain_alert.adapters.local.in_memory_alert_repository import InMemoryAlertRepository
from rain_alert.domain.config import AlertConfig
from rain_alert.domain.entities import Contact, Forecast, RiskAssessment, Warning
from rain_alert.domain.messages import AlertMessage, AlertRecord, MessageRequest, OperatorNotice, OutageRecord
from rain_alert.domain.risk import RiskEvaluator
from rain_alert.domain.sources import SourceResult
from rain_alert.domain.template import MessageComposer as TemplateMessageComposer
from rain_alert.domain.values import Coordinates


@dataclass
class CallLog:
    """One ordered log of every port call the cycle made, across all spies.

    An entry is `(port, method, detail)`. `detail` is a short identifying
    string — the city slug, the level, the message title — so a failure names
    *which* call it was, not only that some call happened.
    """

    entries: list[tuple[str, str, str]] = field(default_factory=list)

    def record(self, port: str, method: str, detail: str = "") -> None:
        self.entries.append((port, method, detail))

    @property
    def steps(self) -> tuple[tuple[str, str], ...]:
        """The sequence of `(port, method)` pairs, for whole-order assertions."""
        return tuple((port, method) for port, method, _ in self.entries)

    def position_of(self, port: str, method: str) -> int:
        """Index of the first call to `port.method`, for relative-order assertions."""
        try:
            return self.steps.index((port, method))
        except ValueError:
            raise AssertionError(f"{port}.{method} was never called; the log holds {self.steps}") from None


@dataclass
class FakeConfigRepository:
    config: AlertConfig
    calls: int = 0
    log: CallLog = field(default_factory=CallLog)

    def load(self) -> AlertConfig:
        self.calls += 1
        self.log.record("config", "load", self.config.city_slug)
        return self.config


@dataclass
class FakeWarningProvider:
    result: SourceResult[tuple[Warning, ...]]
    calls: list[tuple[str, datetime]] = field(default_factory=list)
    log: CallLog = field(default_factory=CallLog)

    def fetch_current_warnings(self, region: str, now: datetime) -> SourceResult[tuple[Warning, ...]]:
        self.calls.append((region, now))
        self.log.record("warnings", "fetch_current_warnings", region)
        return self.result


@dataclass
class FakeForecastProvider:
    result: SourceResult[Forecast]
    calls: list[tuple[Coordinates, int, datetime]] = field(default_factory=list)
    log: CallLog = field(default_factory=CallLog)

    def fetch_forecast(self, location: Coordinates, hours: int, now: datetime) -> SourceResult[Forecast]:
        self.calls.append((location, hours, now))
        self.log.record("forecast", "fetch_forecast", f"{hours}h")
        return self.result


@dataclass
class FakeMessageComposer:
    """Delegates to the real deterministic template by default, so tests
    assert on content without duplicating template logic. `calls` is what
    "the composer was/was not invoked" assertions check."""

    calls: list[MessageRequest] = field(default_factory=list)
    log: CallLog = field(default_factory=CallLog)
    _delegate: TemplateMessageComposer = field(default_factory=TemplateMessageComposer)

    def compose(self, request: MessageRequest) -> AlertMessage:
        self.calls.append(request)
        self.log.record("composer", "compose", request.level.value)
        return self._delegate.compose(request)


@dataclass
class FakeContactRepository:
    contacts: tuple[Contact, ...]
    calls: list[str] = field(default_factory=list)
    log: CallLog = field(default_factory=CallLog)

    def list_active(self, channel: str) -> tuple[Contact, ...]:
        self.calls.append(channel)
        self.log.record("contacts", "list_active", channel)
        return self.contacts


@dataclass
class FakeNotifier:
    alert_calls: list[tuple[AlertMessage, tuple[Contact, ...]]] = field(default_factory=list)
    notice_calls: list[OperatorNotice] = field(default_factory=list)
    log: CallLog = field(default_factory=CallLog)

    def send_alert(self, message: AlertMessage, recipients: tuple[Contact, ...]) -> None:
        self.alert_calls.append((message, recipients))
        self.log.record("notifier", "send_alert", message.title)

    def send_operator_notice(self, notice: OperatorNotice) -> None:
        self.notice_calls.append(notice)
        self.log.record("notifier", "send_operator_notice", notice.kind.value)


@dataclass(frozen=True)
class LoggingRiskEvaluator(RiskEvaluator):
    """The real evaluator, wrapped so "evaluate risk" is an observable step.

    It **subclasses** `RiskEvaluator` rather than duck-typing a stand-in, so
    `CycleDependencies.evaluator_factory` keeps its declared return type, and
    it delegates the verdict to `super()` so the fake wiring never evaluates
    risk differently from production.
    """

    log: CallLog = field(default_factory=CallLog)

    def evaluate(
        self,
        warnings: SourceResult[tuple[Warning, ...]],
        forecast: SourceResult[Forecast],
        now: datetime,
    ) -> RiskAssessment:
        assessment = super().evaluate(warnings, forecast, now)
        self.log.record("evaluator", "evaluate", assessment.level.value)
        return assessment


class FakeAlertRepository:
    """`AlertRepository` that records its calls around a real one.

    It **composes** `InMemoryAlertRepository` rather than reimplementing the
    query. `in_memory_alert_repository.py` says in as many words that a second
    copy of "filter by city, filter by window-start range, sort ascending" is
    the kind of duplication that diverges silently and re-alerts a community,
    and that is exactly what happened here: the third copy also cleared the
    active outage without checking `city_slug`, so a fake the use-case tests
    trust behaved differently from production.

    This class therefore owns exactly one thing — the call log. Behaviour is
    the real store's, and the port contract in
    `tests/unit/adapters/test_local_repositories.py` runs against this class
    too, so the two cannot drift again.
    """

    def __init__(
        self,
        alerts: Iterable[AlertRecord] = (),
        active_outage: OutageRecord | None = None,
        log: CallLog | None = None,
    ) -> None:
        self._state = InMemoryAlertRepository(alerts=alerts, active_outage=active_outage)
        self.query_calls: list[tuple[str, datetime, datetime]] = []
        self.record_calls: list[AlertRecord] = []
        self.save_outage_calls: list[OutageRecord] = []
        self.clear_outage_calls: list[str] = []
        self.log = log if log is not None else CallLog()

    @property
    def alerts(self) -> tuple[AlertRecord, ...]:
        """Everything recorded so far, for assertions on stored state."""
        return self._state.snapshot()[0]

    @property
    def active_outage(self) -> OutageRecord | None:
        return self._state.snapshot()[1]

    def alerts_with_window_start_between(
        self, city_slug: str, earliest_start: datetime, latest_start: datetime
    ) -> tuple[AlertRecord, ...]:
        self.query_calls.append((city_slug, earliest_start, latest_start))
        self.log.record("alerts", "alerts_with_window_start_between", city_slug)
        return self._state.alerts_with_window_start_between(city_slug, earliest_start, latest_start)

    def record_alert(self, record: AlertRecord) -> None:
        self._state.record_alert(record)
        self.record_calls.append(record)
        self.log.record("alerts", "record_alert", record.level.value)

    def get_active_outage(self, city_slug: str) -> OutageRecord | None:
        self.log.record("alerts", "get_active_outage", city_slug)
        return self._state.get_active_outage(city_slug)

    def save_active_outage(self, record: OutageRecord) -> None:
        self._state.save_active_outage(record)
        self.save_outage_calls.append(record)
        self.log.record("alerts", "save_active_outage", record.city_slug)

    def clear_active_outage(self, city_slug: str) -> None:
        self._state.clear_active_outage(city_slug)
        self.clear_outage_calls.append(city_slug)
        self.log.record("alerts", "clear_active_outage", city_slug)
