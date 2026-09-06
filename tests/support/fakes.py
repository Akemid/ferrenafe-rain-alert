"""Hand-written recording spies for all seven ports (design.md section 10).

`unittest.mock` is deliberately avoided: `Mock` satisfies any `Protocol` and
would hide exactly the drift these tests exist to catch. Every fake records
its calls so tests can assert order and arguments, not just return values.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from rain_alert.adapters.local.in_memory_alert_repository import InMemoryAlertRepository
from rain_alert.domain.config import AlertConfig
from rain_alert.domain.entities import Contact, Forecast, Warning
from rain_alert.domain.messages import AlertMessage, AlertRecord, MessageRequest, OperatorNotice, OutageRecord
from rain_alert.domain.sources import SourceResult
from rain_alert.domain.template import MessageComposer as TemplateMessageComposer
from rain_alert.domain.values import Coordinates


@dataclass
class FakeConfigRepository:
    config: AlertConfig
    calls: int = 0

    def load(self) -> AlertConfig:
        self.calls += 1
        return self.config


@dataclass
class FakeWarningProvider:
    result: SourceResult[tuple[Warning, ...]]
    calls: list[tuple[str, datetime]] = field(default_factory=list)

    def fetch_current_warnings(self, region: str, now: datetime) -> SourceResult[tuple[Warning, ...]]:
        self.calls.append((region, now))
        return self.result


@dataclass
class FakeForecastProvider:
    result: SourceResult[Forecast]
    calls: list[tuple[Coordinates, int, datetime]] = field(default_factory=list)

    def fetch_forecast(self, location: Coordinates, hours: int, now: datetime) -> SourceResult[Forecast]:
        self.calls.append((location, hours, now))
        return self.result


@dataclass
class FakeMessageComposer:
    """Delegates to the real deterministic template by default, so tests
    assert on content without duplicating template logic. `calls` is what
    "the composer was/was not invoked" assertions check."""

    calls: list[MessageRequest] = field(default_factory=list)
    _delegate: TemplateMessageComposer = field(default_factory=TemplateMessageComposer)

    def compose(self, request: MessageRequest) -> AlertMessage:
        self.calls.append(request)
        return self._delegate.compose(request)


@dataclass
class FakeContactRepository:
    contacts: tuple[Contact, ...]
    calls: list[str] = field(default_factory=list)

    def list_active(self, channel: str) -> tuple[Contact, ...]:
        self.calls.append(channel)
        return self.contacts


@dataclass
class FakeNotifier:
    alert_calls: list[tuple[AlertMessage, tuple[Contact, ...]]] = field(default_factory=list)
    notice_calls: list[OperatorNotice] = field(default_factory=list)

    def send_alert(self, message: AlertMessage, recipients: tuple[Contact, ...]) -> None:
        self.alert_calls.append((message, recipients))

    def send_operator_notice(self, notice: OperatorNotice) -> None:
        self.notice_calls.append(notice)


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
    ) -> None:
        self._state = InMemoryAlertRepository(alerts=alerts, active_outage=active_outage)
        self.query_calls: list[tuple[str, datetime, datetime]] = []
        self.record_calls: list[AlertRecord] = []
        self.save_outage_calls: list[OutageRecord] = []
        self.clear_outage_calls: list[str] = []

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
        return self._state.alerts_with_window_start_between(city_slug, earliest_start, latest_start)

    def record_alert(self, record: AlertRecord) -> None:
        self._state.record_alert(record)
        self.record_calls.append(record)

    def get_active_outage(self, city_slug: str) -> OutageRecord | None:
        return self._state.get_active_outage(city_slug)

    def save_active_outage(self, record: OutageRecord) -> None:
        self._state.save_active_outage(record)
        self.save_outage_calls.append(record)

    def clear_active_outage(self, city_slug: str) -> None:
        self._state.clear_active_outage(city_slug)
        self.clear_outage_calls.append(city_slug)
