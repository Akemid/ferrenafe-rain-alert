"""The eight ports this application talks to the world through (design.md
section 4, *contract*).

Seven of them are `RunAlertCycle`'s and are bundled into `CycleDependencies`.
The eighth, `SnapshotPublisher`, is the entry point's: `CycleResult` already
carries everything the public page needs and is already returned, so the use
case does not need to know a web page exists.

`typing.Protocol`, structural typing — fakes in `tests/support/fakes.py` do
not subclass these. `@runtime_checkable` is deliberately not used: it only
checks method *names*, giving false confidence. Conformance is guarded
statically by `mypy --strict` (D9).
"""

from datetime import datetime
from typing import Any, Protocol

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
    `WarningSummary.title`. It excludes code-owned configuration (`city`,
    `timezone`) and the evaluator's own numbers. **`checklist` was excluded
    here too until change 3's `SsmConfigRepository` made it operator-editable
    runtime input with no code review** — it now gets the same sanitization
    every other free-text field on this list gets
    (`adapters/agent_prompt.py::prompt_payload`,
    `domain/template.py::MessageComposer`), because "operator-owned" stopped
    meaning "reviewed like any other line of source" the moment it became an
    SSM parameter.

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
    """**Blocking condition on the first implementation that actually delivers.**

    `ConsoleNotifier` is the only implementation that exists, deliberately, and
    that is what makes the gap below survivable today. Writing a `Notifier`
    that reaches a resident changes its severity, and nothing else in this
    codebase will say so at the moment it happens — which is why it is written
    here, on the port, rather than in a document.

    The scheduled cycle's Lambda role holds `dynamodb:PutItem` on the alerts
    table, narrowed by action but **not by item**. An attacker with code
    execution in the function — the realistic path being a parser fault over
    the scraped SENAMHI HTML, or a compromised transitive dependency — writes
    one `AlertRecord` for the city's partition at `imminent`, with a window
    start inside `dedup_lookback_hours`. Every later cycle then reads it as
    prior state, `should_send` returns "not an escalation over prior level
    imminent", and nothing is sent for as long as the lookback runs. One write
    buys seventy-two hours, it outlives the attacker's access, and the three
    CloudWatch alarms stay green throughout: no error, invocations normal,
    snapshot published.

    Today the cost is a suppressed record and a suppressed operator notice.
    The public page keeps showing the *assessment's* level, not the sent
    alert's, so a resident still sees the risk. With a delivering notifier the
    cost becomes a warning that never arrives.

    **It cannot be closed by narrowing IAM** — the attacker writes the
    partition the function legitimately owns. It is not closed by a CloudWatch
    metric filter either: a suppression at an elevated level is exactly what a
    healthy multi-cycle rain event produces, so a filter matching it pages on
    ordinary deduplication and gets muted. That was tried, reviewed, and
    discarded on 2026-10-01 for that reason — see
    `docs/blog/2026-10-01-the-alarm-that-could-not-tell-an-attack-from-the-weather.md`.

    What it needs is provenance: a way to tell a record this system wrote from
    one that appeared beside it. That is an integrity mechanism on the table,
    not a log field — the attacker chooses a plausible `sent_at`.
    """

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


class SnapshotPublisher(Protocol):
    """Where one cycle's public snapshot goes.

    Called from the entry point, not from `RunAlertCycle`: `CycleResult`
    already carries everything and is already returned, so the use case does
    not need to know a web page exists.
    """

    def publish(self, document: dict[str, Any]) -> None: ...
