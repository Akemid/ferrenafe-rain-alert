# Exploration: recent-alerts-tool

> Materialized from Engram `sdd/recent-alerts-tool/explore` (observation #3123, 2026-10-23). The explore phase could not write files.

**What**: Exploration of adding the read-only tool `get_recent_alerts(n)` to `agent/app.py` (Strands 1.55.1 on AgentCore, Haiku 4.5) for the "Give your Agent a Tool" hackathon (deadline 2026-10-26).

**Why**: Continuity in alert text ("third consecutive day, escalating"). Deferred in `composer-agent/state.yaml` (`no-continuity-tool`) because the store was a local JSON file.

**Where**: `agent/app.py`, `src/rain_alert/adapters/dynamodb_alert_repository.py`, `src/rain_alert/domain/message_validation.py`, `agent/prompts.py`, `infra/lib/scheduled-cycle-stack.ts`

## Findings

1. **Blocker already removed.** "Change 3" is scheduled-cycle (deployed 2026-10-01). The store is DynamoDB table `ferrenafe-alerts-sent` (pk `ALERT#<city_slug>`, sk `<window_start ISO>#<level>`, TTL 30 d, PITR, RETAIN, PAY_PER_REQUEST), written by `RunAlertCycle.record_alert` after `send_alert`. `DynamoDbAlertRepository` and moto-style tests exist. No write-path change is needed. The Lambda role has Query/GetItem/PutItem/DeleteItem. The agent runtime (us-east-2, runtime `ferrenaferainalert_rainalert-upwZ4M5wl5`) execution role is CLI-provisioned (agentcore CLI) and has no DynamoDB grant; the table lives in ScheduledCycleStack, the agent in `agentcore/` (separate). The SSM composer flag is `agent` (since 2026-10-02), but no real authorized send had occurred by then, so the table may be empty and the demo needs seeding.

2. **Strands gotcha** (verified in `.venv` `strands/agent/agent.py` 1.55.1): the legacy `Agent.structured_output()` is deprecated and calls `model.structured_output()` directly, with no event loop, so tools are never invoked. Using a tool requires switching to `agent(prompt, structured_output_model=CompositionOutput)` -> `result.structured_output` (`event_loop.py` lines 369-389: the loop runs tools, then forces the structured-output tool at `end_turn`). The `StructuredComposer` Protocol in `app.py` and `FakeStructuredModel` in `agent/tests/test_app.py` must change shape. Second gotcha: a module-level `AGENT` singleton invoked via `__call__` appends to `agent.messages` (whereas `structured_output` used temp messages), so history would leak across warm invocations. Build the Agent per request (or clear messages) and bind the tool per request.

3. **Store options.** DynamoDB (existing, zero new infra, Query on one pk; IAM = one managed policy with `dynamodb:Query` on the table ARN and a `LeadingKeys` condition, attached to the AgentCore execution role; testable with moto, already used) vs. an S3 JSON object (new writer path, read-modify-write race, no TTL) vs. reusing `AlertRepository` from the agent (impossible: `agent/` must not import `rain_alert`; the architecture test enforces both directions). **Recommendation: DynamoDB.** The agent gets its own ~40-line boto3 query that reads only projected attributes (sent_at, level, window, top warning level), never the message body, to avoid re-feeding prior model text.

4. **Tool return shape.** Pre-digested facts computed in code, not by the LLM: `alerts[]` newest-first (n clamped 1..5, window 72 h-7 d), each `{sent_local DD/MM/YYYY HH:MM, level, warning color?}`, plus `consecutive_days` (int), `previous_level`, `escalated` (bool). The city/partition is bound by closure or constant, NOT a model argument. The invoker seam is `invoke(prompt: str)` with payload `{prompt}`; passing `city_slug` needs a seam and contract change, so a constant/env in the agent (TABLE_NAME pattern) is preferred by the exploration.

5. **Validator problem (core design issue).** The validator runs app-side from `MessageRequest` and cannot see tool output. A digit in "3 dias" fails (a bare figure is checked against the union of mm/%/h sets); prior dates fail `_allowed_moments`. Fix: the app side also reads history via the existing `AlertRepository.alerts_with_window_start_between` (already used by AlertPolicy for dedup) and puts a `ContinuitySummary` into `MessageRequest` (pure domain function over AlertRecords: consecutive_days, previous_level, prior local dates). The validator gains `NumberUnit.DAYS` and allowed dates from it. Tool output and validator are two renderings of the same data (drift risk -> contract JSON test). The SYSTEM_PROMPT rule "Do not describe anything as already having happened" contradicts continuity, and the agent-system-prompt wording is owner-approved (D49) and live-acceptance gated, so an owner-approved amendment is needed. Ordinals ("tercer dia") are not in `_NUMERAL_WORDS`, and WORD_NUMBER only fires on mm/h/% units; the prompt should demand digits ("3 dias consecutivos").

6. **Security.** History is own data (not third-party text), but the title/body of prior alerts embed scraped aviso titles, so they are excluded from tool output. The tool is read-only, Query only, LeadingKeys-scoped. Invariants kept: the LLM never decides to alert (the agent runs after AlertPolicy authorization). Tool failure (AccessDenied/timeout) must degrade to empty history, not fail composition; the Lambda fallback to the template still covers it. Reopening `agent-has-no-tools` is justified because the blocker (store location) is removed; `no-warning-detail-tool` stays dropped. Update the `agent.py` docstring "no tools" claims, the test asserting `tools=[]` (non-capability 5), and docs.

7. **Smallest slice (~2 days).**
   - (a) infra: `AlertHistoryReader` ManagedPolicy + output in ScheduledCycleStack, attached to the AgentCore role (manual runbook step, like SnapshotWriter);
   - (b) agent: `get_recent_alerts` `@tool` + per-request Agent + switch to `structured_output_model`;
   - (c) domain `ContinuitySummary` + MessageRequest field + prompt payload + validator DAYS/dates;
   - (d) prompt amendment;
   - (e) demo seeding script on a demo slug (NEVER write fake records into the real city partition: a fake imminent record suppresses real alerts for 72 h, per the Notifier docstring) + before/after evidence in `docs/evidence/`.
   - Cut candidate if time is short: skip the app-side ContinuitySummary and validator change by having the validator treat tool-derived digits as allowed. NOT recommended (breaks the traceability invariant).

## Risks

Validator drift; IAM on a CLI-managed role; empty table; Haiku not calling the tool (prompt instruction; tool_choice auto); cold start adds one Bedrock round trip against the 10 s / 120 s timeouts (`agent_read_timeout` contract); AgentCore deploy of the boto3 dependency (already transitive).
