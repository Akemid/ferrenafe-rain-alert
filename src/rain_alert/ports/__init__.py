"""The seven ports `RunAlertCycle` depends on (design.md section 4, *contract*).

`typing.Protocol`, structural typing — fakes in `tests/support/fakes.py` do
not subclass these. `@runtime_checkable` is deliberately not used: it only
checks method *names*, giving false confidence. Conformance is guarded
statically by `mypy --strict` (D9).
"""

from datetime import datetime
from typing import Protocol

from rain_alert.domain.config import AlertConfig
from rain_alert.domain.entities import Contact, Forecast, Warning
from rain_alert.domain.messages import AlertMessage, AlertRecord, MessageRequest, OperatorNotice, OutageRecord
from rain_alert.domain.sources import SourceResult
from rain_alert.domain.values import Coordinates


class ConfigRepository(Protocol):
    def load(self) -> AlertConfig: ...


class WarningProvider(Protocol):
    def fetch_current_warnings(self, region: str, now: datetime) -> SourceResult[tuple[Warning, ...]]: ...


class ForecastProvider(Protocol):
    def fetch_forecast(self, location: Coordinates, hours: int, now: datetime) -> SourceResult[Forecast]: ...


class MessageComposer(Protocol):
    def compose(self, request: MessageRequest) -> AlertMessage: ...


class ContactRepository(Protocol):
    def list_active(self, channel: str) -> tuple[Contact, ...]: ...


class Notifier(Protocol):  # D3 — two methods, not one union method
    def send_alert(self, message: AlertMessage, recipients: tuple[Contact, ...]) -> None: ...
    def send_operator_notice(self, notice: OperatorNotice) -> None: ...


class AlertRepository(Protocol):
    # --- community-alert dedup ---
    def alerts_with_window_start_between(
        self, city_slug: str, earliest_start: datetime, latest_start: datetime
    ) -> tuple[AlertRecord, ...]: ...  # ascending by window start

    def record_alert(self, record: AlertRecord) -> None: ...

    # --- outage-notice dedup state ---
    def get_active_outage(self, city_slug: str) -> OutageRecord | None: ...
    def save_active_outage(self, record: OutageRecord) -> None: ...  # upsert
    def clear_active_outage(self, city_slug: str) -> None: ...
