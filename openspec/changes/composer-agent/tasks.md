# Tasks: Composer Agent (Strands on AgentCore, with Deterministic Fallback)

Three phases, one per design §11 slice. Dependencies are linear (1 → 2 → 3); slice 1 must not be rushed — it proves the headline property before a single AWS call exists. Strict TDD is ON: `[RED]` names the test and the failure it expects and why; `[GREEN]` is the minimal implementation that turns it green. `[Network]` marks anything requiring live AWS/documentation access.

## Review Workload Forecast

Estimated changed lines: 2100–3400 (pessimistic; see per-slice drivers below)
400-line budget risk: High
Chained PRs recommended: Yes
Decision needed before apply: Yes — `delivery_strategy: ask-on-risk`. `chain_strategy: stacked-to-main` is already selected (state.yaml), so the only remaining decision is whether the owner accepts this forecast or wants a finer split before Phase 1 apply begins.

**Why pessimistic, and what drove each number** — change 1's slices 2 and 3 overran their estimates by 5–10x (planned ~400 → landed 2356; planned ~450 → landed 4507). This plan is more granular than change 1's, which itself inflates the count:

| Slice | Design estimate | This forecast | Driver |
|---|---|---|---|
| 1 | 550–800 | 900–1400 | 9 validator rules individually parametrized, the invariant V0 test, 12 separately-authored fault-table rows (not one parametrize block), the mutation-proof procedure recorded in both directions, plus `values.py`/`messages.py`/`serialization.py`/`run_alert_cycle.py` touches. Change 1's comparable domain+adapter slice (risk/dedup/outage + template) landed at 2356. |
| 2 | 400–650 | 700–1100 | Two full test suites (`src/` and `agent/`) for one golden contract, architecture tests in both directions each with a triangulation case (change 1's single-direction version already needed one), plus a second `uv` project's lockfile in the diff. |
| 3 | 400–600 | 500–900 | boto3 invoker with 5 injected-fault cases, config/wiring/CLI plumbing across 4 files, a runbook, and one opt-in live test — no equivalent unplanned-scope risk to slice 3's change-1 sibling, so held closer to the design number. |

### Suggested Work Units

| Unit | Goal | PR | Notes |
|---|---|---|---|
| 1 | Validator, fallback composer, provenance, notice — against `FakeAgentInvoker` | PR 1 | Tasks 1.1–1.46. No AWS, no Strands, no prompt. Independent; `main`'s behavior is unchanged until something constructs the agent composer. |
| 2 | `agent/` deployment unit + prompt + golden contract + boundary tests | PR 2 | Tasks 2.1–2.23. Depends on PR 1's `AgentInvoker` protocol and validator. |
| 3 | Real invocation, config, CLI opt-in, runbook, opt-in live test | PR 3 | Tasks 3.1–3.20. Depends on PR 2's `agent/` unit being deployable. |

Stacked-to-main: each PR merges to `main` in order once its own gate is green.

---

## Phase 1: Validator, Fallback, Provenance, Notice (PR 1, ~900–1400 lines) 📍

```
PR 1 (this slice) → PR 2 (agent/ unit) → PR 3 (invocation + runbook)
```

### Provenance plumbing

- [ ] 1.1 [RED] `tests/unit/domain/test_values.py` — assert `ComposerName.TEMPLATE`/`.AGENT` exist and `NoticeKind.AGENT_FALLBACK_USED == "agent_fallback_used"`. Expects `AttributeError`: neither exists yet. Design D13, D21.
- [ ] 1.2 [GREEN] `domain/values.py` — add `ComposerName(StrEnum)` and `NoticeKind.AGENT_FALLBACK_USED`. Verify: `uv run pytest tests/unit/domain/test_values.py -q`
- [ ] 1.3 [RED] `tests/unit/domain/test_messages.py` — `AlertMessage` accepts `composed_by`, defaults to `ComposerName.TEMPLATE`; equality now includes it. Expects `TypeError`: no such field. Design D13, design §3 breaking-change table.
- [ ] 1.4 [GREEN] `domain/messages.py` — add `composed_by: ComposerName = ComposerName.TEMPLATE` (additive, frozen dataclass).
- [ ] 1.5 [RED] `tests/unit/adapters/test_serialization.py` — `alert_record_from_dict` restores `composed_by` from `document["composer"]` for both `"template"` and `"agent"` records. Expects: restored message always defaults to `TEMPLATE` regardless of the stored value. Design D13.
- [ ] 1.6 [GREEN] `adapters/serialization.py` — read `composed_by=ComposerName(document["composer"])` in `alert_record_from_dict`; document shape unchanged. Verify: `uv run pytest tests/unit/adapters/test_serialization.py -q`

### The validator (domain/message_validation.py)

- [ ] 1.7 [RED] `tests/unit/domain/test_message_validation.py` — parametrized cases for rules 1–7 (`LEVEL_MISMATCH`, `VALID_UNTIL_MISMATCH`, `EMPTY_TITLE`/`EMPTY_BODY`, `CITY_MISSING` with NFC normalization, `TITLE_TOO_LONG`/`BODY_TOO_LONG`, `FORGED_SECTION_HEADER`, `UNSAFE_CHARACTER`), `ids=` naming each spec scenario. Expects `ModuleNotFoundError`: module does not exist. Design D16, §6 rules 1–7; spec "Accepted agent messages satisfy the same structural invariants", "A disagreeing valid_until is rejected", "Body over the length cap", "Body contains a forged section header".
- [ ] 1.8 [GREEN] `domain/message_validation.py` — `validate_message(request, candidate) -> tuple[Violation, ...]`, `ValidationRule(StrEnum)`, rules 1–7. `MAX_BODY_LENGTH = 1500` per the spec's 2026-09-09 pin (see Open Items — the design only proposed this number). Verify: `uv run pytest tests/unit/domain/test_message_validation.py -q`
- [ ] 1.9 [RED] `tests/unit/domain/test_sanitize.py` — `has_unsafe_characters(value)` predicate reuses the existing `_REMOVED` character class. Expects `AttributeError`: predicate does not exist. Design §6 rule 7.
- [ ] 1.10 [GREEN] `domain/sanitize.py` — add `has_unsafe_characters`. Verify: `uv run pytest tests/unit/domain/test_sanitize.py -q`
- [ ] 1.11 [RED] `tests/unit/domain/test_message_validation.py` — rule 8 (`UNKNOWN_NUMBER`): Spanish decimal comma accepted, window time/date accepted, horizon constant `24` accepted, digit inside a verbatim-quoted title accepted, digit inside a verbatim-quoted checklist item accepted, an invented number ("80") rejected. Expects: without the residue/canonicalization/verbatim-exemption logic, either the invented number passes uncaught or a legitimate quoted digit is wrongly rejected. Design D17, §6 rule 8; spec "Every number in the body is traceable to MessageRequest" (all 7 scenarios).
- [ ] 1.12 [GREEN] `domain/message_validation.py` — implement rule 8: residue construction (strip checklist items, sanitized title, city, timezone, longest-match-first), digit-token extraction, `Decimal` canonicalization, allowed-set construction from `MessageRequest`, verbatim-quote exemption. Verify: `uv run pytest tests/unit/domain/test_message_validation.py -k number -q`
- [ ] 1.13 [RED] `tests/unit/domain/test_message_validation.py` — rule 9 (`WORD_NUMBER`) per the **spec's unit-conditioned wording**: "ochenta milímetros" rejected, "setenta por ciento" rejected, the checklist's "al menos dos días" NOT rejected, "una linterna"/"un botiquín" NOT rejected. Expects: an unconditioned implementation (design D17.3's original wording) would wrongly flag "dos", "un", "una" here — this RED task specifically asserts they must NOT raise. Spec "A measurement written in words is rejected" (amended 2026-09-09 — see Open Items).
- [ ] 1.14 [GREEN] `domain/message_validation.py` — rule 9 matches a Spanish numeral lexicon only when it directly quantifies mm/percentage/hour. Verify: `uv run pytest tests/unit/domain/test_message_validation.py -k word -q`
- [ ] 1.15 [RED] `tests/unit/domain/test_message_validation.py::test_v0_template_always_passes_validator` — parametrized over every `MessageRequest` fixture in the suite: `validate_message(request, template.compose(request)) == ()`. Expects: fails if any rule (especially 6, 8, 9) over-rejects the template's own checklist wording or its two section headers before the residue/unit-conditioning fixes above are in place. Design §6 "Invariant V0"; proposal success criterion "The accepted agent body satisfies the same structural invariants as the template body".
- [ ] 1.16 [GREEN] Confirm the full rule set (1.8–1.14) makes V0 pass for every fixture; adjust residue/exemption logic if any fixture fails. Record the exact fixture list and `pytest -v` output in apply-progress. Verify: `uv run pytest tests/unit/domain/test_message_validation.py -k v0 -v`

### The invocation seam and fakes

- [ ] 1.17 [RED] `tests/unit/adapters/test_agent_invoker.py` — `AgentInvoker` is a `Protocol` with `invoke(prompt: str) -> Mapping[str, Any]`; `AgentInvocationError(reason, detail)` constructs and carries both. Expects `ModuleNotFoundError`. Design D18.
- [ ] 1.18 [GREEN] `adapters/agent_invoker.py` — define both. Verify: `uv run pytest tests/unit/adapters/test_agent_invoker.py -q`
- [ ] 1.19 [RED] `tests/support/fakes.py` — `FakeAgentInvoker` (scripted response or scripted exception, `calls` list, no `unittest.mock`) exists and conforms to `AgentInvoker`. Expects `ImportError`.
- [ ] 1.20 [GREEN] `tests/support/fakes.py`, `tests/support/wiring.py` — implement `FakeAgentInvoker`; `build_fake_deps` gains a `composer=` override (default stays `FakeMessageComposer`, so the 559 existing tests stay untouched). Verify: `uv run pytest tests/unit -q`

### The 12-row fault-injection table (design D15, spec "Every composer failure mode falls back to the template" — each row is its own task, not collapsed)

- [ ] 1.21 [RED] Row 1/12 — deadline exceeded: `FakeAgentInvoker` raises `AgentInvocationError(TIMEOUT, ...)`. Expects `ModuleNotFoundError`: `adapters/agent_composer.py` does not exist yet.
- [ ] 1.22 [RED] Row 2/12 — transport error / connection refused: raises `AgentInvocationError(TRANSPORT_ERROR, ...)`. Same expected failure as 1.21 until the module exists.
- [ ] 1.23 [RED] Row 3/12 — non-2xx response: raises `AgentInvocationError(BAD_STATUS, ...)`.
- [ ] 1.24 [RED] Row 4/12 — non-JSON body: raises `AgentInvocationError(MALFORMED_PAYLOAD, ...)`.
- [ ] 1.25 [RED] Row 12/12 — invoker raises an unexpected exception type: `FakeAgentInvoker.invoke` raises a bare `RuntimeError`, not `AgentInvocationError`.
- [ ] 1.26 [GREEN] `adapters/agent_composer.py` — `AgentBackedComposer.compose`: compute `fallback = self._template.compose(request)` first (unconditionally), `try: draft = self._invoker.invoke(...)`, `except AgentInvocationError` and `except Exception` both route to `_fell_back(...)` and return `fallback`. Makes rows 1, 2, 3, 4, 12 pass — 5 of 12. Design D15. Verify: `uv run pytest tests/unit/adapters/test_agent_composer.py -k "deadline or transport or bad_status or malformed or unexpected" -q`
- [ ] 1.27 [RED] Row 5/12 — JSON missing a required field: `FakeAgentInvoker` returns a decoded dict without `body`. Expects: no shape check exists, candidate wrongly proceeds to acceptance.
- [ ] 1.28 [RED] Row 6/12 — level disagreeing with the evaluated level: well-shaped candidate, `level != request.level`. Expects: `validate_message` is not yet wired into `compose`, so the mismatch is never checked.
- [ ] 1.29 [RED] Row 7/12 — body omitting the city.
- [ ] 1.30 [RED] Row 8/12 — body over the length cap (>1500 chars).
- [ ] 1.31 [RED] Row 9/12 — body containing a number absent from the input ("se esperan 80 mm").
- [ ] 1.32 [RED] Row 10/12 — body containing a newline-forged second `Recomendaciones:` header.
- [ ] 1.33 [RED] Row 11/12 — empty body.
- [ ] 1.34 [GREEN] `adapters/agent_composer.py::parse_candidate(draft, request) -> AlertMessage | None` (returns `None` on a missing/malformed field, closing row 5) + wire `validate_message(request, candidate)` into `compose`: any violation → `_fell_back(VALIDATION_FAILED, ...)`; no violation → `replace(candidate, composed_by=ComposerName.AGENT)`. Closes rows 6–11 and the accepted path. Design D15, D16. Verify: `uv run pytest tests/unit/adapters/test_agent_composer.py -q` (all 12 rows green; each asserts an alert was sent, its text is byte-identical to the template's output for the same `MessageRequest`, and `composer == "template"`)
- [ ] 1.35 [RED] Accepted path: scripted valid `FakeAgentInvoker` response passing every validator rule. Expects: `composed_by == ComposerName.TEMPLATE` still, because 1.34's `replace(...)` branch is unexercised until this test drives it.
- [ ] 1.36 [GREEN] Confirm 1.34 already satisfies this (no new production code expected); if not, fix the happy-path branch. Verify: `uv run pytest tests/unit/adapters/test_agent_composer.py -k accepted -q`

### Fallback notice (design D21)

- [ ] 1.37 [RED] `tests/unit/adapters/test_agent_composer.py` — with a `FakeNotifier`, `AgentBackedComposer` sends exactly one `OperatorNotice(kind=AGENT_FALLBACK_USED)` before returning `fallback`; zero notices on acceptance. Expects: `FakeNotifier.calls == []` — no notice is wired yet.
- [ ] 1.38 [GREEN] `adapters/agent_composer.py` — inject `Notifier` + clock; `_fell_back` calls `notifier.send_operator_notice(...)` before returning. Verify: `uv run pytest tests/unit/adapters/test_agent_composer.py -k notice -q`

### Mutation proof (proposal success criterion 2 — its own task, evidence recorded in both directions)

- [ ] 1.39 Manually delete the `except AgentInvocationError` / `except Exception` clauses (and separately, the `if violations:` guard) in `adapters/agent_composer.py`; re-run the 12-row table. Expect and record: the corresponding rows now raise out of `compose`/`RunAlertCycle.execute`/the test instead of falling back. Paste the failing `pytest` output into apply-progress.
- [ ] 1.40 Revert the mutation; re-run the 12-row table; record the all-green `pytest` output. Design D15 "Mutation proof"; proposal success criterion "Removing the fallback except turns those rows red."

### Wiring into RunAlertCycle

- [ ] 1.41 [RED] `tests/unit/application/test_run_alert_cycle.py` — with `build_fake_deps(composer=<agent fake>)`: `AlertRecord.composer` reads `"agent"` when accepted, `"template"` on fallback. Expects: `run_alert_cycle.py:189` still hardcodes `composer="template"`, so an accepted agent message is misrecorded.
- [ ] 1.42 [GREEN] `application/run_alert_cycle.py` — replace the hardcoded literal with `composer=message.composed_by.value`; amend `CycleResult.notices`' docstring to state it carries outage notices only (D21). Verify: `uv run pytest tests/unit/application/test_run_alert_cycle.py -q`
- [ ] 1.43 [RED] `tests/unit/application/test_run_alert_cycle.py::test_deduplicated_cycle_invokes_agent_seam_zero_times` — dedup policy denies the send; assert `FakeAgentInvoker.calls == []`. Expects: fails if any code path calls `composer.compose` before the authorized-send branch. Spec "Deduplicated cycle invokes nothing"; proposal success criterion "A deduplicated cycle invokes the agent seam zero times."
- [ ] 1.44 [GREEN] Confirm `run_alert_cycle.py` already scopes composition inside the `if decision.send and not suppress_community_alert` block (design §8 row 1); this task exists to pin the property with a test, not to change behavior. Verify: `uv run pytest tests/unit/application/test_run_alert_cycle.py -k zero -q`

### Close-out

- [ ] 1.45 **Security review** (fresh context) on the PR-1 branch diff before opening the PR — per project practice (state.yaml `security_review`). Scope: new `AlertMessage` field and equality change, `serialization.py` round-trip, the fallback notice path. No prompt/LLM surface exists yet in this slice.
- [ ] 1.46 PR-1 verification gate: `uv run pytest tests/unit/domain tests/unit/adapters tests/unit/application -q`, `uv run ruff check`, `uv run mypy src`. Confirm proposal success criteria for the 12-row table, mutation proof, and zero-invocation are all checked.

---

## Phase 2: The `agent/` Deployment Unit (PR 2, ~700–1100 lines) 📍

```
PR 1 (merged) → PR 2 (this slice) → PR 3 (invocation + runbook)
```

- [ ] 2.1 [Network] Scaffold `agent/pyproject.toml` + lockfile (deps: `strands-agents`, `bedrock-agentcore`, `pydantic`); confirm root `[tool.pytest.ini_options] testpaths=["tests"]` does not collect `agent/`. Resolves design §13 to-verify #7 (the `uv` invocation for the nested project). Design D22.
- [ ] 2.2 [Network] Verify exact import paths for `Agent`, `BedrockModel`, `BedrockAgentCoreApp` against current Strands/AgentCore documentation. **Wrong guess becomes an implementation bug** — this is one of the two highest-risk to-verify items. Design §13 to-verify #4 (the API surface itself — `structured_output`, `@app.entrypoint`, `BedrockModel(model_id=…, temperature=…, top_p=…)` — was already verified 2026-09-09; only import paths remain). Record the exact `from ... import ...` lines and pinned versions in apply-progress.
- [ ] 2.3 [Network] Resolve the Bedrock model id string for Claude Haiku 4.5 against live AWS documentation. **The second highest-risk to-verify item** — a wrong id fails every live call. Design §13 to-verify #1; state.yaml `open_decisions.bedrock-model-id`. Record the exact id string used.
- [ ] 2.4 [RED] `agent/tests/test_models.py` — `CompositionOutput` (Pydantic) rejects a payload missing `title`, `body`, `level`, or `valid_until`. Expects `ModuleNotFoundError`: `agent/models.py` does not exist. Design 7.4.
- [ ] 2.5 [GREEN] `agent/models.py` — `CompositionOutput(BaseModel)` per the output contract. Verify: `cd agent && uv run pytest tests/test_models.py -q`
- [ ] 2.6 [RED] `tests/unit/adapters/test_agent_prompt.py` — `build_prompt(request)` is pure; every data value in the output traces to a `MessageRequest` field; no contact/token/account id present. Expects `ModuleNotFoundError`: `agent_prompt.py` does not exist. Spec "Prompt fields are a subset of MessageRequest", "Recipient data never reaches the prompt".
- [ ] 2.7 [GREEN] `src/rain_alert/adapters/agent_prompt.py` — instruction block + `<datos>` JSON fence carrying city/timezone/level/window/reasons/title/forecast/source statuses/checklist; omits `probability_threshold_pct`. Design D20.
- [ ] 2.8 [RED] `tests/unit/adapters/test_agent_prompt.py` — the change-1 hostile-title fixture produces a prompt with no control character and no newline originating from the title. Expects: raw title leaks control chars/newlines because `sanitize_source_text` is not yet called. Spec "Hostile title is sanitized in the prompt".
- [ ] 2.9 [GREEN] `agent_prompt.py` — call `sanitize_source_text` on the warning title and every reason title before interpolation (sanitize first, encode second, per D20 and the port docstring). Verify: `uv run pytest tests/unit/adapters/test_agent_prompt.py -q`
- [ ] 2.10 [RED] Checklist item and city text appear unmodified by `sanitize_source_text`. Spec "Operator-owned text is not sanitized". Expects: fails only if 2.9 over-applied sanitization to operator-owned fields.
- [ ] 2.11 [GREEN] Confirm scope of sanitization is title/reason-titles only. Verify: `uv run pytest tests/unit/adapters/test_agent_prompt.py -q`
- [ ] 2.12 [RED] Prompt contains an explicit instruction to render every quantity in digits. Spec "The prompt instructs digit rendering". Expects: instruction sentence absent.
- [ ] 2.13 [GREEN] Add the digit-rendering sentence to the fixed instruction block. Verify: `uv run pytest tests/unit/adapters/test_agent_prompt.py -q`
- [ ] 2.14 [RED] `tests/unit/adapters/test_agent_prompt.py` + `agent/tests/test_models.py` — `build_prompt`'s payload carries exactly the golden input keys, and `CompositionOutput.model_json_schema()` covers exactly the golden output keys, both against `contracts/agent-composition.json`. Expects `FileNotFoundError`: contract file does not exist.
- [ ] 2.15 [GREEN] `contracts/agent-composition.json` — the golden document (input keys from the prompt payload; output keys `title`/`body`/`level`/`valid_until`). Design D22. Verify: `uv run pytest tests/unit/adapters/test_agent_prompt.py -k contract -q && (cd agent && uv run pytest -k contract -q)`
- [ ] 2.16 [RED] `tests/architecture/test_layer_boundaries.py::test_src_package_never_imports_the_agent_deployment_unit` — scans all of `src/rain_alert` for `agent`, `pydantic`, `strands`, `bedrock_agentcore` imports; includes a synthetic-violation triangulation case (a planted forbidden import) proving the scanner detects it, not just that a clean tree passes. Expects: scanner does not exist. Design D22 table row 1.
- [ ] 2.17 [GREEN] Extend the forbidden-roots constants and implement the scan. Verify: `uv run pytest tests/architecture -q`
- [ ] 2.18 [RED] `tests/architecture/test_layer_boundaries.py::test_agent_unit_never_imports_the_application_package` — scans `agent/` for `rain_alert`/`src` imports, with its own triangulation case. Design D22 table row 2.
- [ ] 2.19 [GREEN] Implement the `agent/`-side scan. Verify: `uv run pytest tests/architecture -q`
- [ ] 2.20 [RED] `agent/tests/test_app.py` — the `@app.entrypoint` returns a JSON-serializable dict; the `Agent` has zero tools registered. Expects `ModuleNotFoundError`: `agent/app.py` does not exist.
- [ ] 2.21 [GREEN] `agent/app.py` — `BedrockAgentCoreApp`, `@app.entrypoint` calling `agent.structured_output(CompositionOutput, prompt)` on `Agent(model=BedrockModel(model_id=<2.3>, temperature=…, top_p=…), tools=[])`; entrypoint returns `output.model_dump()`. Design 7.1, 7.4, D22. Verify: `cd agent && uv run pytest -q`
- [ ] 2.22 **Security review** (fresh context) on the PR-2 branch diff — focused on the prompt-injection surface (`build_prompt`, the sanitize boundary) and the new `pydantic`/`strands` dependency footprint.
- [ ] 2.23 PR-2 verification gate: `uv run pytest tests/unit/adapters/test_agent_prompt.py tests/architecture -q`, `(cd agent && uv run pytest -q)`, `uv run ruff check`. Confirm root `uv run pytest` still collects zero `agent/` tests and `agent/`'s suite is stubbed-model only.

---

## Phase 3: Invocation, Configuration, CLI Opt-In, Runbook (PR 3, ~500–900 lines) 📍

```
PR 1 (merged) → PR 2 (merged) → PR 3 (this slice)
```

- [ ] 3.1 [Network] Resolve the AgentCore data-plane invocation shape: boto3 client name, operation, request/response shape, and whether the payload streams. **The other highest-risk to-verify item** alongside 2.3 — a wrong guess about streaming means D19's deadline mechanism is wrong. Design §13 to-verify #2, D18.
- [ ] 3.2 [Network] Resolve the retry-disabling key for the pinned `botocore` (`max_attempts` vs `total_max_attempts`). Design §13 to-verify #3, D19.
- [ ] 3.3 [Network] Confirm Bedrock + AgentCore unit prices against current pricing (AWS Pricing API or the cited documentation pages). Design §13 to-verify #5.
- [ ] 3.4 [Network] Confirm the AgentCore execution role's permission surface is out of this change's scope (IAM belongs to change 3); record that no IAM resource is created here. Design §13 to-verify #8.
- [ ] 3.5 [Network, opt-in] Measure cold-start latency at the first live run. Design §13 to-verify #6 — this item can only close via task 3.16's live run, not before.
- [ ] 3.6 [RED] `tests/unit/adapters/test_agentcore_invoker.py` — with an injected fake boto3 client: timeout, transport error, non-2xx, non-JSON, oversized body each raise `AgentInvocationError` with the matching `UnavailableReason`. Expects `ModuleNotFoundError`: `agentcore_invoker.py` does not exist. No credentials, no network. Design D18, D19.
- [ ] 3.7 [GREEN] `src/rain_alert/adapters/agentcore_invoker.py` — `BedrockAgentCoreInvoker(client: Any | None = None)`, lazily built on first `invoke`; `Config(connect_timeout=3, read_timeout=10, retries=...)` per 3.2's resolved key; `MAX_RESPONSE_BYTES` cap. Verify: `uv run pytest tests/unit/adapters/test_agentcore_invoker.py -q`
- [ ] 3.8 [RED] `tests/unit/domain/test_config.py` — `AlertConfig.composer: ComposerName = TEMPLATE`. Expects `AttributeError`.
- [ ] 3.9 [GREEN] `domain/config.py` — add the field. Design D23.
- [ ] 3.10 [RED] `tests/unit/adapters/test_static_config_repository.py` — `StaticConfigRepository` reads `composer` from `RAIN_ALERT_COMPOSER` (default `template`); `AgentRuntimeSettings(endpoint, timeout_seconds, region)` built from `RAIN_ALERT_AGENT_RUNTIME_ARN`/`RAIN_ALERT_AGENT_TIMEOUT_S`. Expects: env vars ignored, always returns `TEMPLATE`.
- [ ] 3.11 [GREEN] `adapters/local/static_config_repository.py` — wire the switch and its env override. Design D23. Verify: `uv run pytest tests/unit/adapters/test_static_config_repository.py -q`
- [ ] 3.12 [RED] `tests/unit/entrypoints/test_wiring.py` — `select_composer(config, ...)` (a two-branch `if`) returns `domain.template.MessageComposer` for `TEMPLATE`, a fully-wired `AgentBackedComposer` for `AGENT`. Expects `AttributeError`: `select_composer` does not exist. Design D14.
- [ ] 3.13 [GREEN] `entrypoints/wiring.py` — implement `select_composer`; `build_local_deps` calls it once. Verify: `uv run pytest tests/unit/entrypoints/test_wiring.py -q`
- [ ] 3.14 [RED] `tests/unit/entrypoints/test_cli.py` — `--composer {template,agent}` tri-state, absent = `config.composer`; header becomes `SENT [composer: agent]` / `PREVIEW (NOT SENT) [composer: template]`; `--json` gains `message.composer`; the preview never invokes the invoker even under `--composer agent`. Expects: flag does not exist. Design D24.
- [ ] 3.15 [GREEN] `entrypoints/cli.py` — add the flag and output changes; confirm the preview path still calls the template directly (`cli.py:164`/`208`, D11/D24 unchanged). Verify: `uv run pytest tests/unit/entrypoints/test_cli.py -q`
- [ ] 3.16 [Network, opt-in, deselected by default] `tests/integration/test_agent_live.py` under `@pytest.mark.integration` — one real end-to-end invocation against the deployed runtime; record the model id (3.3), measured cold-start latency (closes 3.5), and the produced message in apply-progress for the owner to judge. Proposal success criterion "The agent runs live at least once." Verify manually: `uv run pytest -m integration -k agent_live`
- [ ] 3.17 Default-suite offline guarantee: run `uv run pytest` (no `-m` flag) with AWS credential env vars unset and confirm every test still passes — zero network, zero model, zero credentials. Spec "Default suite run is offline". Verify: `env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN uv run pytest -q`
- [ ] 3.18 Write the AgentCore CLI deploy runbook under `docs/` (design 3.7, documentation only, not automation). Resolves state.yaml `open_decisions.runbook-location`. This is the only place this change touches `docs/`; not done during planning, per design §10's file table note.
- [ ] 3.19 **Security review** (fresh context) on the PR-3 branch diff — focused on the new AWS credential/ARN surface read from environment (no secrets committed), and the CLI's opt-in flag default.
- [ ] 3.20 PR-3 / final verification gate: `uv run pytest -q` (offline default, no credentials present), `uv run ruff check`, `uv run mypy src`, `git grep` secrets scan. Confirm every proposal Success Criteria row is checked, including the live-run criterion via 3.16's recorded evidence.

---

## Open Items for Apply

Not re-decided here, per scope. Flagged because design and spec disagree, or because the design left something open that apply must pin.

- **Design vs spec contradiction — the word-number rule.** Design D17.3 and §6 rule 9 state the rule unconditionally: "A Spanish number word surviving in the residue is a violation regardless of its value" / "Any hit is a violation." The spec was amended 2026-09-09, *after* the design was written, to reject a word-number only when it quantifies a unit the system reports (mm, %, hours) — because the unconditioned rule rejects the deterministic template's own checklist wording ("al menos dos días", "un botiquín", "una linterna"), which would have failed invariant V0 on the first run. Tasks 1.13/1.14 implement the spec's narrowed version. The design document itself is not amended by this tasks phase.
- **Design vs spec gap — `MAX_TITLE_LENGTH`.** Design §13 proposes 200 as an open question ("spec to pin"). The spec's 2026-09-09 amendment pins only the body cap at 1500 and does not state a title cap number. Task 1.8 ships `MAX_TITLE_LENGTH = 200` from the design's proposal since nothing supersedes it, but this should be confirmed explicitly at apply rather than assumed silently.
- **Design's `contracts/agent-composition.json` root-level path** (§2 D22) — confirm at apply that this is not `openspec/` or `docs/` content and is fine to create outside `src/`/`agent/`, as design already states.

---

## Notes on Rules Applied

- `openspec/config.yaml → rules.tasks`: grouped by phase, hierarchical numbering, tooling scaffold (`agent/pyproject.toml`, task 2.1) is its own early task within the phase that introduces it — change 1 already scaffolded the root `src/` tooling.
- Strict TDD: every behavior change is RED before GREEN; RED tasks name the exact expected failure.
- `strands`/`bedrock_agentcore`/`pydantic` never appear under `src/` (enforced 2.16–2.19); nothing under `src/` imports `agent/` (same tasks).
