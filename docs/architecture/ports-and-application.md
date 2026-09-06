# Ports and the application layer

This document covers `src/rain_alert/ports/__init__.py` and the three modules in `src/rain_alert/application/`. Together they are the contract between the rules and the outside world, and the orchestration that runs one cycle. Read it if you are adding a dependency to the use case, changing the order of the cycle, or checking the claim that the message composer holds no decision authority. Read [README.md](./README.md) first for the seven steps in outline.

## The seven ports

All seven live in one file, `src/rain_alert/ports/__init__.py`. They are `typing.Protocol` classes, which means structural typing: an implementation conforms by having the right methods, not by inheriting.

| Port | Method or methods | What it abstracts |
|---|---|---|
| `ConfigRepository` | `load() -> AlertConfig` | Where configuration comes from. |
| `WarningProvider` | `fetch_current_warnings(region, now)` | The official SENAMHI source. |
| `ForecastProvider` | `fetch_forecast(location, hours, now)` | The Open-Meteo forecast. |
| `MessageComposer` | `compose(request) -> AlertMessage` | Turning a decided verdict into recipient text. |
| `ContactRepository` | `list_active(channel)` | Who receives community alerts. |
| `Notifier` | `send_alert(...)` and `send_operator_notice(...)` | Delivery, of two different kinds. |
| `AlertRepository` | five methods, listed below | Durable state for both dedup rules. |

### Three decisions worth knowing

**`@runtime_checkable` is deliberately not used.** It only checks method names, which gives false confidence: a fake with a matching name and the wrong signature would pass. Conformance is guarded statically by `mypy --strict` instead. This is decision `D9`.

**Fakes do not subclass these protocols.** The recording spies in `tests/support/fakes.py` declare no base class. Structural typing means an accidental signature change in a port shows up as a type error at the wiring site, which is exactly the drift the tests exist to catch.

**`MessageComposer` carries a sanitization invariant, stated on the port.** It is the only port through which recipient-facing text is produced, which makes it the chokepoint: every source-derived free text reaching `AlertMessage.title` or `AlertMessage.body` must pass through `domain.sanitize.sanitize_source_text` first.

"Source-derived" means the system did not write it. Today that is the scraped SENAMHI aviso title, carried on `WarningReason.title` and `WarningSummary.title`. It excludes operator-owned configuration — `city`, `timezone`, `checklist` — and the evaluator's own numbers.

The invariant lives on the port rather than only in `template.py` so that change 2's agent-backed composer inherits it rather than rediscovering it. An agent does not weaken the requirement and does not replace it: a model asked to quote a title will quote it verbatim, newlines included, and text arriving from a scraped page is precisely the input a composer must not be handed unfiltered. The reproduced defect and the four rendering boundaries are described in [domain.md](./domain.md).

**`Notifier` has two methods, not one method taking a union.** This is decision `D3`. A community alert and an operator notice serve different audiences, are formatted differently, and in change 3 will travel over different channels. Collapsing them into one method would have pushed a type check into every implementation.

### The ports package imports almost nothing

The boundary test `test_ports_package_imports_only_typing_and_domain` allows exactly four import roots: `typing`, `collections.abc`, `datetime` and `rain_alert.domain`. The design document described that list as `typing`, `collections.abc` and `rain_alert.domain`. The test's own comment records why `datetime` was added: every port signature carrying a `now` or a timestamp needs it, and it is a standard library type with no I/O or third-party surface, so it was added rather than reopened as a design question.

### `AlertRepository` carries two collections

```python
def alerts_with_window_start_between(city_slug, earliest_start, latest_start) -> tuple[AlertRecord, ...]
def record_alert(record) -> None
def get_active_outage(city_slug) -> OutageRecord | None
def save_active_outage(record) -> None
def clear_active_outage(city_slug) -> None
```

The first pair is community-alert dedup. The second group is outage-notice dedup state.

The query is deliberately dumb. It is a key-range read that returns records ascending by window start, and nothing more. Overlap and escalation are rules, so they belong in `AlertPolicy` and `should_send`, not in the store. That keeps the store trivially checkable here, and it is also the cheapest DynamoDB access pattern for change 3.

`save_active_outage` is an upsert. At most one active outage exists per city.

## `application/dependencies.py`

`CycleDependencies` is a frozen dataclass bundling all seven ports plus two more things.

| Field | Type |
|---|---|
| `config` | `ConfigRepository` |
| `warnings` | `WarningProvider` |
| `forecast` | `ForecastProvider` |
| `composer` | `MessageComposer` |
| `contacts` | `ContactRepository` |
| `notifier` | `Notifier` |
| `alerts` | `AlertRepository` |
| `evaluator_factory` | `Callable[[RiskThresholds], RiskEvaluator]` |
| `now` | `Callable[[], datetime]` |

**Why a bundle.** Seven ports plus an evaluator, a policy and a clock would give `RunAlertCycle` ten constructor parameters. The bundle keeps constructor injection while making both CLI and test wiring a single object. This is decision `D6`.

**Why an evaluator factory rather than an evaluator.** `RiskEvaluator` is constructed from the thresholds inside `AlertConfig`, and the config is not known until step 1 of the cycle has run. The factory defers construction to the moment the thresholds are in hand. In practice the factory passed in is the `RiskEvaluator` class itself.

**Why a clock callable.** This is decision `D7`. `datetime.now()` is never called directly inside the cycle. The CLI passes either the system clock or the instant given by `--now`, which is what makes a run reproducible against pinned fixtures.

The payoff of `CycleDependencies` is stated in its own docstring and is worth repeating. `tests.support.wiring.build_fake_deps()` and `entrypoints.wiring.build_local_deps()` return the same type. **The object graph the CLI runs is the object graph the tests exercise.** Only the leaves differ.

## `application/policies.py`

`AlertPolicy` is the port-bound shell over the pure `should_send` rule. It holds an `AlertRepository` and a lookback in hours. It holds no state of its own.

```python
def decide(self, city_slug: str, assessment: RiskAssessment, now: datetime) -> SendDecision:
    lookback_start = now - timedelta(hours=self._lookback_hours)
    earliest_start = min(lookback_start, assessment.window.start)
    prior = self._alerts.alerts_with_window_start_between(city_slug, earliest_start, assessment.window.end)
    return should_send(assessment, prior)
```

**Why this class exists at all.** The design document put this code under a `# domain/dedup.py` header, but `AlertPolicy` takes an `AlertRepository`, which is a port. Importing a port from `domain/` fails the architecture boundary test, even under `if TYPE_CHECKING:`. So the pure rule stayed in `domain/dedup.py` and this shell, which only fetches state and delegates, moved to `application/`.

**Why the range start is clamped.** The query start is `min(now - lookback, assessment.window.start)`, not simply `now - lookback`.

An imminent window is the union of the SENAMHI warning windows that matched. A long-duration aviso can begin well before the lookback starts. Without the clamp, the record for the alert already sent for that window would fall outside the query range, `should_send` would see no overlapping prior, and the community would be re-alerted on every single cycle for the rest of the aviso.

Clamping only ever widens the range, so the lookback still governs short windows exactly as before.

## `application/run_alert_cycle.py`

`RunAlertCycle.execute()` runs the cycle and returns a `CycleResult`. It is the only place in the codebase where the order of operations is decided.

### The result

`CycleResult` is what the CLI, and change 3's Lambda entrypoint, need in order to report on a cycle whether or not a send happened.

| Field | Notes |
|---|---|
| `config_city` | The city name from configuration. |
| `senamhi` | The raw `SourceResult`, so the caller can read notes and outage detail. |
| `open_meteo` | Likewise. |
| `assessment` | The `RiskAssessment`. |
| `decision` | The `SendDecision` from the policy. |
| `message_request` | **Always populated**, even on a non-send. |
| `message` | The composed `AlertMessage`, or `None` when nothing was composed. |
| `sent` | Whether a community alert was handed to the notifier. |
| `recipients_count` | How many contacts it would have reached. |
| `notices` | The operator notices emitted this cycle. |

`message_request` being always populated is decision `D11`. It is what lets the CLI render a preview of exactly what would have gone out, without invoking any composer. On a deduplicated cycle that preview costs nothing, which matters once the composer is a paid model call.

### The steps, and the details that are easy to miss

```python
now = deps.now()
config = deps.config.load()

warnings = deps.warnings.fetch_current_warnings(config.region, now)
forecast = deps.forecast.fetch_forecast(config.coordinates, config.forecast_hours, now)
```

The same `now` is threaded through every call. Nothing reads the clock twice.

```python
active_outage = deps.alerts.get_active_outage(config.city_slug)
outage_decision = evaluate_outage(warnings, forecast, active_outage, config.city_slug, now)
for notice in outage_decision.notices:
    deps.notifier.send_operator_notice(notice)
if outage_decision.state_changed:
    ...
```

**The state write is gated on `state_changed`, not on `next_state is None`.** A healthy cycle and an unchanged ongoing outage both need zero writes. In change 3 each write is a DynamoDB call every six hours, so the distinction is a real cost. When state did change, a `None` next state clears the record and anything else upserts it.

```python
evaluator = deps.evaluator_factory(config.thresholds)
assessment = evaluator.evaluate(warnings, forecast, now)

policy = AlertPolicy(deps.alerts, config.dedup_lookback_hours)
decision = policy.decide(config.city_slug, assessment, now)

message_request = _build_message_request(config, assessment, warnings, forecast)
```

Note the order. The assessment is computed, then the send decision, and only then the message request. The request is built from the assessment and the sources, never the other way round.

```python
if decision.send and not outage_decision.suppress_community_alert:
    message = deps.composer.compose(message_request)
    recipients = deps.contacts.list_active(config.active_channel)
    deps.notifier.send_alert(message, recipients)
    record = AlertRecord(..., composer="template")
    deps.alerts.record_alert(record)
```

**The record is written after the notification, not before.** If the notifier raises, nothing is recorded, and the next cycle will try again. Recording first would mark an undelivered alert as sent.

The record is written with `composer="template"` unconditionally, because the deterministic template is the only composer that exists in this change.

### How the structure enforces that the composer decides nothing

This is the claim worth verifying against the code rather than taking on trust, because change 2 replaces the composer with a language model.

Three properties, all visible in the snippet above.

1. **The composer is called inside the `if`, not before it.** `decision.send` was produced by `AlertPolicy`, which read the assessment and the repository. If the send is not authorised, `compose` is never called at all.
2. **Its input carries an already-decided level.** `MessageRequest.level` comes from `assessment.level`. There is no field on `MessageRequest` that would let the composer influence a level, a window or a threshold.
3. **Its output is never read by a decision.** `message` is passed to `deps.notifier.send_alert`, stored on the `AlertRecord`, and attached to `CycleResult`. No branch anywhere reads it.

That is a property of the call graph. It survives any change to the composer's implementation, which is the whole point of putting it behind a port. This change ships only the deterministic template, so today there is nothing for a model to do even if one existed.

### `_build_message_request`

Builds the composer's input from the config, the assessment and the two source results.

The forecast summary is built only when the forecast is `Available`. The horizons are clamped through `evaluated_horizon` against what the forecast actually covers, so a short series summarises truthfully instead of raising. `mm_24h` uses `IMMINENT_HORIZON_HOURS` from the domain. `mm_48h` uses `config.forecast_hours`. The peak is taken over the full clamped horizon.

The warning summary is built only when warnings are `Available` and there is at least one. `_most_severe_warning` picks the highest by `_WARNING_SEVERITY_ORDER`, which ranks yellow, orange and red as 1, 2 and 3. This is a separate ordering from `LEVEL_RANK` in the domain, because it ranks `WarningLevel`, not `Level`.

`checklist`, `timezone` and `city` come straight from the configuration.

## Where to go next

- [domain.md](./domain.md) for the rules these ports feed and the types they move.
- [adapters.md](./adapters.md) for the concrete implementations of all seven ports.
- [entrypoints-and-testing.md](./entrypoints-and-testing.md) for `build_local_deps`, `build_fake_deps`, and the tests that assert call order.
- [README.md](./README.md) for the cycle in outline.
