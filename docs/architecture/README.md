# Rain alert core: what every part of the code does

This is the entry point for understanding the code in `src/rain_alert/`. It explains what the system decides, the seven steps it runs to decide it, the four layers the code is split into, and what each of the 50 source files is for. Read it on its own if you want the whole picture in a few minutes. Follow the links at the end if you need a specific layer in depth.

## What the system decides

The system answers one question on every run. Should the community of Ferreñafe be told that rain risk is elevated right now?

It answers by combining two independent signals.

| Signal | Source | What it says |
|---|---|---|
| Official warning | SENAMHI Lambayeque warnings page, scraped (in the deployed cycle, read from an S3 copy pushed from outside AWS, because SENAMHI does not answer AWS; see [ADR 0003](../decisions/0003-senamhi-s3-relay.md)) | The region is under an official aviso, at yellow, orange or red |
| Local forecast | Open-Meteo API for the configured coordinates | Millimetres of rain and hourly probability expected at Ferreñafe |

Neither signal is enough alone. The official aviso covers a whole department, highlands included, and it is multi-hazard. The forecast is a global model, and global models are imprecise on the Peruvian coast. Combining them produces a verdict with judgment, which is the point of the whole design.

The verdict is one of three levels defined in `Level`.

| Level | Meaning |
|---|---|
| `none` | Nothing qualifies. No community message. |
| `prepare` | Elevated risk with lead time. Preparation is warranted. |
| `imminent` | Flooding rain is expected inside the near horizon. |

## What is deployed, and what is not

The cycle runs on AWS in `us-east-2` today, on a schedule, and it composes its message with a language model. It does not deliver that message to anyone. Read this before the rest, because the older design documents describe a different system in places.

| Piece | Where it is defined | What it is |
|---|---|---|
| `ferrenafe-scheduled-cycle` stack | `infra/lib/scheduled-cycle-stack.ts` | An EventBridge Scheduler cron at 00, 06, 12 and 18 in `America/Lima`, invoking the Lambda `ferrenafe-rain-alert-cycle` (Python 3.12, ARM64). It also owns the DynamoDB table `ferrenafe-alerts-sent`, the SENAMHI relay bucket, a CloudTrail trail on that bucket, an SNS alarms topic and the CloudWatch alarms. |
| `ferrenafe-public-snapshot` stack | `infra/lib/public-snapshot-stack.ts` | A public S3 bucket holding one object, `status.json`, plus the `SnapshotWriter` policy that lets the cycle replace it. |
| AgentCore stack | `agentcore/`, code in `agent/` | The AgentCore Runtime `rainalert`: a Strands agent on Bedrock Claude Haiku 4.5 (`agent/app.py`), which only drafts message text. |
| Relay producer | `ops/launchd/pe.ferrenafe.relay-push.plist`, `src/rain_alert/entrypoints/relay_push.py` | A macOS LaunchAgent outside AWS that runs `rain-alert-relay-push` every hour, because SENAMHI does not answer AWS. |
| Public page | `web/`, `amplify.yml` | An Astro page hosted on Amplify that reads `status.json`. |

Four things hold the deployed shape together.

- **The relay.** The Lambda never fetches SENAMHI. The producer puts the page into the relay bucket, and `S3RelayReader` and `RelayWarningProvider` read it back. An object that is 3 hours old or more is refused as `STALE_RELAY`, and the cycle degrades to forecast-only. See [ADR 0003](../decisions/0003-senamhi-s3-relay.md) and `docs/evidence/2026-10-03-senamhi-relay-live.md`. `infra/cdk.json` sets `scheduleEnabled` and `relayEnabled` to `"true"`.
- **Configuration.** Seven SSM parameters under `/ferrenafe/rain-alert/` are read by `SsmConfigRepository`. They are seeded by hand (`docs/runbooks/seed-ssm-parameters.md`), not created by the CDK. The `composer` parameter switches between the agent and the template.
- **The agent only writes words.** The risk level is decided by `RiskEvaluator` before the agent is called. `AgentBackedComposer` computes the template message first, validates the agent's draft with `validate_message`, and sends the template instead whenever the draft is rejected or the call fails.
- **No delivery.** The only notifier is `ConsoleNotifier`, which writes to the Lambda's log and cannot transmit. Nothing reaches a resident by message. The public channel is the page above, which shows the last published snapshot.

The design documents under `docs/superpowers/` and the planning artifacts under `openspec/` describe the planned system. Where they and the code disagree, the code is what runs, and this guide follows the code.

## The cycle, in seven steps

`RunAlertCycle.execute` in `src/rain_alert/application/run_alert_cycle.py` runs the whole thing. These are its steps in the order the code performs them.

1. **Load configuration.** `ConfigRepository.load` returns one `AlertConfig`: city, coordinates, timezone, region, thresholds, checklist, horizons.
2. **Fetch warnings.** `WarningProvider.fetch_current_warnings` returns a `SourceResult` that is either the warnings in force or a declared outage.
3. **Fetch forecast.** `ForecastProvider.fetch_forecast` returns a `SourceResult` that is either a `Forecast` or a declared outage.
4. **Evaluate outage.** `evaluate_outage` compares which sources are down now against the outage record persisted from the last run. It returns operator notices to send and the next state to store. Notices are sent, and state is written only when it changed.
5. **Evaluate risk.** `RiskEvaluator.evaluate` walks five ordered branches, most severe first, and returns a `RiskAssessment` carrying the level, the affected window, and structured reasons.
6. **Apply the dedup policy.** `AlertPolicy.decide` queries prior sent alerts for the overlapping window and delegates to the pure `should_send` rule.
7. **Compose, notify, record.** Only if step 6 authorised a send, and step 4 did not suppress community alerts. The composer produces the Spanish message (the template, or the agent's draft once it passes validation), the notifier receives it, and an `AlertRecord` is written so step 6 of the next run can see it.

Every run returns a `CycleResult`, whether or not anything was sent. The CLI renders that result, and `run_once` in `src/rain_alert/entrypoints/run_once.py` (shared by the CLI and the Lambda) publishes the public snapshot after the cycle, where a failed publish is logged and does not fail the run. `CycleResult.message_request` is always populated even on a non-send, which is what lets the CLI print a message preview without invoking any composer.

The system diagram in `docs/rain-alert-core-architecture.drawio` is the original target topology from the approved design, drawn before the deployment existed. It is not a picture of what is deployed: the table above is. The seven numbered pipeline steps on that diagram are the design specification's numbering, which differs slightly from the code's: the code inserts outage evaluation as step 4.

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
| Ports | `src/rain_alert/ports/` | Eight `Protocol` classes: seven describing what the use case needs, plus `SnapshotPublisher` for the entry point | `typing`, `collections.abc`, `datetime`, `rain_alert.domain` |
| Application | `src/rain_alert/application/` | The use case, the dependency bundle, the policy shell | Ports and domain |
| Adapters | `src/rain_alert/adapters/` | HTTP, HTML scraping, the S3 relay, the agent, DynamoDB, SSM, S3 publishing, console output, local JSON persistence | Anything |
| Entrypoints | `src/rain_alert/entrypoints/` | The CLI, the Lambda handler, the relay producer and the object graphs they build | Anything |

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
| `snapshot.py` | `build_snapshot`, the pure document the public page reads, including its Spanish labels. |
| `sanitize.py` | `sanitize_source_text`, the one sanitizer every source-derived free text passes through before it is rendered. |
| `message_validation.py` | `validate_message`, the rules a drafted message must satisfy before it is sent. |
| `messages.py` | `MessageRequest`, `AlertMessage`, `AlertRecord`, `OutageRecord`, `OperatorNotice` and the two summary types the composer receives. |
| `template.py` | The deterministic `MessageComposer`, which renders the same structured reasons into neutral Spanish with local times. |

### Ports, `src/rain_alert/ports/`

| File | What it is for |
|---|---|
| `__init__.py` | The eight `Protocol` classes this application talks to the world through, and nothing else. Seven are the use case's; `SnapshotPublisher` is the entry point's. |

### Application, `src/rain_alert/application/`

| File | What it is for |
|---|---|
| `__init__.py` | Package marker. |
| `dependencies.py` | `CycleDependencies`, the frozen bundle of the seven ports `RunAlertCycle` needs, plus the evaluator factory and the clock. |
| `policies.py` | `AlertPolicy`, the port-bound shell that queries the repository and delegates to `should_send`. |
| `run_alert_cycle.py` | `RunAlertCycle`, the use case that runs the seven steps, plus the `CycleResult` it returns. |

### Adapters, `src/rain_alert/adapters/`

| File | What it is for |
|---|---|
| `__init__.py` | Package marker. |
| `http.py` | The shared fetch seam: `FetchError`, `fetch_text`, `HtmlFetcher` and `HttpxHtmlFetcher`, with no `httpx` exception allowed to escape. |
| `open_meteo.py` | The forecast adapter: the verified query, the pure `parse_forecast_payload`, and `OpenMeteoForecastProvider`. |
| `senamhi_scraper.py` | The warnings scraper: header-signature table location, in-force detection, relevance filtering, and loud failure. |
| `senamhi_relay.py` | `RelayWarningProvider`: reads the SENAMHI page from one S3 object, refuses a stale one, and parses it with the scraper's parser. |
| `s3_relay_reader.py` | `S3RelayReader` and `UnconfiguredRelayReader`: fetch and validate that object, and name every failure. |
| `agent_composer.py` | `AgentBackedComposer`: asks the agent for a draft, validates it, and falls back to the template. |
| `agent_invoker.py` | The `AgentInvoker` seam, so the offline suite needs no model. |
| `agentcore_invoker.py` | `BedrockAgentCoreInvoker`, the real invoker of the AgentCore Runtime. |
| `agent_prompt.py` | `build_prompt`, the pure function that turns a `MessageRequest` into the agent's prompt. |
| `dynamodb_alert_repository.py` | `DynamoDbAlertRepository`, the `AlertRepository` over `ferrenafe-alerts-sent`. |
| `ssm_config_repository.py` | `SsmConfigRepository`, the `ConfigRepository` over the seven SSM parameters. |
| `s3_snapshot_publisher.py` | `S3SnapshotPublisher`, which writes `status.json` to the public bucket. |
| `senamhi_classification.py` | The SENAMHI Spanish title vocabulary translated into domain `Phenomenon` and `Zone` terms, by word-boundary regular expressions over normalized text. |
| `console_notifier.py` | `ConsoleNotifier`, which prints community alerts and operator notices under distinct prefixes and cannot transmit. |
| `serialization.py` | The persisted document shape for alerts, outages and reasons, which `DynamoDbAlertRepository` stores unchanged. |

### Local adapters, `src/rain_alert/adapters/local/`

| File | What it is for |
|---|---|
| `__init__.py` | Package marker stating these are shipped implementations, not test doubles. |
| `static_config_repository.py` | `StaticConfigRepository`: the calibrated thresholds, the Spanish checklist, and the sourced city-centre coordinates with their strict environment override. |
| `static_contact_repository.py` | `StaticContactRepository`: one synthetic local contact whose handle is literally `stdout`, carrying no personal data. |
| `in_memory_alert_repository.py` | `InMemoryAlertRepository`: the whole key-range query rule, with no I/O. |
| `json_alert_repository.py` | `JsonFileAlertRepository`: the same store persisted atomically to one gitignored JSON file, so separate process invocations share state. |
| `json_file_snapshot_publisher.py` | `JsonFileSnapshotPublisher`: the same snapshot written to a local file instead of S3. |
| `offline_sources.py` | `FileHtmlFetcher` and `OfflineOpenMeteoProvider`, which swap the fetch leaf only so the real parsers still do the work. |

### Entrypoints, `src/rain_alert/entrypoints/`

| File | What it is for |
|---|---|
| `__init__.py` | Package marker. |
| `wiring.py` | `build_local_deps` and `build_cloud_deps`, the real object graphs, plus the composer, warning-provider and snapshot-publisher selection. Both can only construct `ConsoleNotifier`. |
| `run_once.py` | `run_once`, the sequence the CLI and the Lambda share: load configuration, run the cycle, publish the snapshot. |
| `lambda_handler.py` | `handler`, the Lambda entry point the schedule invokes. |
| `relay_push.py` | The `rain-alert-relay-push` command: fetches the SENAMHI page outside AWS and replaces the relay object. |
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

At the time of writing, `uv run pytest` reports 1351 passed and 15 deselected. The deselected tests are the live-network canaries, which are opt-in by the `addopts = "-q -m 'not integration'"` setting in `pyproject.toml`.

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

Ferreñafe  (-6.6360, -79.7899)
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

The **coordinates are printed on every run**, because the point a forecast was fetched for is part of reading it. They are the sourced centre of the city, `6°38'10"S 79°47'23"W`, cited to two public references in `static_config_repository.py` and pinned by a test. Each run also prints how the point was obtained, `[public_reference]` for the default. They used to carry a `(PLACEHOLDER — pending confirmation)` marker instead, while the value was an unsourced guess; that marker and the boolean behind it are retired, and provenance came back as an enum after a security review argued that leaving no visible signal was the wrong trade here. See [adapters.md](./adapters.md).

The **reasons are English and the message body is Spanish**, in the same output. Those are two audiences. The `Reasons` block is the operator's audit trail with the numbers in it. The message body is what a community member would read.

The **message is printed even when nothing is sent**, labelled `PREVIEW (NOT SENT)` in that case. That is how calibration reads the real text without a send.

## Where to go next

| Document | Covers |
|---|---|
| [domain.md](./domain.md) | Every module in `src/rain_alert/domain/`: the types, the risk branches, the hazard filter, the dedup and outage rules, the Spanish template. |
| [ports-and-application.md](./ports-and-application.md) | The eight `Protocol` classes, `CycleDependencies`, `AlertPolicy` and `RunAlertCycle`. |
| [adapters.md](./adapters.md) | The HTTP seam, the Open-Meteo client, the SENAMHI scraper, classifier and relay, the console notifier, serialization, and everything under `local/`. |
| [entrypoints-and-testing.md](./entrypoints-and-testing.md) | The CLI, the relay producer, the object graph and warning-provider selection, and the test architecture including the architecture boundary test and the live canaries. |

Background reading, subordinate to the code in every case where they disagree:

- `docs/superpowers/specs/2026-09-03-rain-alert-core-design.md`, the approved design.
- `openspec/changes/core-alert-cycle/design.md`, the technical design with its numbered decisions `D1` through `D12`.
- `openspec/changes/core-alert-cycle/specs/*/spec.md`, the seven requirement specifications.
- `docs/rain-alert-core-architecture.drawio`, the system diagram.
