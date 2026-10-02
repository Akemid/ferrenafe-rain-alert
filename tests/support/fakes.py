"""Hand-written recording spies for all eight ports (design.md section 10).

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

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from rain_alert.adapters.local.in_memory_alert_repository import InMemoryAlertRepository
from rain_alert.adapters.senamhi_relay import RelayObject
from rain_alert.domain.config import AlertConfig
from rain_alert.domain.entities import Contact, Forecast, RiskAssessment, Warning
from rain_alert.domain.messages import AlertMessage, AlertRecord, MessageRequest, OperatorNotice, OutageRecord
from rain_alert.domain.risk import RiskEvaluator
from rain_alert.domain.sources import SourceResult
from rain_alert.domain.template import MessageComposer as TemplateMessageComposer
from rain_alert.domain.values import Coordinates

#: "Nothing was scripted", distinct from a scripted `None`. See `FakeAgentInvoker`.
_UNSET: Any = object()


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


class FakeAgentInvoker:
    """`AgentInvoker` that returns a scripted mapping or raises a scripted error.

    It is the whole offline fault table's driver: every transport row scripts
    an `AgentInvocationError`, every payload row scripts a response, and row
    12 scripts a bare `RuntimeError` to prove an exception type the composer
    never anticipated is still absorbed.

    `calls` is the list of prompts. Two properties are asserted on it and
    nowhere else: a deduplicated cycle invokes the seam **zero** times, and an
    authorized one invokes it **exactly once**, with no retry.

    Constructing it with neither a response nor an error raises rather than
    returning `None`, because a silent `None` reaching a composer typed to
    receive a mapping fails somewhere else entirely and reads as a defect in
    the code under test. `_UNSET` rather than `None` marks "nothing was
    scripted", because `None` is itself one of the shapes worth scripting: a
    real invoker decodes JSON, and `null` decodes to it.

    `response` is deliberately `Any`, and `invoke` keeps the Protocol's
    `Mapping[str, Any]` return annotation while being able to return
    something else. That mismatch **is** the fault under test: the composer's
    shape rows stand for an SDK-backed invoker that breaks its own declared
    contract, which is precisely the case a type annotation cannot prevent.
    """

    def __init__(
        self,
        response: Any = _UNSET,
        error: BaseException | None = None,
        log: CallLog | None = None,
    ) -> None:
        if response is _UNSET and error is None:
            raise ValueError("FakeAgentInvoker needs a scripted response or a scripted error")
        self._response = response
        self._error = error
        self.calls: list[str] = []
        self.log = log if log is not None else CallLog()

    def invoke(self, prompt: str) -> Mapping[str, Any]:
        self.calls.append(prompt)
        self.log.record("invoker", "invoke", str(len(self.calls)))
        if self._error is not None:
            raise self._error
        assert self._response is not _UNSET  # guarded at construction
        return self._response


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
    """Both notifier channels, recorded, with the operator one breakable.

    `notice_error` exists because the two channels fail independently in
    production. `ConsoleNotifier` writes to a stream today, so a closed
    descriptor, a full disk or a piped-into command that exited all reach the
    operator channel alone; in change 3 it becomes a network notifier where
    transient failure is routine. A test sets it *after* `build_fake_deps`
    has built the graph, because the agent-backed composer must be given the
    very notifier the cycle delivers the alert through.

    The attempt is recorded **before** the raise: "exactly one notice per
    fallback" is a claim about attempts, and a notice that was tried and
    failed still consumed the fallback's one chance to speak.
    """

    alert_calls: list[tuple[AlertMessage, tuple[Contact, ...]]] = field(default_factory=list)
    notice_calls: list[OperatorNotice] = field(default_factory=list)
    log: CallLog = field(default_factory=CallLog)
    notice_error: BaseException | None = None

    def send_alert(self, message: AlertMessage, recipients: tuple[Contact, ...]) -> None:
        self.alert_calls.append((message, recipients))
        self.log.record("notifier", "send_alert", message.title)

    def send_operator_notice(self, notice: OperatorNotice) -> None:
        self.notice_calls.append(notice)
        self.log.record("notifier", "send_operator_notice", notice.kind.value)
        if self.notice_error is not None:
            raise self.notice_error


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


@dataclass
class RecordingPublisher:
    """`SnapshotPublisher` that records every document it was given, in order.

    Unlike the other fakes here it is not part of `build_fake_deps`'s object
    graph: publishing is called from the entry point, after `RunAlertCycle`
    has already returned (see `ports.SnapshotPublisher`), so tests wire it in
    directly rather than through `CycleDependencies`.
    """

    documents: list[dict[str, Any]] = field(default_factory=list)

    def publish(self, document: dict[str, Any]) -> None:
        self.documents.append(document)


@dataclass
class FakeRelayReader:
    """`RelayObjectReader` that returns a programmed object or raises a programmed error.

    `result` is either a `RelayObject` (returned) or an exception instance
    (raised unchanged), so one fake covers a `FetchError` and an arbitrary
    surprise alike.
    """

    result: RelayObject | BaseException
    calls: int = 0

    def read(self) -> RelayObject:
        self.calls += 1
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


@dataclass
class FakeHtmlFetcher:
    """`HtmlFetcher` that returns a programmed page or raises a programmed error.

    `result` is either the page text (returned) or an exception instance
    (raised unchanged). Every requested URL is recorded.
    """

    result: str | BaseException
    urls: list[str] = field(default_factory=list)

    def fetch(self, url: str) -> str:
        self.urls.append(url)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result
