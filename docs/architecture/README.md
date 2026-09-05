# Rain alert core: what every part of the code does

This is the entry point for understanding the code in `src/rain_alert/`. It explains what the system decides, the seven steps it runs to decide it, the four layers the code is split into, and what each of the 34 source files is for. Read it on its own if you want the whole picture in a few minutes. Follow the links at the end if you need a specific layer in depth.

## What the system decides

The system answers one question on every run. Should the community of Ferreñafe be told that rain risk is elevated right now?

It answers by combining two independent signals.

| Signal | Source | What it says |
|---|---|---|
| Official warning | SENAMHI Lambayeque warnings page, scraped | The region is under an official aviso, at yellow, orange or red |
| Local forecast | Open-Meteo API for the configured coordinates | Millimetres of rain and hourly probability expected at Ferreñafe |

Neither signal is enough alone. The official aviso covers a whole department, highlands included, and it is multi-hazard. The forecast is a global model, and global models are imprecise on the Peruvian coast. Combining them produces a verdict with judgment, which is the point of the whole design.

The verdict is one of three levels defined in `Level`.

| Level | Meaning |
|---|---|
| `none` | Nothing qualifies. No community message. |
| `prepare` | Elevated risk with lead time. Preparation is warranted. |
| `imminent` | Flooding rain is expected inside the near horizon. |

## What this code does not do yet

State this plainly before reading further, because the design documents describe a larger system than the one that exists.

- **No cloud.** There is no Lambda, no DynamoDB, no Parameter Store, no EventBridge. Storage is a local JSON file.
- **No scheduler.** Nothing runs the cycle on a timer. A person runs it from a terminal.
- **No language model.** The message composer in this change is a deterministic template. No model is called anywhere.
- **No delivery.** Nothing is transmitted to any recipient. The only notifier that exists prints to a stream.

Those arrive in changes 2 and 3 of the `core-alert-cycle` rollout. Everything in `src/` today is the decision engine and the calibration harness around it.

Note that `README.md` at the repository root has not caught up with this branch. It says no decision logic, HTTP or HTML adapters, or CLI exist yet. All four exist, and this document describes them.

## The cycle, in seven steps

`RunAlertCycle.execute` in `src/rain_alert/application/run_alert_cycle.py` runs the whole thing. These are its steps in the order the code performs them.

1. **Load configuration.** `ConfigRepository.load` returns one `AlertConfig`: city, coordinates, timezone, region, thresholds, checklist, horizons.
2. **Fetch warnings.** `WarningProvider.fetch_current_warnings` returns a `SourceResult` that is either the warnings in force or a declared outage.
3. **Fetch forecast.** `ForecastProvider.fetch_forecast` returns a `SourceResult` that is either a `Forecast` or a declared outage.
4. **Evaluate outage.** `evaluate_outage` compares which sources are down now against the outage record persisted from the last run. It returns operator notices to send and the next state to store. Notices are sent, and state is written only when it changed.
5. **Evaluate risk.** `RiskEvaluator.evaluate` walks five ordered branches, most severe first, and returns a `RiskAssessment` carrying the level, the affected window, and structured reasons.
6. **Apply the dedup policy.** `AlertPolicy.decide` queries prior sent alerts for the overlapping window and delegates to the pure `should_send` rule.
7. **Compose, notify, record.** Only if step 6 authorised a send, and step 4 did not suppress community alerts. The composer produces the Spanish message, the notifier receives it, and an `AlertRecord` is written so step 6 of the next run can see it.

Every run returns a `CycleResult`, whether or not anything was sent. The CLI renders that result. `CycleResult.message_request` is always populated even on a non-send, which is what lets the CLI print a message preview without invoking any composer.

The system diagram in `docs/rain-alert-core-architecture.drawio` shows the target cloud topology from the approved design. It is the shape the code is preparing for, not the shape it runs in today. The seven numbered pipeline steps on that diagram are the design specification's numbering, which differs slightly from the code's: the code inserts outage evaluation as step 4, and message validation is not part of this change.

## The layers, and why they are separated

The package is hexagonal: four layers, plus a composition root that assembles them. Dependencies point inward only.

```
entrypoints  ->  application  ->  ports  ->  domain
     |                                          ^
     +--------->  adapters  --------------------+
```

| Layer | Package | Contains | May import |
|---|---|---|---|
| Domain | `src/rain_alert/domain/` | Types, rules, the evaluator, the template | Standard library and other domain modules only |
| Ports | `src/rain_alert/ports/` | Seven `Protocol` classes describing what the use case needs | `typing`, `collections.abc`, `datetime`, `rain_alert.domain` |
| Application | `src/rain_alert/application/` | The use case, the dependency bundle, the policy shell | Ports and domain |
| Adapters | `src/rain_alert/adapters/` | HTTP, HTML scraping, JSON persistence, console output | Anything |
| Entrypoints | `src/rain_alert/entrypoints/` | The CLI and the object graph it builds | Anything |

The separation buys three things a reader should look for.

**The rules are testable in milliseconds.** The domain has no network, no filesystem and no third-party imports, so the entire risk-evaluation suite runs without touching anything outside the process.

**The fragile part is quarantined.** Scraping a public HTML page is the most breakable code in the system. It lives behind `WarningProvider`, so when it breaks, the cycle degrades instead of stopping.

**The boundary is enforced, not requested.** `tests/architecture/test_layer_boundaries.py` parses every domain and ports module with `ast` and fails the build on a forbidden import. It catches imports nested inside function bodies and inside `if TYPE_CHECKING:` blocks, and it resolves relative imports to absolute names first, so `from ..adapters import x` inside the domain is caught.

## The five things a newcomer gets wrong

These are the decisions that are not visible from the code shape alone. Each one is explained fully in a linked document.

**The language model never decides whether to alert.** Read step 7 above again. The composer is invoked after `AlertPolicy` has already authorised the send, it receives a `MessageRequest` whose `level` is already fixed, and its return value is never read by any branch. It is a node inside a flow that code governs. That is a structural property, not a rule someone has to remember. Details in [ports-and-application.md](./ports-and-application.md).

**An unavailable source is a value, not an exception.** `SourceResult` is a tagged union of `Available[T]` and `Unavailable`. `Unavailable` has no `data` attribute at all, so unreadable data is unreadable by construction, and a `match` narrows between the two shapes. Nothing raises. Details in [domain.md](./domain.md).

**The scraper fails loudly, and a broken parse is worse than a declared outage.** Zero extracted rows means breakage, not calm, because the page has carried history since 2024. An unparseable cell on the row marked in force aborts the whole parse. Details in [adapters.md](./adapters.md).

**A heat warning must never announce flooding.** The SENAMHI warnings page is multi-hazard. Wind, heat and cold reach orange and red routinely. A filter stands between the page and the evaluator. Details in [domain.md](./domain.md) and [adapters.md](./adapters.md).

**Highlands rain is an early signal, not an imminent one.** Rain over the Río La Leche headwaters can flood Ferreñafe, but it arrives by river routing with hours of lead time. Two separate rules answer two separate questions about it. Details in [domain.md](./domain.md).

## Every source file, in one sentence

### Domain, `src/rain_alert/domain/`

| File | What it is for |
|---|---|
| `__init__.py` | Package marker declaring the domain free of adapter and third-party imports. |
| `values.py` | Enums and range-checked value objects: `Level`, `WarningLevel`, `SourceName`, `NoticeKind`, `UnavailableReason`, `Coordinates`, `TimeWindow`. |
| `sources.py` | The `SourceResult` tagged union: `Available[T]` with data and operator notes, or `Unavailable` with a machine-readable reason. |
| `hazards.py` | The two flood-relevance rules, `is_flood_relevant` and `may_raise_imminent`, plus the `Phenomenon` and `Zone` vocabularies they operate on. |
| `entities.py` | `Warning`, `HourlyPoint`, `Forecast`, `RiskAssessment` and `Contact`, including the forecast accessors that aggregate probability as a maximum. |
| `config.py` | `RiskThresholds` and `AlertConfig`, the injected numbers that keep every literal out of the evaluator. |
| `reasons.py` | The structured `Reason` variants and `render_reason_en`, the operator-facing English rendering with the numbers in it. |
| `risk.py` | `RiskEvaluator`, five ordered branches from most severe to least, each producing its own reasons. |
| `dedup.py` | `should_send`, the pure rule deciding whether an assessment authorises a new community send. |
| `outage.py` | `evaluate_outage`, the dual-outage state machine that returns notices and the next state as data. |
| `messages.py` | `MessageRequest`, `AlertMessage`, `AlertRecord`, `OutageRecord`, `OperatorNotice` and the two summary types the composer receives. |
| `template.py` | The deterministic `MessageComposer`, which renders the same structured reasons into neutral Spanish with local times. |

### Ports, `src/rain_alert/ports/`

| File | What it is for |
|---|---|
| `__init__.py` | The seven `Protocol` classes the use case depends on, and nothing else. |

### Application, `src/rain_alert/application/`

| File | What it is for |
|---|---|
| `__init__.py` | Package marker. |
| `dependencies.py` | `CycleDependencies`, the frozen bundle of all seven ports plus the evaluator factory and the clock. |
| `policies.py` | `AlertPolicy`, the port-bound shell that queries the repository and delegates to `should_send`. |
| `run_alert_cycle.py` | `RunAlertCycle`, the use case that runs the seven steps, plus the `CycleResult` it returns. |

### Adapters, `src/rain_alert/adapters/`

| File | What it is for |
|---|---|
| `__init__.py` | Package marker. |
| `http.py` | The shared fetch seam: `FetchError`, `fetch_text`, `HtmlFetcher` and `HttpxHtmlFetcher`, with no `httpx` exception allowed to escape. |
| `open_meteo.py` | The forecast adapter: the verified query, the pure `parse_forecast_payload`, and `OpenMeteoForecastProvider`. |
| `senamhi_scraper.py` | The warnings scraper: header-signature table location, in-force detection, relevance filtering, and loud failure. |
| `senamhi_classification.py` | The SENAMHI Spanish title vocabulary translated into domain `Phenomenon` and `Zone` terms, by word-boundary regular expressions over normalized text. |
| `console_notifier.py` | `ConsoleNotifier`, which prints community alerts and operator notices under distinct prefixes and cannot transmit. |
| `serialization.py` | The persisted document shape for alerts, outages and reasons, carried forward unchanged for change 3's DynamoDB adapter. |

### Local adapters, `src/rain_alert/adapters/local/`

| File | What it is for |
|---|---|
| `__init__.py` | Package marker stating these are shipped implementations, not test doubles. |
| `static_config_repository.py` | `StaticConfigRepository`: the calibrated thresholds, the Spanish checklist, and the placeholder coordinates with their strict environment override. |
| `static_contact_repository.py` | `StaticContactRepository`: one synthetic local contact whose handle is literally `stdout`, carrying no personal data. |
| `in_memory_alert_repository.py` | `InMemoryAlertRepository`: the whole key-range query rule, with no I/O. |
| `json_alert_repository.py` | `JsonFileAlertRepository`: the same store persisted atomically to one gitignored JSON file, so separate process invocations share state. |
| `offline_sources.py` | `FileHtmlFetcher` and `OfflineOpenMeteoProvider`, which swap the fetch leaf only so the real parsers still do the work. |

### Entrypoints, `src/rain_alert/entrypoints/`

| File | What it is for |
|---|---|
| `__init__.py` | Package marker. |
| `wiring.py` | `build_local_deps`, the real object graph, which can only construct `ConsoleNotifier`. |
| `cli.py` | The `rain-alert-cycle` command: argument parsing, the operator text rendering, and the `--json` calibration document. |

## Running it

The default test suite is offline and fast.

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

At the time of writing, `uv run pytest` reports 455 passed and 15 deselected. The deselected tests are the live-network canaries, which are opt-in by the `addopts = "-q -m 'not integration'"` setting in `pyproject.toml`.

```bash
uv run pytest -m integration
```

Run one cycle against the live sources. It sends nothing.

```bash
uv run rain-alert-cycle
```

Run one cycle with no network access at all, at a fixed instant, against a disposable state file.

```bash
uv run rain-alert-cycle \
  --offline-fixtures path/to/fixtures \
  --now 2026-09-04T12:00:00 \
  --state-file /tmp/state.json
```

The fixture directory must contain `senamhi.html` and `open_meteo.json`. The names are pinned as `SENAMHI_FIXTURE_NAME` and `OPEN_METEO_FIXTURE_NAME` in `entrypoints/wiring.py`.

## What one run looks like

This is real output, produced by the command above against the repository's own pinned fixtures. The `[DRY-RUN ALERT]` block is written by `ConsoleNotifier` during step 7. The block below it is the operator view the CLI renders afterwards.

```text
------------------------------------------------------------------------
[DRY-RUN ALERT] imminent — nothing was transmitted: this build has no notifier that can deliver a message
  would reach: 1 recipient(s)
  valid until: 2026-09-07T05:00:00+00:00
  title: Alerta de lluvias — Ferreñafe — riesgo inminente
  body:
    Ciudad: Ferreñafe
    Nivel: riesgo inminente
    Ventana: del 04/09/2026 00:00 al 07/09/2026 00:00 (hora local, America/Lima)
    Motivos:
    - Aviso oficial del SENAMHI, nivel naranja: PRECIPITACIONES EN LA COSTA NORTE Y SIERRA.
    Recomendaciones:
    - Almacena agua potable para al menos dos días.
    - Protege documentos y aparatos eléctricos por encima del nivel del piso.
    - Limpia canaletas, techos y desagües cercanos.
    - Asegura objetos sueltos y calaminas.
    - Ten a mano una linterna, un botiquín y los teléfonos de emergencia.
------------------------------------------------------------------------

Ferreñafe  (-6.6400, -79.7900)  (PLACEHOLDER — pending confirmation)
Sources    senamhi: available (fetched 2026-09-04T12:00:00+00:00)
           open_meteo: available (fetched 2026-09-04T12:00:00+00:00)
Notes      —
Level      imminent
Window     2026-09-04T05:00:00+00:00 → 2026-09-07T05:00:00+00:00
Reasons    - official SENAMHI warning: orange — PRECIPITACIONES EN LA COSTA NORTE Y SIERRA
Decision   SENT — new level for window (1 recipient(s))
Message    SENT
           title: Alerta de lluvias — Ferreñafe — riesgo inminente
           ...
Notices    0 emitted
```

Three things in that output are worth naming, because they are deliberate.

The **coordinates carry a placeholder marker** on every run. Ferreñafe's exact latitude and longitude is still an open decision, so `AlertConfig.coordinates_are_placeholder` is `True` and the CLI prints `(PLACEHOLDER — pending confirmation)` beside them. Presenting a guess as verified would put a forecast for the wrong place under the project's name.

The **reasons are English and the message body is Spanish**, in the same output. Those are two audiences. The `Reasons` block is the operator's audit trail with the numbers in it. The message body is what a community member would read.

The **message is printed even when nothing is sent**, labelled `PREVIEW (NOT SENT)` in that case. That is how calibration reads the real text without a send.

## Where to go next

| Document | Covers |
|---|---|
| [domain.md](./domain.md) | Every module in `src/rain_alert/domain/`: the types, the risk branches, the hazard filter, the dedup and outage rules, the Spanish template. |
| [ports-and-application.md](./ports-and-application.md) | The seven `Protocol` classes, `CycleDependencies`, `AlertPolicy` and `RunAlertCycle`. |
| [adapters.md](./adapters.md) | The HTTP seam, the Open-Meteo client, the SENAMHI scraper and classifier, the console notifier, serialization, and everything under `local/`. |
| [entrypoints-and-testing.md](./entrypoints-and-testing.md) | The CLI, the object graph, and the test architecture including the architecture boundary test and the live canaries. |

Background reading, subordinate to the code in every case where they disagree:

- `docs/superpowers/specs/2026-09-03-rain-alert-core-design.md`, the approved design.
- `openspec/changes/core-alert-cycle/design.md`, the technical design with its numbered decisions `D1` through `D12`.
- `openspec/changes/core-alert-cycle/specs/*/spec.md`, the seven requirement specifications.
- `docs/rain-alert-core-architecture.drawio`, the system diagram.
