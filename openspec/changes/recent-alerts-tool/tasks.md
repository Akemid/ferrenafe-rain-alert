# Tasks: Recent Alerts Tool (agent continuity)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~1300-1700 (about 40% tests) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 -> PR 2 -> PR 3 -> PR 4 |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Notes |
|------|------|-----------|-------|
| 1 | Agent tool live-ready (contract, history.py, app, prompt) | PR 1 | DEMO. ~450. Base: main or tracker |
| 2 | IAM policy + seed script + runbook | PR 2 | DEMO. ~350. Independent of PR 1 code |
| 3 | Domain continuity + validator + cycle + adapters | PR 3 | REQUIRED (not cuttable). ~600. Base: PR 2 branch (or main) |
| 4 | Evidence + acceptance + latency | PR 4 | DEMO evidence. Docs/scripts |

Tags: [DEMO] needed for the live before/after. [REQ] invariant, not cuttable. [POLISH] cuttable. [OWNER] owner-approved action. Test commands: `uv run pytest`; `uv run --directory agent pytest`; `uv run ruff check`; `uv run mypy`; `cd infra && npm test`. Each impl task is RED then GREEN in the same commit.

## Phase 1: Agent tool (PR 1) [DEMO]

- [x] 1.0 [OWNER] Owner approves D9 prompt wording before 1.7. (Approved 2026-10-24.)
- [x] 1.1 Create `contracts/recent-alerts.json` (table, `ALERT#` prefix, 7 d, max n 5, partitions, fixture cases); add `history_key` + schema_version 2 to `contracts/composer-fence.json`. RED: fence contract test.
- [x] 1.2 RED/GREEN `agent/tests/test_history.py` + `agent/history.py` binding parser: invalid or missing -> empty history; partition never a tool arg.
- [x] 1.3 RED/GREEN `summarize` in `agent/history.py` vs fixture (`agent/tests/test_history_contract.py`): Lima dates, gaps, same-day, `sent_at >= as_of`, 7 d; facts `consecutive_days`, `previous_level`, `escalated`, `trend`.
- [x] 1.4 RED/GREEN DDB reader (botocore Stubber): projected Query on `ALERT#<partition>`, no title/body/mm; client timeouts 1 s/2 s, 2 attempts.
- [x] 1.5 RED/GREEN tool factory: `n` clamped 1..5, schema only `n`, per-request result cache, `ClientError`/`BotoCoreError`/`KeyError`/`ValueError` -> empty history, no write calls.
- [x] 1.6 RED/GREEN `agent/app.py`: `MODEL` at import, new `Agent` per request, `agent(prompt, structured_output_model=...)`, payload `history`; update `agent/tests/test_app.py` (`tool_names == ["get_recent_alerts"]`, two-call isolation).
- [x] 1.7 RED/GREEN `agent/prompts.py` D9 amendment (tool-first, digits, no decimals); remove "no tools" docstrings.
- [x] 1.8 PR 1 gate: agent suite, `uv run pytest`, ruff, mypy.

## Phase 2: IAM + seed (PR 2) [DEMO]

- [x] 2.1 RED `infra/test/*`: `AlertHistoryReader` = Query only, table ARN, three conditions, output name. GREEN in `infra/lib/scheduled-cycle-stack.ts`.
- [x] 2.2 RED/GREEN `scripts/seed_demo_history.py` (moto): `--day N`, `--reset`, refuses non-`demo-ferrenafe` slug with exit != 0 and zero writes.
- [x] 2.3 Create `docs/runbooks/agent-alert-history.md`: attach, post-deploy check, AccessDenied on `ALERT#other`, rollback.
- [x] 2.4 PR 2 gate: `cd infra && npm test`, `uv run pytest`.

## Phase 3: Deploy + live smoke [DEMO] [OWNER]

- [ ] 3.1 [OWNER] `cdk deploy ScheduledCycleStack` (us-east-2).
- [ ] 3.2 [OWNER] Attach `AlertHistoryReader` to the AgentCore role; run the 2.3 check.
- [ ] 3.3 [OWNER] `agentcore deploy` (runtime v4).
- [ ] 3.4 Seed day 1 and invoke with `history.partition=demo-ferrenafe`: smoke, non-empty tool read.

## Phase 4: Domain + validator hardening (PR 3) [REQ]

- [ ] 4.1 RED/GREEN `src/rain_alert/domain/continuity.py` `ContinuitySummary`/`summarize` against the same fixture; purity.
- [ ] 4.2 RED/GREEN `messages.py`: `MessageRequest.continuity` default empty; template output unchanged and valid.
- [ ] 4.3 RED/GREEN `message_validation.py`: `NumberUnit.DAYS`, prior dates, WORD_NUMBER on days, ordinals before `dia`; empty summary allows none; existing suite green.
- [ ] 4.4 RED/GREEN `run_alert_cycle.py`: summary only in send branch (`dataclasses.replace`); read failure -> empty; no read on dedup.
- [ ] 4.5 RED/GREEN `agent_invoker`, `agentcore_invoker`, `agent_composer`: `invoke(prompt, history=None)`; fence contract; architecture test green.
- [ ] 4.6 PR 3 gate: full suite.

## Phase 5: Evidence (PR 4) [DEMO]

- [ ] 5.1 Run `scripts/agent_live_acceptance.py` (>= 8/10).
- [ ] 5.2 [OWNER] Run AFTER (`runs/after/invoke_after.py`): 3 days x 3 runs, validator applied, latencies recorded.
- [ ] 5.3 D11: if 3 x worst > 35 s raise `agent_read_timeout_seconds` (max 60) with test; worst > 20 s -> rollback.
- [ ] 5.4 Create `docs/evidence/2026-10-2X-recent-alerts-before-after.md` (masked ARNs, 9 BEFORE vs 9 AFTER, real partition unchanged).

## Phase 6: Polish [POLISH]

- [ ] 6.1 Real-partition snapshot diff script.
- [ ] 6.2 README/docs mentions of "no tools".
