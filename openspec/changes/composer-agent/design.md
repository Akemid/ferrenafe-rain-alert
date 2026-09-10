# Design: Composer Agent (Strands on AgentCore, with Deterministic Fallback)

Source of truth: `docs/superpowers/specs/2026-09-03-rain-alert-core-design.md` section 7. Scope and closed decisions: `proposal.md`. Behaviour: `specs/*/spec.md`. Contracts inherited from change 1: `openspec/changes/archive/2026-09-07-core-alert-cycle/design.md` — decisions there are numbered D1–D12 and this document continues at **D13** so the two read as one series.

Design spec 3.2 is preserved and strengthened, not restated: **the model never decides whether to alert.** Every decision below is checked against that invariant, and section 8 maps each of the proposal's eight non-capabilities to the code that enforces it.

---

## 1. Technical Approach

One sentence: *the agent is a second implementation of the existing `MessageComposer` port, chosen in the composition root, wrapping a narrow invocation seam whose every failure returns the template's output — so the alert path has no branch that can end without a message.*

Four structural commitments carry the rest of the document.

| # | Commitment | Consequence |
|---|---|---|
| 1 | The `MessageComposer` port signature does not change | `RunAlertCycle` keeps one composer dependency and gains no branch per implementation; change 3 consumes the same port |
| 2 | The only value crossing that port is `AlertMessage`, so provenance must ride on it | D13 |
| 3 | The fallback is one `try` in one function, with the template value computed **before** the agent is called | D15 — "the alert never depends on the agent" becomes lexically checkable |
| 4 | `pydantic`, `strands` and `bedrock_agentcore` exist only in `agent/`, a second deployment unit | D22 — enforced in both directions by AST test |

The port docstring already binds this change. Quoted verbatim from `src/rain_alert/ports/__init__.py`:

> **Every source-derived free text that reaches `AlertMessage.title` or `AlertMessage.body` must pass through `rain_alert.domain.sanitize.sanitize_source_text` first.**
>
> An agent-backed composer does not weaken this and does not replace it: a model asked to quote a title will quote it verbatim, newlines included, and prompt text arriving from a scraped page is exactly the input a composer must not be handed unfiltered.

The agent composer discharges it twice: on the way **in** (every source-derived string is sanitized before it enters the prompt, D20) and on the way **out** (the validator rejects a body carrying control or bidi characters, §6 rule 7). Sanitizing the input is not sufficient on its own, because the model is free to emit characters nobody gave it.

---

## 2. Architecture Decisions

### D13 — Provenance rides on `AlertMessage.composed_by`

**Choice**: add `ComposerName` (`StrEnum`: `template`, `agent`) to `domain/values.py`, and a defaulted field `composed_by: ComposerName = ComposerName.TEMPLATE` to `AlertMessage`. `RunAlertCycle` replaces the hardcoded `composer="template"` at `application/run_alert_cycle.py:189` with `composer=message.composed_by.value`.

**Alternatives considered**: widen the port to return a `(message, provenance)` pair or a `Composition` wrapper (breaks the port, breaks `domain/template.py`, breaks every fake and the CLI preview path); a second port `ProvenancedComposer` used by the use case (two composer ports, one of which is decorative, and the sanitization chokepoint claim moves off the port the docstring is written on); a stateful `last_composer` attribute read after the call (spooky, and wrong the moment anything composes twice); `isinstance` in the use case (the branch-per-composer the brief forbids).

**Rationale**: the constraint decides it. If the port must not change and the use case must not branch, the only channel from composer to use case is the return value. Provenance is genuinely a property of the text — "who wrote this" — and putting it there also gives the CLI and change 3's `--json` the fact for free, without a second channel.

**Why defaulted**: change 1 already settled this exact shape for `Available.notes` ("a defaulted field is purely additive, breaks no merged call site and keeps `mypy --strict` green"). The default is also the conservative value: a composer that forgets to set it under-claims agent involvement rather than over-claiming it.

**No persisted-format change.** `adapters/serialization.py:151` already writes `"composer": record.composer`, and `alert_record_from_dict` rebuilds the `AlertMessage` from that same document — so the reconstruction gains `composed_by=ComposerName(document["composer"])` and the document shape is byte-identical. This mirrors the decision already recorded in that module's docstring: *"`AlertRecord.level` and `AlertRecord.message.level` are stored once."* Provenance is now the second fact stored once.

### D14 — Selection lives in the composition root, as an `if`, not a registry

**Choice**: `entrypoints/wiring.build_local_deps` loads `AlertConfig` once and calls a module-level `select_composer(config, ...) -> MessageComposer`, a two-branch `if` on `config.composer`.

**Alternatives considered**: a `COMPOSERS: dict[ComposerName, type]` dispatch table (the two constructors have different arities; a table would need factories, which is more code than the `if` it replaces — rule of three not met); selecting inside `RunAlertCycle` (puts wiring in the use case and re-introduces the branch); an environment variable read at wiring time (see D23).

**Rationale**: KISS, and the composition root is already the only place in this codebase that knows which leaves are real (change 1 D6). `build_local_deps` therefore calls `config.load()` once for the selection, and `RunAlertCycle.execute` calls it again for the cycle. For `StaticConfigRepository` that is free. **In change 3 it is one extra SSM read every six hours** — negligible, and the natural fix there is a memoizing `ConfigRepository`, not a design change here. Recorded so change 3 does not rediscover it.

### D15 — The fallback is one `try`, and the template runs first

```python
# src/rain_alert/adapters/agent_composer.py
class AgentBackedComposer:
    def compose(self, request: MessageRequest) -> AlertMessage:
        fallback = self._template.compose(request)          # always, before anything can fail
        try:
            draft = self._invoker.invoke(build_prompt(request))
        except AgentInvocationError as exc:
            return self._fell_back(FallbackReason(exc.reason), exc.detail, request, fallback)
        except Exception as exc:  # noqa: BLE001 — an SDK bug must degrade the cycle, not abort it
            return self._fell_back(FallbackReason.UNEXPECTED_ERROR, f"{type(exc).__name__}: {exc}", request, fallback)
        candidate = parse_candidate(draft, request)          # raises nothing; returns None on a bad shape
        if candidate is None:
            return self._fell_back(FallbackReason.MALFORMED_PAYLOAD, ..., request, fallback)
        violations = validate_message(request, candidate)
        if violations:
            return self._fell_back(FallbackReason.VALIDATION_FAILED, ..., request, fallback)
        return replace(candidate, composed_by=ComposerName.AGENT)
```

**Where the `except` lives**: in the adapter, never in `RunAlertCycle`. The use case today contains **zero** `try` blocks — change 1 made failure travel as data (`SourceResult`, D2). Here failure cannot be data, because the port returns `AlertMessage`; so the adapter absorbs it, exactly as `adapters/http.py` states its own first rule: *"No `httpx` exception ever escapes."* If the use case caught instead, every future composer would teach the use case its own failure modes.

**Why the template is computed first, unconditionally**: it makes "there is always a message" a property of the *function's shape* rather than of its paths. Every `return` in `compose` returns either the agent's validated text or the exact `fallback` object built on line one; no path constructs a message late, and none can be reached with `fallback` unbound. The cost is one pure `"\n".join` per sending cycle — microseconds — paid also on the accepted path.

**The fallback value is never validated.** Validating it would create a path where a validator bug produces no message at all. The validator's correctness against the template is instead asserted as a test property (§6, invariant V0).

**Mutation proof (proposal success criterion 2)**: deleting either `except` clause makes the corresponding fault-table rows raise out of `compose`, out of `RunAlertCycle.execute`, and out of the test — the row asserting "an alert was sent" fails. Deleting the `if violations:` guard makes the validation rows fail on `composer == "agent"` and on the body assertion. Both are recorded as a manual, repeatable procedure in the change artifacts; no mutation-testing dependency is added.

### D16 — The validator is a pure domain module returning violations

**Choice**: `src/rain_alert/domain/message_validation.py`.

```python
def validate_message(request: MessageRequest, candidate: AlertMessage) -> tuple[Violation, ...]: ...
```

Empty tuple means accepted. `Violation(rule: ValidationRule, detail: str)` is a frozen dataclass; `ValidationRule` is a `StrEnum`.

**Alternatives considered**: `application/` placement (the proposal's fallback, and `state.yaml → open_decisions.validator-placement` hands the choice here — **resolved: it stays in `domain/`**); returning `bool` (loses what was wrong, and the operator notice exists precisely to say what was wrong); raising (turns the adapter's happy path into a second `try`, when the design's whole point is that there is exactly one).

**Rationale**: it is a rule about *what the system is willing to say*, which is the same argument `domain/sanitize.py` already makes in its own docstring for living in the domain. It imports stdlib and domain types only, carries no LLM, HTTP or AWS concern, and is therefore compliant with `openspec/config.yaml → rules.specs` ("keep the domain layer free of AWS/HTTP/HTML/LLM concerns"). Keeping it beside `dedup.py` and `outage.py` also keeps it table-testable with no fakes, which is how every other rule in this codebase is tested.

### D17 — Numbers: strip the residue, canonicalize, then compare against an allowed set

Full mechanism in §6 rule 8. The three decisions the spec explicitly handed to design:

1. **Residue before scan.** Strings the *system itself supplied* — each `checklist` item, `sanitize_source_text(warning.title)`, `city`, `timezone` — are removed from the body by exact substring replacement (longest first) before any numeric scan. Without this, the real Ferreñafe checklist alone ("Almacena agua potable para al menos **dos** días") would fail every agent message.
2. **Canonicalization, not string equality.** `12,4`, `12.4` and `12.40` are the same quantity; each extracted digit token and each allowed value is reduced to a canonical `Decimal` before comparison.
3. **A word-number quantifying a reported unit is rejected, never parsed.** A Spanish number word is a violation when it directly quantifies a unit this system reports: millimetres, a percentage, or hours. The word is never parsed into an integer. Parsing `veintidós mil cuatrocientos` is a grammar, and every bug in that grammar fails *lax*. The prompt demands digits, so such a word already means the draft disobeyed; distrusting it is cheaper and safer.

   *Narrowed 2026-09-09, after the spec was amended and before any code.* This decision first rejected **any** Spanish number word, unconditionally. That was wrong against the shipped system, not merely strict. The deterministic template's own body carries three today, all from the operator checklist: `dos` in "al menos dos días", and `un`/`una` in "un botiquín" and "una linterna". The unconditional rule would have rejected the system's own text, so invariant V0 would have failed on the first run — the validator, not the template, being the thing at fault. And `un`/`una` are the ordinary Spanish indefinite articles, so an unconditioned detector fires on almost any sentence.

   Restricting it to a reported unit targets the real threat, inventing a rainfall or probability figure, and leaves ordinary prose alone. It is deliberately narrower than the digit rule: a word-number quantifying anything else, such as days or objects, is outside this rule. The spec is the authority; see `specs/agent-message-composition/spec.md`, "A measurement written in words is rejected".

**Direction of error is chosen deliberately.** Wrong-strict costs one template message and one operator notice. Wrong-lax puts an invented rainfall figure in front of a community. Every ambiguity in §6 resolves strict.

### D18 — The invocation seam mirrors `HtmlFetcher` / `FetchError`

```python
# src/rain_alert/adapters/agent_invoker.py
class AgentInvoker(Protocol):
    def invoke(self, prompt: str) -> Mapping[str, Any]: ...     # a decoded JSON object

class AgentInvocationError(Exception):
    def __init__(self, reason: UnavailableReason, detail: str) -> None: ...
```

**Rationale**: this is the shape change 1 already chose twice — a `Protocol` fetch seam plus one translated exception carrying a `UnavailableReason` the domain already knows (`TIMEOUT`, `TRANSPORT_ERROR`, `BAD_STATUS`, `MALFORMED_PAYLOAD`). Those four values are exactly the transport rows of the fault-injection table, so no new enum is introduced. **Rejected**: reusing `FetchError` itself (its name and home say HTML fetch; `except FetchError` in the composer would also swallow a fetch error arriving from somewhere else); returning `SourceResult` (the composer is not a source, and the tagged union buys nothing where there is exactly one caller).

**Real implementation** — `BedrockAgentCoreInvoker`, boto3, in `adapters/agentcore_invoker.py`. *To verify at apply:* the exact boto3 client name and operation for the AgentCore data plane, its request/response shape, and whether the payload arrives as a streaming body. **Preferred**: boto3, because the proposal's deployment table already allows `boto3` in adapters and SigV4 must not be hand-rolled. **Fallback if the pinned botocore does not expose the operation**: HTTPS to the runtime endpoint with a signing helper, reusing `adapters/http.py`.

**No credentials offline**: the boto3 client is *injected* (`client: Any | None = None`) and built lazily on first `invoke`, exactly as `HttpxHtmlFetcher(transport=...)` injects its transport. Constructing a client eagerly in wiring raises without a region configured, which would break `uv run pytest` on a clean machine. The offline suite drives `tests/support/fakes.FakeAgentInvoker`, a hand-written spy with a scripted response or a scripted exception and a `calls` list — no `unittest.mock`, per the standing rule in `tests/support/fakes.py`.

### D19 — Hard 10-second deadline, one attempt, no retry

| Question | Answer |
|---|---|
| Deadline | 10 s wall clock, resolving `state.yaml → open_decisions.composer-timeout` |
| Attempts | 1. No retry, no backoff — change 1 D12's posture, and a retry doubles the worst case to buy a second chance at something the template gives for free and instantly |
| Sized for | A **cold** start. The cycle runs every six hours; no warm reuse should be expected, so the cold case is the only case |
| Mechanism | botocore `Config(connect_timeout=3, read_timeout=10, retries=...)`. *To verify at apply:* the exact retry-disabling key for the pinned botocore (`max_attempts` in legacy mode counts retries; `total_max_attempts` in standard mode counts attempts) |
| Caveat | `read_timeout` bounds each socket read, not total wall clock. For a single non-streaming response the two coincide. **If the response streams** (to verify, D18), the invoker must additionally enforce a `time.monotonic()` deadline across the read loop and raise `TIMEOUT` on overrun |
| Why not 5 s | Below plausible cold-start latency the agent would never run in production, and the fallback notice would fire every cycle — a kill switch by accident |
| Why not 30 s | Nobody waits on this job, but Lambda bills for it, and the timeout must stay well inside change 3's function timeout with the rest of the cycle |
| Tunable | It is adapter configuration, not a constant, so the first live measurements can move it without a code change |

Outbound dependency on change 3: the Lambda timeout must exceed *cycle duration + this deadline*.

### D20 — The prompt is a fixed instruction block plus a JSON data block

`src/rain_alert/adapters/agent_prompt.py::build_prompt(request: MessageRequest) -> str` — a pure function, unit-tested with no I/O.

**Why `adapters/` and not `domain/`**: a prompt is written in the *model's* vocabulary, which is external. This is the same argument change 1 §5.1 used to put `senamhi_classification.py` in the adapter while the hazard rules stayed in the domain, and it keeps `openspec/config.yaml`'s "domain free of LLM concerns" rule literally true.

**Fencing, two layers, in this order**:

1. Every source-derived free text passes `sanitize_source_text` **before** entering the payload. This removes newlines, C0/C1 controls and bidi controls, and caps length.
2. The payload is then `json.dumps(..., ensure_ascii=False)` into a `<datos>…</datos>` fence. JSON escaping alone is **not** a substitute for step 1: change 1 §3.3 recorded that under `ensure_ascii=False` `json.dumps` writes bidi overrides through verbatim. Sanitize first, encode second.

The instruction block tells the model that everything inside the fence is data. That instruction is written, and it is **not counted as a control** — section 8 lists what holds regardless of it.

**What the payload carries**: `city`, `timezone`, `level`, window start/end (ISO UTC and rendered local), structured reasons with their numbers, the sanitized aviso title and level, the forecast summary numbers, both source statuses, and the checklist items verbatim.

**What it never carries**: contacts, handles, tokens, account ids, ARNs, state-file contents — and `ForecastThresholdReason.probability_threshold_pct`, which `domain/reasons.py` marks operator-only. Omitting the threshold from the prompt is the mechanism that keeps it out of a recipient's message; asking the model not to mention it would not be.

### D21 — The fallback notice is emitted by the adapter, not the use case

**Choice**: `AgentBackedComposer` is injected with the `Notifier` and the clock, and sends one `OperatorNotice(kind=NoticeKind.AGENT_FALLBACK_USED)` per fallback, before returning the template's message.

**Alternative considered**: the use case emits it, gated on `config.composer is AGENT and message.composed_by is TEMPLATE`. That is branch-free in the "per composer" sense and was close. **Rejected because it loses the reason**: the use case knows *that* the agent was skipped, never *why*. A notice reading "fallback happened" with no fault, no detail and no violation list does not do the job the notice exists for — the proposal's own words are "silent degradation is how a broken agent stays broken for a season", and a reasonless notice is a slower version of the same failure.

**Consequences, stated so a reviewer does not read them as bugs**:

- `CycleResult.notices` continues to carry outage notices only. Its docstring is amended to say so. The operator still sees the fallback notice, because `ConsoleNotifier` writes it to the same stream.
- The notice is sent *during* composition, therefore **before** `notifier.send_alert`. That ordering is right: the operator learns the message they are about to relay came from the template.
- `OperatorNotice.sources` is `frozenset()` — no data source failed. `ConsoleNotifier` will print `sources: ` with an empty list. Accepted; the notice's `subject` and `body` carry the information.
- `NoticeKind.AGENT_FALLBACK_USED = "agent_fallback_used"` is not a new decision: `domain/values.py:45` and change 1 §3 both reserve that exact spelling in a comment.

### D22 — `agent/` is a second uv project, and the boundary is tested in both directions

`agent/` gets its own `pyproject.toml` and lockfile with `strands-agents`, `bedrock-agentcore` and `pydantic`. It is not a package inside `src/rain_alert`, is not part of the `rain-alert` wheel, and is never on the change-3 Lambda's dependency graph. Root `[tool.pytest.ini_options] testpaths=["tests"]` already excludes it; its suite runs under its own command. *To verify at apply:* the exact `uv` invocation for the nested project.

Both directions are enforced in `tests/architecture/test_layer_boundaries.py`, extending the module constants change 1 left for this purpose:

| Direction | Test | Forbidden roots |
|---|---|---|
| `src/` → `agent/` and `src/` → model SDKs | new `test_src_package_never_imports_the_agent_deployment_unit`, scanning **all** of `src/rain_alert` | `agent`, `pydantic`, `strands`, `bedrock_agentcore` |
| `agent/` → `src/` | new `test_agent_unit_never_imports_the_application_package`, scanning `agent/` | `rain_alert`, `src` |
| `domain/` (unchanged rule) | existing `test_domain_package_has_no_forbidden_imports` | `DOMAIN_FORBIDDEN_ROOTS` + `pydantic` |

Note that `pydantic` is **not** in today's `DOMAIN_FORBIDDEN_ROOTS`; the change-2 line adds it, and adds it at the wider `src/` scope, which is what `state.yaml → resolved_decisions.pydantic-boundary` actually requires.

**Why the second direction needs a test at all** — the part a reader cannot infer: an `agent/` module importing `rain_alert.domain` **works on the developer's machine**, because both trees sit on `sys.path` during local development. It fails only after deploy, inside AgentCore Runtime, where the `rain_alert` package does not exist. That is the most expensive place in this project to discover an import.

**The contract cannot be shared as code**, therefore. It is duplicated deliberately: frozen dataclasses in `domain/messages.py`, a Pydantic model in `agent/`. The drift guard is a golden JSON document at `contracts/agent-composition.json`, at the repository root so neither unit imports the other's tests: the `src/` suite asserts the prompt payload `build_prompt` produces carries exactly the golden input keys, and the `agent/` suite asserts `CompositionOutput.model_json_schema()` covers exactly the golden output keys.

### D23 — Kill switch in `AlertConfig`; endpoint and timeout in adapter settings

| Setting | Home | Why |
|---|---|---|
| `AlertConfig.composer: ComposerName = TEMPLATE` | `domain/config.py`, read by `ConfigRepository` | It is an operator *policy* choice and it is rollback level 1. `AlertConfig` is what SSM feeds in change 3, so flipping it there is instant and needs no deploy. An env var read in wiring could not be changed without a redeploy of the Lambda |
| `AgentRuntimeSettings(endpoint, timeout_seconds, region)` | `adapters/agentcore_invoker.py`, built in wiring from `RAIN_ALERT_AGENT_RUNTIME_ARN` / `RAIN_ALERT_AGENT_TIMEOUT_S` | An ARN is an AWS concern. Putting it on `AlertConfig` would widen the frozen stdlib core with infrastructure (D1) and violate the domain-purity rule. Precedent: `DEFAULT_TIMEOUT` lives in `adapters/http.py`, and the Open-Meteo endpoint lives in its own adapter |

**Ships defaulting to `template`.** The change lands inert; the owner flips it. `select_composer` with `composer=TEMPLATE` returns `domain.template.MessageComposer()` — literally the object `main` wires today — so the "behaves exactly as `main`" success criterion is provable by construction, not by comparison.

### D24 — CLI `--composer {template,agent}`, tri-state, default "whatever config says"

**Rejected**: a boolean `--agent`. An operator debugging a bad agent message needs to force the *template* while configuration says `agent`; a boolean cannot express that. Absent flag = use `config.composer`.

Output changes: the `Message` block header becomes `SENT [composer: agent]` / `PREVIEW (NOT SENT) [composer: template]`, and the `--json` document's `message` object gains `"composer"`.

**The preview always reads `template`, and that is not a bug.** Change 1 D11 requires the CLI to print a message on every run while a deduplicated cycle invokes no composer; the preview is rendered by calling the template directly on `CycleResult.message_request` (`cli.py:164`, `cli.py:208`). A preview therefore never invokes the agent and never costs anything. This is stated because a reader seeing `PREVIEW … [composer: template]` under `--composer agent` will otherwise file it.

---

## 3. Contracts Touched (breaking-change callout)

`AlertMessage`, `MessageRequest` and the alert-record shape are consumed by change 3. Every touch is listed here with its consequence, per the change-1 rule that anything marked *contract* is breaking to change.

| Contract | Change | Breaking? | Consequence for change 3 |
|---|---|---|---|
| `MessageComposer` port | none | no | The agent composer satisfies it structurally, like every other adapter |
| `MessageRequest` | none | no | The agent receives exactly what the template receives. This is why design 7.2 maps 1:1 |
| `AlertMessage` | **+ `composed_by: ComposerName = TEMPLATE`** | **Additive, source-compatible** | Existing construction sites (8 in the tree) keep working. **Value equality now includes provenance** — a test asserting an agent message equals a template message will now fail, which is the intended behaviour for the fault-injection table. Change 3's DynamoDB mapper reads provenance from the record attribute that already exists |
| `AlertRecord` | none | no | `composer` was already a field; it stops being a literal |
| Persisted alert document (`alert_record_to_dict`) | **none** | no | The `"composer"` attribute already exists and already round-trips. Only `alert_record_from_dict` changes, to restore `composed_by` from it |
| `AlertConfig` | **+ `composer: ComposerName = TEMPLATE`** | **Additive** | Change 3 must publish one more SSM parameter. Absent parameter ⇒ default ⇒ template |
| `NoticeKind` | **+ `AGENT_FALLBACK_USED`** | Additive | Change 3's Telegram/SES notifier gains one kind to route. Reserved by name since change 1 |
| `CycleResult.notices` | semantics clarified in the docstring | no | Carries outage notices only (D21) |
| CLI `--json` document | `message.composer` added | Additive | A calibration log reader gains a field |

---

## 4. Components and Data Flow

```
                 entrypoints/wiring.select_composer(config)      ← the switch, read once
                        │
       config.composer == template          config.composer == agent
                        │                                │
             domain.template.MessageComposer   adapters.agent_composer.AgentBackedComposer
                                                   │        │            │
                                        agent_prompt   AgentInvoker   domain.template
                                        (pure)         (seam)         (the fallback value)
                                                          │
                                        ┌─────────────────┴──────────────────┐
                                BedrockAgentCoreInvoker            FakeAgentInvoker
                                (boto3, 10 s, no retry)            (tests/support)
                                        │
                        ══════ deployment boundary, no shared code ══════
                                        │
                    agent/  BedrockAgentCoreApp @app.entrypoint
                            Agent(model=BedrockModel(...), tools=[])
                            agent.structured_output(CompositionOutput, prompt)
```

Validation and provenance on the way back:

```
raw dict → parse_candidate → AlertMessage draft → validate_message(request, draft)
                                                        │
                                            () ─────────┴───────── (violations,)
                                             │                           │
                       replace(draft, composed_by=AGENT)      Notifier.send_operator_notice
                                                              + return the template's message
```

---

## 5. Sequence Diagrams

### 5.1 Accepted path — the agent's wording ships

```mermaid
sequenceDiagram
    autonumber
    participant U as RunAlertCycle
    participant A as AgentBackedComposer
    participant T as domain.template.MessageComposer
    participant P as build_prompt
    participant I as AgentInvoker
    participant R as AgentCore Runtime (agent/)
    participant V as validate_message
    participant N as Notifier
    participant S as AlertRepository

    Note over U: AlertPolicy already returned send=True; the level is fixed
    U->>A: compose(MessageRequest)
    A->>T: compose(request)
    T-->>A: AlertMessage(composed_by=template)   %% the fallback value, built first
    A->>P: build_prompt(request)
    P->>P: sanitize_source_text(title), json.dumps(payload)
    P-->>A: prompt string
    A->>I: invoke(prompt)            %% 10 s deadline, single attempt
    I->>R: invoke_agent_runtime(payload)
    R->>R: agent.structured_output(CompositionOutput, prompt)
    R-->>I: {"title","body","level","valid_until"}
    I-->>A: decoded JSON object
    A->>V: validate_message(request, draft)
    V-->>A: ()  %% no violations
    A-->>U: AlertMessage(composed_by=agent)
    U->>N: send_alert(message, recipients)
    U->>S: record_alert(AlertRecord(composer="agent"))
    Note over U,N: zero operator notices on the accepted path
```

### 5.2 Fallback path — any fault, one notice, the template ships

```mermaid
sequenceDiagram
    autonumber
    participant U as RunAlertCycle
    participant A as AgentBackedComposer
    participant T as domain.template.MessageComposer
    participant I as AgentInvoker
    participant V as validate_message
    participant N as Notifier
    participant S as AlertRepository

    U->>A: compose(MessageRequest)
    A->>T: compose(request)
    T-->>A: fallback = AlertMessage(composed_by=template)

    alt transport fault (timeout / connection / non-2xx / non-JSON / unexpected type)
        A->>I: invoke(prompt)
        I--xA: AgentInvocationError(reason, detail)
        Note over A: caught by the single try; no other code runs
    else bad shape or failed validation
        A->>I: invoke(prompt)
        I-->>A: decoded object
        A->>V: validate_message(request, draft)
        V-->>A: (Violation(UNKNOWN_NUMBER, "80"), ...)
    end

    A->>N: send_operator_notice(AGENT_FALLBACK_USED, reason + detail + violations)
    A-->>U: fallback            %% byte-identical to the template's output
    U->>N: send_alert(fallback, recipients)
    U->>S: record_alert(AlertRecord(composer="template"))
    Note over U,S: the alert is sent in every row of the fault table
```

---

## 6. The Validator

`validate_message(request, candidate) -> tuple[Violation, ...]`. Rules are evaluated in full — all violations are collected, not short-circuited, because the notice should name every problem the draft had.

| # | Rule | `ValidationRule` | Mechanism |
|---|---|---|---|
| 1 | `candidate.level == request.level` | `LEVEL_MISMATCH` | Enum equality. Non-capability 2 |
| 2 | `candidate.valid_until == request.window.end` | `VALID_UNTIL_MISMATCH` | Exact equality. **Rejected alternative: silently overwrite it.** A draft that got `valid_until` wrong got something else wrong too; overwriting hides that |
| 3 | Title and body non-empty after `strip()` | `EMPTY_BODY`, `EMPTY_TITLE` | — |
| 4 | Body mentions the city | `CITY_MISSING` | Casefolded containment after Unicode **NFC** normalization of both sides. A byte-wise `in` misses `Ferreñafe` written with a combining tilde, and rejecting a correct message over an encoding choice is a defect, not strictness |
| 5 | Length caps | `TITLE_TOO_LONG`, `BODY_TOO_LONG` | Module constants. Pinned by the spec; design floor below |
| 6 | Section headers appear at most once each as a full line | `FORGED_SECTION_HEADER` | Line-oriented count of `Motivos:` and `Recomendaciones:`. This is the forged-block defect change 1 closed on the input side, checked here on the output side |
| 7 | No control or bidi characters anywhere in title or body | `UNSAFE_CHARACTER` | A new predicate `sanitize.has_unsafe_characters(value)` reusing the **existing** `_REMOVED` character class. Exposing a predicate rather than copying the regex keeps the class in one place; two copies of that class is how a bidi override eventually gets through one of them |
| 8 | Every number in the residue is in the allowed set | `UNKNOWN_NUMBER` | Below |
| 9 | No Spanish number word in the residue | `WORD_NUMBER` | Below |

**V0 — the invariant that protects the rules from themselves.** For every request fixture in the suite, `validate_message(request, template.compose(request)) == ()`. If the deterministic template cannot pass the validator, the validator is wrong, not the template. This is the single test that keeps rule 5's cap above the longest real template body (roughly 900 characters with the five-item Ferreñafe checklist — the design floor for `MAX_BODY_LENGTH` is therefore comfortably above it, proposed 1500, spec to pin) and keeps rule 6 from rejecting the template's own two headers.

**Rules 8 and 9 in detail.**

1. **Residue.** From `title + "\n" + body`, remove by exact substring replacement, longest first: every `checklist` item, `sanitize_source_text(request.warning.title)` when a warning is present, `request.city`, `request.timezone`. What remains is the residue — the text the model actually wrote.
   *Consequence, deliberate:* a paraphrased checklist item is no longer an exact match, so any number inside it faces the full rules. The agent must quote checklist items verbatim. That constraint goes in the prompt and in the spec; erring here falls back, which is safe.
2. **Extraction.** Digit tokens matched as `\d+(?:[.,]\d+)?` over the residue. Splitting on `/` and `:` means `12/03/2026` yields `12`, `03`, `2026` and `14:00` yields `14`, `00` — each checked independently against the allowed set, which is why the set below includes the components of rendered dates and times.
3. **Canonicalization.** Each token → `Decimal` with `,` read as the decimal separator, then `.normalize()`. `12,4`, `12.4` and `12.40` collapse to one value. Leading zeros collapse, so `03` matches `3`.
4. **Allowed set**, built from `MessageRequest` by the same canonicalization, as the union of: `forecast.mm_24h`, `forecast.mm_48h`, `forecast.peak_probability_pct`; every `ForecastThresholdReason`'s `accumulated_mm`, `hours` and `probability_pct` (**not** `probability_threshold_pct` — it is operator-only and never enters the prompt, so it must never appear in a body); the digit components of `window.start`, `window.end` and `forecast.peak_at` rendered in `request.timezone` with the template's own `%d/%m/%Y %H:%M` format, plus their UTC ISO components; `warning.source_id` digits; and for every millimetre value, both the raw value and its one-decimal rendering, because `domain/template.py` prints `{accumulated:.1f}` and a model told "12.4 mm" may legitimately write `12,4`.
5. **Comparison.** Any canonical token not in the set is one `UNKNOWN_NUMBER` violation naming the token.
6. **Word-numbers.** An accent-stripped, casefolded, word-boundary scan of the residue against a Spanish numeral lexicon: `cero`–`quince`, `dieciséis`–`diecinueve`, the tens `veinte`–`noventa` including the `veinti-` contractions, `cien`/`ciento`, `doscientos`–`novecientos` with feminine forms, `mil`, `millón`/`millones`, and `un`/`una`. Any hit is a violation. Accent-stripping is required because `veintidós` and `veintidos` are the same word to a reader.

**Residual, accepted and recorded**: roman numerals and ordinals (`nivel III`, `primer`) are neither digit runs nor lexicon entries. They cannot express a rainfall figure usefully, and widening the lexicon towards ordinals would start rejecting ordinary prose. Wrong-lax, knowingly, and narrow.

### 6.1 Amendments after the adversarial review (2026-09-10)

Steps 1–6 above describe the first implementation. Six of them were reproduced as holes and are superseded by `specs/agent-message-composition/spec.md`, which carries the amended wording and the scenarios. Summarised here so this section is not read as current:

| Step | Superseded because |
|---|---|
| 1 Residue | The removal was an unanchored global replace with no minimum length. A span of `"80"` exempted `80` everywhere; a span could be cut out of the middle of a longer number and leave an allowed remainder; one supplied string exempted every occurrence. Now: ≥ 12 characters, word-boundary anchored, one occurrence per string supplied. Checklist items are sanitized before comparison, because the template renders them sanitized. |
| 2 Extraction | Stands as already amended at apply time (dates and times whole). Extended: a token takes every separator it can reach, so `1.234,5` is one ambiguous token; a leading sign belongs to the figure; superscript and subscript digits are extracted so they can be refused. |
| 3 Canonicalization | Threw the writing away. `Decimal("12.400") == Decimal("12.4")`, so `12.400 mm` was accepted as 12.4 — twelve thousand four hundred to a Peruvian Spanish reader. Ambiguous forms are no longer read as quantities: a three-digit group after a single separator, two separators, zero padding, a sign, and non-ASCII digit families. |
| 4 Allowed set | Unit-blind. A flat `set[Decimal]` could ask whether a number is on the request and never whether it is on the request *as that kind of quantity*, so a probability legitimised a rainfall figure. Keyed by millimetres, percentage and hours now; a bare figure with no adjacent unit is still checked against the union. |
| 6 Word-numbers | Forward-only, and `un`/`una` were in the lexicon. Four invented quantities escaped in ordinary Spanish, including the template's own `probabilidad máxima de <numeral>` phrasing; and `Espera una hora` was refused. Read in both directions now, bounded; the indefinite articles are connectors. |
| V0 | Every fixture used the shipped five-item checklist, so the invariant was never asked about the checklist. Four reachable configurations failed it. `domain/template.py` sanitizes and bounds the checklist, and the fixtures cover all four. The body measurement in the paragraph above (~900 characters) was wrong twice over: the shipped body is 511 and the largest fixture 648. |

**Residuals added, accepted and recorded**, alongside the roman-numeral note above:

- **A word-number of magnitude one.** `un`/`una`/`uno` no longer fire on their own, so `Puede caer un mm` is not refused. A compound still fires on its leading numeral. Refusing the ordinary Spanish for "wait an hour" costs far more than this.
- **`km²` and friends.** Superscript digits are extracted and always refused, so a body writing a squared unit outside a quoted span is refused with them. Wrong-strict, narrow, and no real aviso title needs it outside a quote.
- **A short checklist item bearing a digit.** The verbatim-span minimum is 12 characters, so an item shorter than that containing a digit would fail V0 rather than reach an alert. The shipped five contain no digit. `request.city` is nine characters and no longer earns an exemption; it carries no digit and no numeral.
- **`validate_message` raises on an unusable timezone.** `ZoneInfo(request.timezone)` raises exactly as `domain/template.py` already does. Deliberately not converted into a violation: a bad timezone is a *configuration* fault, not a fault in the candidate, and reporting it as a violation would make the fallback adapter send the template — which cannot render either — while telling the operator the draft was at fault. **The fallback adapter must not assume `validate_message` cannot raise.** It is the one exit from this module that is not a `tuple[Violation, ...]`.

**Out of scope here, recorded for a later pull request** (both belong to the prompt, not the validator):

- A body containing no number at all can still say anything. Every rule in section 6 constrains figures, structure and characters; none constrains claims.
- Nothing rejects a URL or a phone number in a body.

### 6.2 Amendments after the second adversarial review (2026-09-10)

Four more, three of them in the same shape: a rule that enumerated the cases somebody had thought of where it meant a property or a boundary a reader would recognise.

| What | Superseded because |
|---|---|
| 7 Character class | It enumerated its members. Thirteen invisible characters were reproduced outside the enumeration, each unflagged by the predicate *and* surviving the sanitizer — the tags block above all, which carries printable ASCII invisibly and put a forged `Recomendaciones:` header past an exact-match count that returned one while the reader saw two. Now: general categories `Cc`, `Cf`, `Cs`, `Co`, `Cn`, `Zl`, `Zp`, plus the named invisibles whose category is a visible one. `Mn` and `Zs` are excluded on purpose — a combining tilde is `Mn` and `Ferreñafe` has to keep passing. |
| 6 and 8 Normalization | Rule 4 composed to NFC and nothing else did, so a decomposed unit matched no unit pattern and a decomposed quotation was not a quotation. Both rules read the composed body now; rule 7 deliberately still reads the raw one. |
| 4 Unit resolution | Forward-only, offset-exact and accent-sensitive, so `70 (mm)`, `70-mm`, `70 mms` and a decomposed `70 milímetros` all fell through to the union — the unit-blind set amendment 4 had just removed, reachable through a bracket. Now: a bounded punctuation run that stops at a sentence terminator, folded text, plural abbreviations, and the clause-scoped backward scan below. |
| 6 Backward scan | Bounded by a count of four tokens, which is what one example needed. Ordinary Spanish word order walked past it in three reproduced sentences. Bounded by the clause now — back to the nearest sentence terminator or line break — and the same implementation serves rule 8, which had no backward reading at all. `al menos dos días` stays silent on the line break rather than on a window width. |

**Residuals added, accepted and recorded:**

- **`por ciento` is a unit, not a numeral.** Reading backwards reaches the `ciento` of that phrase where reading forwards never did, so it is skipped when `por` precedes it. A body writing `ciento` as a numeral immediately after the word `por` therefore goes unrefused. It is not a sentence anyone writes.
- **The truncation marker is refused, not asserted.** `domain/template.py` drops a checklist item that sanitizes to `CHECKLIST_TRUNCATED_ES` rather than raising on it. Asserting the marker's position would make the template raise on a reachable configuration, and a fallback that cannot compose leaves nothing to send. The dropped item is a real instruction the community loses; the marker then says truthfully that the list was cut.

**Blocking on the pull request that first wires model output into the cycle**, not on this one, and recorded in full under `tasks.md` → Open Items: no rule governs what the message tells a person to *do*, and the verbatim exemption removes the quoted span from the text every later rule reads — so a hostile aviso title reading `LLAME AL` plus a nine-digit mobile number is accepted when quoted, while the same digits standing alone are rejected. The three parts required there are a closed instruction vocabulary, a flat refusal of contact channels anywhere in the body including inside a verbatim span, and an exemption narrowed to numeric provenance alone.

---

## 7. Testing Strategy

| Layer | What | How |
|---|---|---|
| Domain | Every validator rule, both directions, at its boundary; canonicalization equivalences; the residue rule against the real checklist | `@pytest.mark.parametrize` over frozen case dataclasses, `ids=` carrying spec scenario names. No fakes, no I/O |
| Domain | **V0** — the template always passes the validator | Parametrized over every request fixture |
| Adapter | The 12-row fault-injection table: one alert sent, text byte-identical to the template's, `composer == "template"`, exactly one notice | `AgentBackedComposer` + `FakeAgentInvoker` + `FakeNotifier` |
| Adapter | Accepted path: `composed_by == agent`, zero notices | Same fake, scripted valid response |
| Adapter | Prompt safety: hostile-title fixture from change 1 ⇒ no control character, no newline from the title, no contact data, no threshold value | Pure assertions on `build_prompt` output |
| Adapter | Invoker: timeout, transport error, non-2xx, non-JSON, oversized body ⇒ `AgentInvocationError` with the right reason | Injected fake boto3 client; **no credentials, no network** |
| Application | Deduplicated cycle ⇒ `FakeAgentInvoker.calls == []`; provenance reaches `AlertRecord.composer`; the notice arrives before `send_alert` | `build_fake_deps(composer=...)` + the shared `CallLog` |
| Entrypoints | `--composer` tri-state; the header and `--json` field; preview never invokes the invoker | `capsys` + `--offline-fixtures` |
| Architecture | Both boundary directions (D22) | AST scan, with a triangulation case proving the new scans can fail |
| Contract | Golden `contracts/agent-composition.json` asserted from both units | Two suites, one file |
| `agent/` | Entrypoint returns a JSON-serializable dict; the Pydantic model rejects a missing field; zero tools registered | Its own suite, stubbed model where possible |
| Integration (opt-in) | One real end-to-end invocation | Existing `@pytest.mark.integration`, deselected by default |

`build_fake_deps` gains a `composer=` override so use-case tests can drive the agent composer; the default stays `FakeMessageComposer`, so the 559 existing tests are untouched.

---

## 8. Prompt Injection — Each Non-Capability Mapped to a Mechanism

The property is not "the model cannot be persuaded". It is: whatever a hostile aviso title says, the following hold.

| # | The agent cannot… | Mechanism (code, not prompt wording) |
|---|---|---|
| 1 | cause or prevent a send | Invoked only inside the `if decision.send and not suppress_community_alert` block at `run_alert_cycle.py:176`. Its return value appears in no predicate anywhere |
| 2 | change level, window or `valid_until` | Validator rules 1–2; and `AlertRecord.level`/`.window` are taken from `assessment`, never from the message |
| 3 | introduce a number | Validator rules 8–9 |
| 4 | forge the body's line grammar | `sanitize_source_text` on the way in (change 1 §3.3) + validator rules 6–7 on the way out |
| 5 | reach any side effect | Zero tools registered on the `Agent`; the entrypoint returns a dict; the execution role grants model invocation only (*IAM is change 3's; to verify at apply*) |
| 6 | suppress the alert by failing | D15 — every exit of `compose` returns a message |
| 7 | see a contact, token or account id | `MessageRequest` has no contact field, and `contacts.list_active` is called **after** `composer.compose` (`run_alert_cycle.py:177–178`). **That ordering is now load-bearing for a security property** — a future refactor that hoists the contact read above composition would weaken this silently, so it needs a test that fails on reorder (the shared `CallLog` already makes it assertable) |
| 8 | produce unbounded text | Validator rule 5 in code, never requested in the prompt; plus `MAX_RESPONSE_BYTES` in the invoker, so a multi-megabyte body is rejected before it is parsed |

---

## 9. Cost and Timing

| Question | Answer |
|---|---|
| Prompt size | Instruction block ≈ 1200 characters, JSON payload ≈ 1000–1400 with the five-item checklist ⇒ roughly **600–900 input tokens**. Output: title plus a template-sized body ⇒ roughly **250–350 output tokens** |
| Price per invocation | Claude Haiku 4.5 token price × the above, plus AgentCore Runtime active-CPU-seconds and peak memory for a few seconds. Order of magnitude: fractions of a cent. **The unit prices are to verify at apply** against the two pages design spec §7.1 cites; this document does not assert them from memory |
| Monthly | Alerts fire only on a new or escalated level in a rare-event system. Cents |
| Cold start | Assume **every** invocation is cold: a six-hourly cycle gives no warm reuse. The deadline is sized for the cold case. Measured cold-start latency is **to verify at first live run** and is the input that would move D19's number |
| Deadline tolerated | 10 s, single attempt (D19). Nobody waits — it is a scheduled job — so the deadline can be generous, and exceeding it must still be cheap, which is why there is no retry |
| Deduplicated cycle | **Zero invocations, zero cost.** Structural, not conventional: composition sits inside the send branch (`run_alert_cycle.py:176`) and the CLI preview calls the template directly (D11). Asserted as `FakeAgentInvoker.calls == []` |

---

## 10. File Changes

| Path | Action | Slice |
|---|---|---|
| `src/rain_alert/domain/values.py` | Modify | 1 — `ComposerName`, `NoticeKind.AGENT_FALLBACK_USED` |
| `src/rain_alert/domain/messages.py` | Modify | 1 — `AlertMessage.composed_by` |
| `src/rain_alert/domain/message_validation.py` | Create | 1 — D16, D17 |
| `src/rain_alert/domain/sanitize.py` | Modify | 1 — `has_unsafe_characters` predicate |
| `src/rain_alert/adapters/agent_composer.py` | Create | 1 — D15, D21 |
| `src/rain_alert/adapters/agent_invoker.py` | Create | 1 — the `AgentInvoker` Protocol and `AgentInvocationError` only |
| `src/rain_alert/adapters/serialization.py` | Modify | 1 — restore `composed_by` on read; document unchanged |
| `src/rain_alert/application/run_alert_cycle.py` | Modify | 1 — one line: real provenance; `CycleResult.notices` docstring |
| `tests/support/fakes.py`, `tests/support/wiring.py` | Modify | 1 — `FakeAgentInvoker`, `composer=` override |
| `tests/unit/domain/test_message_validation.py`, `tests/unit/adapters/test_agent_composer.py` | Create | 1 |
| `agent/pyproject.toml`, `agent/app.py`, `agent/models.py`, `agent/tests/**` | Create | 2 |
| `src/rain_alert/adapters/agent_prompt.py` | Create | 2 — shared subject of the golden contract |
| `contracts/agent-composition.json` | Create | 2 |
| `tests/architecture/test_layer_boundaries.py` | Modify | 2 — both directions, D22 |
| `src/rain_alert/adapters/agentcore_invoker.py` | Create | 3 — boto3, D18, D19 |
| `src/rain_alert/domain/config.py` | Modify | 3 — `AlertConfig.composer` |
| `src/rain_alert/adapters/local/static_config_repository.py` | Modify | 3 — the switch's default and its env override |
| `src/rain_alert/entrypoints/wiring.py`, `entrypoints/cli.py` | Modify | 3 — `select_composer`, `--composer`, output |
| `tests/integration/test_agent_live.py` | Create | 3 — opt-in marker |
| `docs/` deploy runbook | Create | 3 — AgentCore CLI path (design 3.7) |

Nothing in `openspec/specs/` or `docs/` is touched during planning; the runbook lands in apply.

---

## 11. Slice Boundaries

Matches the proposal's three. Dependencies are linear (1 → 2 → 3) and each slice is independently revertable. `sdd-tasks` owns the final forecast and the chain decision; `state.yaml` records `stacked-to-main`.

| Slice | Content | Est. lines | Autonomous verification | Rollback |
|---|---|---|---|---|
| 1 — validator and fallback | `ComposerName`, `composed_by`, the notice kind, the pure validator, `AgentBackedComposer` against `FakeAgentInvoker`, the fallback notice, real provenance. **No AWS, no Strands, no prompt** | 550–800 | The full 12-row fault table green; V0 green; dedup ⇒ zero invocations; mutation run recorded | Revert. `main`'s behaviour is unchanged either way — nothing constructs the agent composer yet |
| 2 — agent deployment unit | `agent/` project, Pydantic output model, `BedrockModel`, `BedrockAgentCoreApp` entrypoint, `build_prompt`, the golden contract, both architecture tests | 400–650 | Prompt-safety tests green; both boundary tests green with triangulation; `agent/` suite green | Delete `agent/` and the prompt module. Slice 1 stands and still ships the template |
| 3 — invocation and runbook | `BedrockAgentCoreInvoker`, `AlertConfig.composer`, `select_composer`, CLI `--composer`, deploy runbook, opt-in live test | 400–600 | `uv run pytest` green with no credentials present; one live run recorded in the change artifacts | Set the switch to `template` — no revert needed. Then revert the adapter if desired |

Slice 1 is the one that must not be rushed: it is where the headline property is proven, and it is fully testable before a single AWS call exists.

---

## 12. Migration / Rollout

No data migration. `AlertRecord.composer` already exists on merged records and both values stay readable; the persisted document shape does not change (D13). Rollout is the switch: the change ships defaulting to `template`, so merging it changes nothing observable. Rollback is the four levels in the proposal, cheapest first, and level 1 — flip the configuration — needs no deploy.

---

## 13. Open Questions

- [ ] **`MAX_BODY_LENGTH` / `MAX_TITLE_LENGTH` exact values.** Design floor: strictly above the longest real template body (V0 enforces it). Proposed 1500 / 200. Spec to pin.
- [ ] **The allowed set's exact membership** (`state.yaml → open_decisions.number-definition`) is the spec's. This document pins extraction, residue, canonicalization and comparison; if the spec narrows the set, only the set-building function changes.
- [ ] **`valid_until` mismatch: reject or overwrite?** Design position: reject (rule 2). Spec may overrule; overwriting is strictly more permissive.
- [ ] **Whether `CycleResult.notices` should eventually carry composer notices too.** Deferred: it would need the composer's notices to travel back through the port, which is the wrapper D13 rejected. Revisit only if change 3 needs it.

### To verify at apply

Nothing below is asserted here. Each names the check that must run before code depends on it.

1. **Bedrock model id for Claude Haiku 4.5** — resolve against live AWS documentation at apply (`state.yaml → open_decisions.bedrock-model-id`). Not guessed here.
2. **AgentCore data-plane invocation** — the boto3 client name, the operation, the request/response shape, and whether the payload streams (D18). The streaming answer decides whether D19 needs an explicit monotonic deadline.
3. **botocore retry-disabling key** for the pinned version (`max_attempts` vs `total_max_attempts`, D19).
4. **Exact import paths** in the `agent/` unit — the modules exporting `Agent`, `BedrockModel` and `BedrockAgentCoreApp`, and the package names to pin in `agent/pyproject.toml`. The API surface (`structured_output`, `@app.entrypoint`, `BedrockModel(model_id=…, temperature=…, top_p=…)`) was verified 2026-09-09; the import paths were not.
5. **Bedrock and AgentCore unit prices** — against the two pages design spec §7.1 cites, or the AWS Pricing API. §9 gives token counts only.
6. **Measured cold-start latency** at the first live run; it is the input that would move the 10 s deadline.
7. **The `uv` invocation** for the nested `agent/` project, and that root `uv run pytest` does not collect it.
8. **The AgentCore execution role's permissions** (non-capability 5). IAM belongs to change 3; this change must not assume it.
