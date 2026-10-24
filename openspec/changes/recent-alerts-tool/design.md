# Design: Recent Alerts Tool (agent continuity)

## Technical Approach

The application binds the history context. The model only chooses `n`. The app builds a `ContinuitySummary` from DynamoDB inside the authorized-send branch. `AgentComposer` sends `{prompt, history}`. The agent builds one `Agent` per request, with one closure-bound tool `get_recent_alerts(n)`, and calls `agent(prompt, structured_output_model=CompositionOutput)`. This is the event-loop path; it is verified in strands 1.55.1 at `event_loop.py:369-389`. The prompt instructs the model to call the tool first; the loop does not enforce it, and skipping it only loses continuity. The validator accepts continuity figures only when they come from the summary. The facts are computed twice, once per side of the import boundary, and a shared contract fixture pins both. The LLM still runs only after `AlertPolicy` has authorized the send.

## Architecture Decisions

| # | Decision | Rejected | Rationale |
|---|---|---|---|
| D1 | The partition goes in the payload: `history: {partition, as_of, current_level, current_warning_level}` next to `prompt`. Add `history_key` to `composer-fence.json` (schema_version 2) | Runtime env var | An env var on the shared runtime would point production reads at the demo partition. The payload is written by an IAM-authenticated invoker, never by the model. `as_of` makes demo replays deterministic. If the field is missing or invalid, history is empty and composition is never refused |
| D2 | Two implementations: `domain/continuity.py` and `agent/history.py`. `contracts/recent-alerts.json` holds the constants and the fixture cases, and both suites assert it | One shared module | `agent/` must not import `rain_alert` (enforced by the architecture test). The existing `composer-fence.json` already uses this pattern |
| D3 | `consecutive_days` is the run of consecutive America/Lima dates that ends at `as_of`'s local date. Today counts. Each earlier date needs at least one alert with `sent_at < as_of`. Lookback is 7 d. `continuing = consecutive_days >= 2`. `trend` (`rising`/`steady`/`falling`/`none`) compares the current level and warning level with the previous alert | Letting the LLM compare | Every continuity fact is computed in code. `continuing` keeps digits out of the prompt |
| D4 | Tool output: `alerts[]`, newest first, `n` clamped to 1..5, each `{sent_local "DD/MM/YYYY HH:MM", level, warning_level\|null}`, plus the facts from D3. Never title, body, or mm | Returning raw items | Prior mm amounts would widen the validator. Prior titles are scraped text. Residual risk: IAM cannot enforce this for nested `reasons` fields (see D8); it is code-enforced in `agent/history.py`, and nothing reaches the model |
| D5 | `MODEL` is built once at import. A new `Agent` is built per request. The tool result is cached per request (a second call returns the same dict) | `AGENT` singleton | `__call__` appends to `agent.messages`, so a singleton leaks history across warm invocations. Building the model at import keeps fail-fast at startup |
| D6 | The tool catches `ClientError`, `BotoCoreError`, `KeyError`, `ValueError` and returns empty history. It logs the error code only. Client config: connect 1 s, read 2 s, `total_max_attempts` 2 | Raising | History is optional. A missing history must never cost the alert |
| D7 | If the app-side summary read fails, the app uses an empty summary (fails closed). If the model then claims continuity, the validator rejects it and the template is sent | Failing the cycle | The send is already authorized, and history must never block it |
| D8 | `AlertHistoryReader` ManagedPolicy: `dynamodb:Query` on the table ARN only. Conditions: `ForAllValues:StringEquals dynamodb:LeadingKeys` = [`ALERT#ferrenafe`, `ALERT#demo-ferrenafe`] with a `Null` guard. `dynamodb:Attributes` ⊆ [pk, sk, sent_at, level, reasons]. `dynamodb:Select = SPECIFIC_ATTRIBUTES`. Output `AlertHistoryReaderPolicyName` | Outputting the ARN as the proposal says; granting from the agentcore project | The stack's convention is names only, never ARNs. IAM denies reading the top-level `message` (title and body). `dynamodb:Attributes` is top-level only, so the allowed `reasons` list of maps, which carries `WarningReason.title` and `ForecastThresholdReason.accumulated_mm`, is NOT hidden by IAM. Residual risk: for those nested fields the guarantee is code-enforced (`_top_warning_level` drops them), not IAM-enforced; nothing reaches the model. The attribute list lives in `contracts/recent-alerts.json` |
| D9 | Amend D49 for continuity only. Rule: "Call `get_recent_alerts` once before writing. Mention earlier alerts only when `continuing` is true: state the days in digits as `N días consecutivos`, copy the dates from the tool, say the alert is rising only when `trend` is `rising`. Otherwise do not describe anything as already having happened" | A broad rewrite | Smallest change to owner-approved wording. Re-gated by `scripts/agent_live_acceptance.py` |
| D10 | Validator changes: add `NumberUnit.DAYS` (`d[ií]as?`). The allowed DAYS value is `{consecutive_days}`, only when continuing. Prior `sent_local` dates and times join `_allowed_moments`. WORD_NUMBER also fires on days, and ordinals before `día` are refused | Treating tool digits as allowed | Keeps the "every figure traces to MessageRequest" rule |
| D11 | Latency: measure the AFTER run. If 3 × worst case > 35 s, raise `agent_read_timeout_seconds` to at most 60 (60 + 60 headroom ≤ 120 s Lambda) and add the measurements to the invoker test. If the worst case is > 20 s, roll back | Guessing a number | One existing round trip plus one to two more (~7.4 s each). The measurement-sized test pattern already exists |

## Sequence

```
RunAlertCycle   AlertRepo(DDB)   AgentComposer   AgentCore app   get_recent_alerts   Bedrock    Validator
 decide(send)──>│ query (dedup)
 [send only] ──>│ query 7d ──> ContinuitySummary into MessageRequest
 compose(req) ─────────────────>│ invoke{prompt, history}
                                │──────────────>│ new Agent(MODEL, tool bound to history)
                                │               │──────────────────────────────────────>│ turn 1
                                │               │<────────── tool_use(n) ───────────────│
                                │               │──────────>│ Query ALERT#<partition> (projected)
                                │               │<──────────│ facts (or empty on error)
                                │               │──────────────────────────────────────>│ turn 2 + forced structured output
                                │<──────────────│ CompositionOutput dict
                                │ validate_message(req incl. continuity) ───────────────────────────────>│
                                │<──────── ok -> agent text | violation -> template ───────────────────│
```

## File Changes

| File | Action | Description |
|---|---|---|
| `contracts/recent-alerts.json` | Create | Table name, `ALERT#` prefix, lookback, max n, allowed partitions, fixture cases |
| `contracts/composer-fence.json` | Modify | `history_key`, schema_version 2 |
| `src/rain_alert/domain/continuity.py` | Create | `ContinuitySummary` and `summarize(records, as_of, tz, current)` |
| `src/rain_alert/domain/messages.py` | Modify | `MessageRequest.continuity: ContinuitySummary = EMPTY` (defaulted, additive) |
| `src/rain_alert/domain/message_validation.py` | Modify | D10 |
| `src/rain_alert/application/run_alert_cycle.py` | Modify | Build the summary in the send branch (`dataclasses.replace`) |
| `src/rain_alert/adapters/{agent_invoker,agentcore_invoker,agent_composer}.py` | Modify | `invoke(prompt, history=None)` |
| `agent/history.py` | Create | Binding parser, DDB reader, `summarize`, tool factory |
| `agent/app.py`, `agent/prompts.py` | Modify | D5, D9, docstrings ("no tools" removed) |
| `agent/tests/test_app.py`, `test_history.py`, `test_history_contract.py` | Modify/Create | Fakes change shape; `tool_names == ["get_recent_alerts"]` |
| `infra/lib/scheduled-cycle-stack.ts`, `infra/test/*` | Modify | D8 |
| `scripts/seed_demo_history.py` | Create | `--day N` writes that scenario day's record to `demo-ferrenafe` via `DynamoDbAlertRepository`; `--reset` deletes the partition. Refuses any other slug |
| `docs/runbooks/agent-alert-history.md` | Create | Attach the policy, then run the post-deploy check: a non-empty demo read and an AccessDenied on `ALERT#other` |
| `docs/evidence/2026-10-2X-recent-alerts-before-after.md` | Create | Before/after table with masked ARNs |

AFTER script (outside the repo): `runs/after/invoke_after.py`. It reuses `runs/before/scenario.json` and `build_request`, with `as_of` = window start. For each day it runs 3 runs with `history.partition=demo-ferrenafe`, applies `validate_message` (also applied to the BEFORE outputs offline), then seeds that day's record.

## Testing Strategy (strict TDD)

| Layer | What | How |
|---|---|---|
| Unit (domain) | `summarize`: Lima date boundary, gaps, same-day duplicates, `sent_at >= as_of` excluded, 7 d cutoff; DAYS and dates in the validator | pytest, contract fixture |
| Unit (agent) | Tool output, n clamp, error degradation, per-request isolation (two calls, no leaked messages), binding validation | botocore `Stubber`, fake Agent factory, contract fixture |
| Integration | Repository read for the summary; seeding script; refusing a non-demo slug | moto |
| Infra | Policy actions, resource, all three conditions; output name | jest assertions |
| Live | Acceptance script (D49 gate); AFTER success criteria | Manual, evidence doc |

## Rollback

1. Set the SSM composer flag to `template` (takes effect immediately).
2. Redeploy runtime v3 from `baseline-no-tools`. App changes stay inert: an old runtime ignores `history`.
3. Detach the policy.
4. Run `seed_demo_history.py --reset`.

## Open Questions

- [ ] Confirm the table and the runtime are both in us-east-2. The tool client uses the runtime's region.
- [ ] Owner approval of the D9 wording before apply.

## Review Amendments

Applied after the adversarial review of the validator change. Each one narrows D10 or D7 and none widens what a body may say.

- **W1 (D10, spelled-out day counts).** A cardinal number word followed by a day-like word (`dia`, `jornada`, `noche`, `semana` and their plurals) is refused with `WORD_NUMBER` unless that exact phrase, folded, appears verbatim in the request's own checklist. This replaces "refused only when the clause has a continuity marker", which let `Hace tres días que llueve`, `van cuatro días de lluvia` and `por tres jornadas` through with any `consecutive_days`. The marker check stays as a second, additional refusal. Known wrong-lax residual: a phrase the checklist itself carries (today `dos días`) is still accepted outside a continuity-marker clause, so `Hace dos días que llueve` passes. It is bounded by the checklist and cannot be widened by the model.
- **W2 (D10, prior moments).** The validator reads only the 5 most recent `prior_alerts` (`MAX_PRIOR_ALERTS`, pinned to `max_n` in the contract), the ones the tool could have shown the model. A prior date is accepted alone; a prior time is accepted only directly after its own date as one `DD/MM/YYYY HH:MM` pair, so a body can no longer mix one alert's date with another's time. A bare prior time (`las 08:00`) is now refused; times of the request's own window, warning and peak are unchanged.
- **W3 (D7, degraded read).** A failed continuity read now logs `continuity read failed: <ExceptionClass>` at WARNING, the same convention as `agent/history.py` (class or AWS code, never the message, which can carry a table ARN). A test pins that the failure leaves `sent`, `recipients_count` and the recorded alert equal to a successful read's.
- **W4 (D7, who pays for the read).** `RunAlertCycle` reads the history only when `deps.composer.consumes_history` is true. The flag is a new attribute on the `MessageComposer` port: `False` on the deterministic template, `True` on `AgentBackedComposer`. It is asked of the composer, not of `config.composer`, because the CLI `--composer` override replaces the config only inside wiring and the cycle's own `config.load()` would not see it. With the template the request keeps `EMPTY_CONTINUITY`, which the template ignores. `FakeMessageComposer` defaults to `True` so existing continuity tests keep observing the read.
- **S1 (D1, one instant).** `ContinuitySummary` gains `as_of: datetime | None = None`. `RunAlertCycle` stamps it with the cycle's `now`, also on the empty summary of a failed read, and `AgentBackedComposer._history_binding` binds that instant, falling back to its own clock only for a request no cycle stamped. The app summary and the agent's tool therefore count days from the same moment even when composition takes seconds.
- **S2 (D10, ordinal day digits).** `\d+(er|ro|ra|º|ª)\s*día` joins the forward DAYS unit, and a figure directly after `día` (`día 3`) is read as DAYS too. Both are held to `consecutive_days`, so a `3er día` can no longer borrow a 3.0 mm forecast value. Note: `º`/`ª` are letters outside the Latin script by the validator's own rule, so a body writing `3º día` is refused by `DISALLOWED_SCRIPT` regardless; the unit match still stops it borrowing a value first. Known wrong-strict, pre-existing: `día 22 de octubre` is not introduced by a date marker and is refused as a bare figure.
