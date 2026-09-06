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
    """The only port through which recipient-facing text is produced.

    That makes it the chokepoint for one invariant every implementation owes,
    including change 2's agent-backed composer:

    **Every source-derived free text that reaches `AlertMessage.title` or
    `AlertMessage.body` must pass through
    `rain_alert.domain.sanitize.sanitize_source_text` first.**

    Source-derived means "the system did not write it": today that is the
    scraped SENAMHI aviso title, carried on `WarningReason.title` and
    `WarningSummary.title`. It excludes operator-owned configuration
    (`city`, `timezone`, `checklist`) and the evaluator's own numbers.

    The reason is not theoretical. `AlertMessage.body` is a line-oriented
    format whose grammar is `- ` bullets and `Motivos:` / `Recomendaciones:`
    headers. A title containing newlines was shown to produce a body with two
    `Recomendaciones:` sections, the forged one first and formatted
    identically to the genuine one, carrying a live ANSI escape and a
    right-to-left override. A person deciding what to do in a flood cannot
    tell the two apart.

    An agent-backed composer does not weaken this and does not replace it: a
    model asked to quote a title will quote it verbatim, newlines included,
    and prompt text arriving from a scraped page is exactly the input a
    composer must not be handed unfiltered.
    """

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
