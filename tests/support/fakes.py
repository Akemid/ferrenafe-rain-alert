"""Hand-written recording spies for all seven ports (design.md section 10).

`unittest.mock` is deliberately avoided: `Mock` satisfies any `Protocol` and
would hide exactly the drift these tests exist to catch. Every fake records
its calls so tests can assert order and arguments, not just return values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

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


@dataclass
class FakeAlertRepository:
    alerts: list[AlertRecord] = field(default_factory=list)
    active_outage: OutageRecord | None = None
    query_calls: list[tuple[str, datetime, datetime]] = field(default_factory=list)
    record_calls: list[AlertRecord] = field(default_factory=list)
    save_outage_calls: list[OutageRecord] = field(default_factory=list)
    clear_outage_calls: list[str] = field(default_factory=list)

    def alerts_with_window_start_between(
        self, city_slug: str, earliest_start: datetime, latest_start: datetime
    ) -> tuple[AlertRecord, ...]:
        self.query_calls.append((city_slug, earliest_start, latest_start))
        matches = [
            record
            for record in self.alerts
            if record.city_slug == city_slug and earliest_start <= record.window.start <= latest_start
        ]
        return tuple(sorted(matches, key=lambda record: record.window.start))

    def record_alert(self, record: AlertRecord) -> None:
        self.alerts.append(record)
        self.record_calls.append(record)

    def get_active_outage(self, city_slug: str) -> OutageRecord | None:
        if self.active_outage is not None and self.active_outage.city_slug == city_slug:
            return self.active_outage
        return None

    def save_active_outage(self, record: OutageRecord) -> None:
        self.active_outage = record
        self.save_outage_calls.append(record)

    def clear_active_outage(self, city_slug: str) -> None:
        self.active_outage = None
        self.clear_outage_calls.append(city_slug)
