# Rain Early-Warning System for Ferreñafe — Core Pipeline Design

**Date:** 2026-09-03
**Status:** Approved design, pending user review of this document
**Subproject:** 1 of 3 (alert core)
**Diagram:** `docs/rain-alert-core-architecture.drawio`

---

## 1. Context and Purpose

Ferreñafe is a city in the Lambayeque department on the northern coast of Peru. The coast is a desert: mean annual rainfall in nearby Chiclayo is on the order of 30 mm. During El Niño events, rainfall that would be unremarkable elsewhere floods streets and homes because drainage was never designed for it. The El Niño costero of 2017 caused severe flooding across Lambayeque, including Ferreñafe.

This system sends **preventive** rain alerts to a community in Ferreñafe (initially members of a local LDS ward, extensible to more people) so they can prepare: emergency backpacks, roof and drainage checks, supplies. Recipients act on the alert **before** the rain arrives.

Constraints that shape the design:

- **Lead time of hours to two days.** During a strong storm, internet and electricity are expected to fail. An alert that arrives when the rain is already falling is useless. The system warns ahead of time and does not attempt real-time nowcasting.
- **Budget near zero.** A few dollars per month at most. Serverless only, and paid components run only when an alert is actually produced.
- **Credibility.** Repeated or false alerts make people stop reading. The system must never spam and must never let a language model decide whether to alert.
- **Learning goal.** The author wants to learn Amazon Bedrock AgentCore and the Strands Agents SDK. The design gives the agent a legitimate, bounded role rather than forcing it into the control flow.

## 2. Scope

### In scope (this subproject)

The complete alert pipeline: scheduled execution, data acquisition from two sources, deterministic risk evaluation, deduplication, message composition by an agent with a deterministic fallback, validation, notification through the v1 channel (human relay), and recording of sent alerts.

### Non-goals (deferred to later subprojects or stages)

- **Subproject 2 — Admin web.** Managing contacts, monitored city, thresholds, channels, and scheduler frequency. Requires authentication (Cognito) because it handles personal data.
- **Subproject 3 — Public site.** A static page explaining how the project works.
- **Stage v2 of notification.** Automatic one-to-one WhatsApp and email delivery to recipients.
- **SMS.** Possible future adapter if budget allows.

### What the core prepares for later work

The core is built so the later subprojects add adapters rather than rewrite logic:

- `ConfigRepository` port: v1 reads from SSM Parameter Store; the admin web later writes to the same store.
- `ContactRepository` port: v1 reads from a DynamoDB table; the admin web later manages that table.
- `Notifier` port: v1 ships a human-relay adapter; v2 adds WhatsApp and email adapters without touching the domain.

## 3. Decisions and Rejected Alternatives

### 3.1 Architecture approach

Three approaches were considered:

- **A. Minimal serverless, no agent framework.** EventBridge → Lambda; on alert, a single Bedrock call drafts the message. Cheapest and simplest, but teaches little about agents.
- **B. Serverless pipeline plus a composer agent on AgentCore.** *(Chosen.)* Same deterministic control flow as A, governed by code. The message-composer node is a Strands agent on AgentCore Runtime with read-only tools, invoked only when a new alert exists. Near-zero cost because the agent runs for seconds, only on alert. Delivers the AgentCore learning goal where the agent genuinely adds value.
- **C. Entire graph in Strands on AgentCore.** Fetch and evaluation as custom nodes, composer as an agent node. Couples deterministic logic to the agent runtime and makes it harder to test in isolation.

**B is chosen.** It keeps the alert decision deterministic and testable while giving the agent a real, bounded job.

### 3.2 The LLM never decides whether to alert

An earlier idea was to expose the deterministic rules as a tool the agent calls. This was rejected because it inverts control: the agent would still decide when to call the tool, whether to trust the result, and whether to send. The rules would live in code, but the flow would be governed by the model. Instead, code governs the flow and the agent is one node inside it. The agent enriches the message; it never gates the alert.

### 3.3 WhatsApp Groups API rejected

Verified on 2026-09-03 against Meta's documentation: the WhatsApp Business Cloud API now has a Groups API, but it requires an Official Business Account (business verification, not available to an individual), the business must create the group (it cannot join an existing neighborhood group), and groups are capped at 8 participants. It does not fit a community group. Automatic delivery in v2 will therefore be one-to-one messages to registered contacts.

### 3.4 No LangGraph

Strands has a deterministic `Graph` pattern (`GraphBuilder`, custom nodes, conditional edges), so LangGraph adds nothing. More importantly, only one of the pipeline's nodes uses a model. Orchestrating four code-only steps with an agent framework is unnecessary weight. Orchestration stays in plain Python; Strands is used only inside the composer.

### 3.5 Scheduler frequency is infrastructure configuration

Runtime settings (coordinates, thresholds, channel, checklist) live in SSM Parameter Store and are read by the Lambda at start. The scheduler rate is different: a Lambda cannot change its own trigger by reading a parameter. The rate lives on the EventBridge Scheduler resource, defined in IaC. In v1 it changes by redeploying. The admin API (subproject 2) will later call the Scheduler API to update it.

### 3.6 Contacts are not stored in Parameter Store

Contacts are personal data, grow over time, and are queried by channel. They live in DynamoDB behind `ContactRepository`, not in SSM.

### 3.7 Infrastructure as code in TypeScript, agent via AgentCore CLI

The application is Python. The CDK stack is written in **TypeScript**, following the most common CDK convention. The composer agent is deployed to AgentCore Runtime with the **AgentCore CLI** (starter toolkit), not through CDK.

## 4. Architecture and Flow

See `docs/rain-alert-core-architecture.drawio` for the diagram.

Two independent signals are combined. The SENAMHI warning says "the region is at risk"; Open-Meteo says "at Ferreñafe specifically, X mm with Y % probability are expected in the next 48 hours." Neither alone is sufficient: the official warning covers the whole department (including the highlands), and global models are imprecise on the Peruvian coast. Together they produce an alert with judgment.

Every 6 hours, EventBridge Scheduler triggers a Python Lambda that runs the following cycle:

1. **Load configuration** from SSM Parameter Store (coordinates, thresholds, active channel, preparedness checklist).
2. **Fetch warnings.** The SENAMHI adapter scrapes the Lambayeque warnings table and returns normalized current warnings, or reports the source as unavailable.
3. **Fetch forecast.** The Open-Meteo adapter returns 48 hours of hourly precipitation and probability for the configured coordinates, or reports the source as unavailable.
4. **Evaluate risk.** The domain applies fixed rules (section 6) and produces a `RiskAssessment` with level none / prepare / imminent, the affected window, and the reasons.
5. **Deduplicate.** `AlertPolicy` checks the alerts-sent table. If the same level was already notified for this window and there is no escalation, the cycle ends here at zero cost, without invoking any model.
6. **Compose.** Only for a new alert, the Lambda invokes the composer agent on AgentCore Runtime with structured data. The agent returns the message text. If the agent fails, times out, or returns an invalid message, the domain's deterministic template produces the text instead.
7. **Validate and notify.** Code validates the text (section 7), reads recipients from `ContactRepository`, and delivers through the configured `Notifier`. The sent alert is then recorded in the alerts-sent table.

Source failures are handled explicitly (section 6.3). The SENAMHI scraper is the most fragile piece because any HTML change breaks it, so it is isolated behind a port, and its failure degrades the system rather than stopping it.

## 5. Data Sources

### 5.1 SENAMHI meteorological warnings (Lambayeque)

- URL pattern: `https://www.senamhi.gob.pe/?dp=lambayeque&p=aviso-meteorologico`
- **No API exists.** The page is a server-rendered HTML table. No JSON, XML, RSS, or CAP endpoints were found.
- Table fields: warning title, number, emission date, start date, end date, duration in hours, level (amarillo / naranja / rojo).
- Detail page per warning by ID: `/?p=aviso-meteorologico-detalle&a=<year>&b=<id>&c=00&d=SENA`.
- Scope is the **whole department**, not the city.
- The page lists historical warnings back to 2024, so a correctly parsed table is never empty. This property is used to detect scraper breakage (section 6.4).

### 5.2 Open-Meteo forecast

- Free for non-commercial use, no API key, no rate limits for non-commercial volumes.
- Hourly `precipitation` and `precipitation_probability` for any latitude/longitude.
- Provides the city-level signal for Ferreñafe. Model accuracy on the Peruvian coast is an open question to observe during calibration (section 11).

## 6. Domain Model and Risk Rules

The domain knows nothing about AWS, HTTP, HTML, or language models. It is pure Python and testable in milliseconds without network access.

### 6.1 Entities

- **Warning** — a normalized official warning: level (yellow / orange / red), start and end window, region, title, source ID.
- **Forecast** — a 48-hour hourly series of precipitation (mm) and probability (%) for the configured coordinates.
- **RiskAssessment** — output of the evaluator: level (none / prepare / imminent), affected window, and a list of textual reasons justifying the level. The reasons feed the composer agent and give the operator an audit trail of why the alert fired.
- **AlertPolicy** — decides whether a `RiskAssessment` deserves delivery:
  - Send when the level is **new for that window**, or when it **escalates** relative to the last sent alert.
  - **Never** re-send the same level within the same window.
  - **De-escalation sends nothing** in v1.

### 6.2 Source status

`WarningProvider` and `ForecastProvider` do not return bare data. Each returns a result with a status: **available** (with data, possibly an empty list of warnings) or **unavailable**. "Zero warnings with a healthy source" means calm. "Unavailable" means the system does not know. The evaluator treats these two cases differently.

### 6.3 Risk rules

Thresholds are read from configuration (SSM Parameter Store). Initial values:

| Level | Condition (SENAMHI available) |
|---|---|
| None | No condition below is met |
| Prepare | SENAMHI warning yellow or higher for Lambayeque **AND** Open-Meteo forecasts ≥ 9.5 mm accumulated in the next 48 h with probability ≥ 60 % |
| Imminent | SENAMHI warning orange or red, **OR** Open-Meteo forecasts ≥ 20 mm in 24 h with probability ≥ 70 % |

Degraded modes:

- **SENAMHI unavailable.** Open-Meteo alone may trigger *Prepare*, but with probability ≥ 70 % instead of 60 %, to compensate for the missing official confirmation. The recipient message must state explicitly that the official SENAMHI source was unavailable and the alert is based on forecast only. The operator receives a separate technical notice that the source is down. *Imminent* is unchanged (it could already fire from a single source).
- **Open-Meteo unavailable.** A SENAMHI orange or red warning still triggers *Imminent*. *Prepare* cannot fire. The operator receives a technical notice.

Named test scenarios that the rules must satisfy:

- 2017-03-12, 29.8 mm forecast in 24 h, probability ≥ 70 % → **imminent**.
- 9 mm in 48 h at ≥ 60 % with a SENAMHI yellow warning → **prepare**.
- SENAMHI unavailable, 15 mm in 48 h at 65 % → **none** (degraded mode requires 70 %).

### 6.4 Scraper breakage detection

The SENAMHI adapter must **not** report "available with zero warnings" when it failed to understand the HTML. If the expected table structure is not recognized, or zero rows are extracted, the adapter reports **unavailable**. Because the page always lists history since 2024, an empty extraction is evidence of breakage, not of calm. This is the case in which an HTML change would otherwise leave the system blind without anyone noticing.

### 6.5 Evidence behind the thresholds

SENAMHI's warning methodology is published in *Estimación de Umbrales de Precipitaciones Extremas para la emisión de avisos meteorológicos* (2014). It does not use fixed millimetre values nationwide. For each station it computes percentiles of daily rainfall over rainy days (RR > 0.1 mm) and classifies: moderately rainy above p75, rainy above p90, very rainy above p95, extremely rainy above p99. Warning levels yellow, orange, and red correspond approximately to p90, p95, and p99.

A CENEPRED risk assessment report for Pimentel (Chiclayo) reproduces SENAMHI's thresholds for the **Reque** station, the closest coastal station to Ferreñafe, series 1964–2014:

| Category | Daily rainfall at Reque |
|---|---|
| Moderately rainy (p75–p90) | 2.8 – 5.7 mm |
| Rainy (p90–p95, ≈ yellow) | 5.7 – 9.5 mm |
| Very rainy (p95–p99, ≈ orange) | 9.5 – 38.8 mm |
| Extremely rainy (> p99, ≈ red) | > 38.8 mm |

The same report records Reque's historical maximum of **60.4 mm on 1998-02-18**, and the El Niño costero 2017 daily maximum of **29.8 mm on 2017-03-12**, which was enough for severe flooding in Chiclayo.

How the initial thresholds map to this evidence:

- *Prepare* at 9.5 mm in 48 h sits exactly on Reque's "very rainy" (p95) boundary.
- *Imminent* at 20 mm in 24 h is above "very rainy" and below the 2017 peak of 29.8 mm. It is set below the observed peak because global models tend to underestimate rainfall on the Peruvian coast; 20 mm would have captured the critical days of 2017.

Limitations of the evidence:

- Chiclayo mean annual precipitation of ≈ 29.6 mm (1950–1991) comes from an IGP climate page seen only in search results; the page itself did not respond and the figure is not directly verified.
- Ferreñafe has no station in these sources. It lies roughly 20 km inland from Reque and may receive somewhat more rain. This bias is conservative in the safe direction: if Reque-scale thresholds fire, Ferreñafe likely qualifies too.
- The ENFEN technical report on 2017 was blocked by a security check and could not be read.

**The thresholds are hypotheses to calibrate during the first rainy season.** They live in Parameter Store precisely so they can be tuned without code changes.

Sources:

- SENAMHI, Estimación de Umbrales de Precipitaciones Extremas (2014): <https://www.senamhi.gob.pe/load/file/01402SENA-6.pdf>
- CENEPRED, risk assessment for Pimentel, Chiclayo (Reque thresholds and 1998/2017 records): <https://sigrid.cenepred.gob.pe/sigridv3/storage/biblioteca//18143_informe-de-evaluacion-de-riesgos-por-lluvias-intensas-en-el-pueblo-joven-ampliacion-nuevo-progreso-del-distrito-de-pimentel-provincia-de-chiclayo-y-de.pdf>
- Andina, meaning of SENAMHI yellow/orange/red levels: <https://andina.pe/agencia/noticia-tu-rio-esta-alerta-significan-los-umbrales-amarillo-naranja-y-rojo-del-senamhi-1064719.aspx>
- IGP, Chiclayo climate (not directly verified): <http://met.igp.gob.pe/clima/HTML/chiclayo.html>
- Meta, WhatsApp Groups API: <https://developers.facebook.com/documentation/business-messaging/whatsapp/groups>
- Open-Meteo: <https://open-meteo.com/>
- AgentCore pricing: <https://aws.amazon.com/bedrock/agentcore/pricing/>

## 7. Message Composer Agent

### 7.1 Runtime and model

- Strands Agents SDK, deployed to **Amazon Bedrock AgentCore Runtime** with the AgentCore CLI.
- Model: **Claude Haiku 4.5** on Bedrock. Drafting one paragraph from pre-digested data does not need a larger model, and the cost per alert is a fraction of a cent.
- AgentCore Runtime bills active CPU per second plus peak memory while the session lives. A few-second invocation, only on alert, costs fractions of a cent.

### 7.2 Input contract

The agent receives structured JSON, never raw data:

- risk level and affected window
- evaluator reasons
- forecast summary: accumulated mm at 24 h and 48 h, peak hour, probability
- SENAMHI warning summary (level, title, dates, ID) when available
- status of each source (available / unavailable)
- city name
- preparedness checklist from configuration

### 7.3 Tools

Two **read-only** tools:

- `get_warning_detail(warning_id)` — full text of the SENAMHI detail page for context.
- `get_recent_alerts(n)` — last alerts sent, to keep continuity across messages (for example, "as we warned yesterday, rain conditions continue").

The agent has **no send tool**. Delivery is performed by code after validation.

### 7.4 Output contract

```json
{ "title": "...", "body": "...", "level": "prepare | imminent", "valid_until": "ISO-8601" }
```

Recipient-facing text is in neutral Spanish. In degraded mode the body must state that the official SENAMHI source was unavailable.

### 7.5 Validation in code

Before the message is accepted, code verifies:

1. `level` matches the evaluated level.
2. The body mentions the city.
3. The body respects the maximum length.
4. **The body contains no number that does not appear in the input.** This prevents the agent from inventing "80 mm" where the forecast said 12.

### 7.6 Fallback

**The alert never depends on the agent.** If AgentCore fails, times out, or returns output that fails validation, the domain's deterministic template produces the message from the same structured input and the alert is sent anyway. The agent improves wording; it is not a point of failure.

## 8. Notification

Delivery goes through the `Notifier` port. v1 and v2 are **stages of the same project**, not alternatives to choose between.

### 8.1 v1 — Human relay (built now)

The operator (the project author) receives the ready-to-forward message and forwards it to the neighborhood WhatsApp group manually. Channel: Telegram bot or SES email (choice deferred, see section 11). The operator also receives technical notices (source unavailable, agent fallback used).

### 8.2 v2 — Automatic delivery (later)

Added once v1 has produced real alerts and proven itself:

- **One-to-one WhatsApp** via AWS End User Messaging Social, which wraps the WhatsApp Business API. Constraint to verify at that time: business-initiated messages require pre-approved templates, so the composed text will likely have to fit as variables inside a fixed template rather than as free-form body. This may constrain the output contract.
- **Email** via Amazon SES, free-form text.
- **SMS** as a later adapter if budget allows.

v2 requires Meta business verification, template approval, and contacts with consent. That is paperwork and time, not design; deferring it avoids stalling the project before anything works.

## 9. Configuration and Storage

| Concern | Store | Notes |
|---|---|---|
| Coordinates, thresholds, active channel, preparedness checklist | SSM Parameter Store (standard tier, free) | Read by the Lambda at start via `ConfigRepository` |
| Contacts (recipient, channel, consent) | DynamoDB table `contacts` | Personal data; managed later by the admin web via `ContactRepository` |
| Alerts sent (level, window, timestamp, message) | DynamoDB table `alerts-sent` | Deduplication and audit via `AlertRepository` |
| Scheduler rate | EventBridge Scheduler resource (IaC) | Changed by redeploy in v1; admin API updates it later |

## 10. Code Structure, Testing, and Deployment

### 10.1 Structure (hexagonal)

```
alerta-lluvias-ferrenafe/
  src/rain_alert/
    domain/        entities, RiskEvaluator, AlertPolicy, fallback message template
    ports/         Protocols: ForecastProvider, WarningProvider, MessageComposer,
                   Notifier, AlertRepository, ContactRepository, ConfigRepository
    application/   RunAlertCycle: the use case orchestrating the 7 steps
    adapters/      open_meteo, senamhi_scraper, ssm_config, dynamodb, agentcore_composer,
                   telegram_notifier, ses_notifier
    entrypoints/   lambda_handler and a local CLI
  agent/           Strands composer agent with its two tools, deployed separately to AgentCore
  infra/           CDK stack in TypeScript
  tests/
  docs/
```

The **local CLI** runs the full cycle against the real sources with a console notifier that prints what would have been sent without sending anything. It is the primary tool for learning the system's behavior and calibrating thresholds.

### 10.2 Testing strategy (Strict TDD)

- **Domain (evaluator, policy):** table-driven unit tests using the Reque thresholds and the named scenarios in section 6.3.
- **Scraper:** HTML fixtures captured from the real SENAMHI page, plus a fixture with altered structure that must yield *unavailable*, never "zero warnings".
- **Agent output validator:** cases with invented numbers that must be rejected and fall back to the template.
- **Use case:** fakes for every port, no AWS. Verifies deduplication, degraded modes, and fallback.
- **AWS adapters:** moto for DynamoDB and SSM.
- **Agent:** tested locally with Strands before deployment.
- **Infrastructure:** CDK TypeScript assertions tests.

Tooling: `uv`, `pytest`, `ruff` for Python; CDK TypeScript for infrastructure.

### 10.3 Deployment

- One CDK stack (TypeScript) in `us-east-1`: EventBridge Scheduler, Lambda, the two DynamoDB tables, SSM parameters, IAM roles.
- The composer agent is deployed to AgentCore Runtime with the AgentCore CLI.
- Expected monthly cost with free tiers for Lambda, Scheduler, DynamoDB, and SSM: cents, almost entirely from Bedrock and AgentCore when an alert occurs.

## 11. Repository and Data Hygiene

The repository is intended to be published (recommended: open source under Apache 2.0, pending the owner's confirmation). The following rules apply from the first commit regardless of that decision, because git history is permanent:

- **No personal data in the repository.** Contacts (names, phone numbers, emails, consent) live only in DynamoDB. Test fixtures use obviously fake data.
- **No secrets in the repository.** Telegram bot tokens, AWS account IDs, email addresses, and API credentials come from SSM Parameter Store or environment variables. A committed example file lists variable names with no values. Secrets are checked before every commit.
- **Disclaimer in the README.** The system is a community complement to the official warnings of SENAMHI and INDECI, not a replacement, and is provided without warranty.
- **Open-Meteo license.** Free for non-commercial use only; the project is non-commercial. Any commercial reuse by third parties is their responsibility under Open-Meteo's terms.

## 12. Open Questions to Resolve at Implementation

- License choice (Apache 2.0 recommended) and public or private repository.
- WhatsApp template constraints for v2 and their effect on the output contract.
- Open-Meteo model accuracy for the Peruvian coast, observed during calibration.
- Exact coordinates for Ferreñafe to store in configuration.
- Telegram bot versus SES email for the v1 operator channel.
