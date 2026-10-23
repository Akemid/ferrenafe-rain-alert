# Proposal: Recent Alerts Tool (agent continuity)

## Intent

Alerts are composed in isolation. Baseline `baseline-no-tools` (94a2140, runtime v3): a 3-day escalation (yellow 14.2 mm -> orange 31.5 mm -> red 68.4 mm) produced 9 messages, none referencing prior days. Give the composer ONE read-only tool, `get_recent_alerts(n)`, so messages state continuity ("3 dias consecutivos, en aumento"). Hackathon deadline 2026-10-26 13:00 PDT.

**Reopens `agent-has-no-tools` on new evidence**: its only blocker (local JSON store) was removed by scheduled-cycle (DynamoDB, 2026-10-01). `no-warning-detail-tool` stays dropped. The LLM still never decides whether to alert.

## Scope

### In Scope
- (a) `AlertHistoryReader` ManagedPolicy (`dynamodb:Query`, table ARN, `LeadingKeys`) in ScheduledCycleStack + ARN output + runbook attach step
- (b) `@tool get_recent_alerts`; per-request Agent; `agent(prompt, structured_output_model=...)`
- (c) Domain `ContinuitySummary` in `MessageRequest`, prompt payload, validator `NumberUnit.DAYS` + allowed prior dates; contract test pinning tool output vs summary
- (d) Continuity-only amendment to rule D49, re-gated by the live acceptance script
- (e) Demo-partition seeding script; before/after evidence in `docs/evidence/`

### Out of Scope
- `get_warning_detail`; any write tool; multi-city
- "Nivel inminent" title wording fix
- Moving the AgentCore role grant into CDK

## Capabilities

### New Capabilities
- `agent-alert-history`: read-only history tool, partition binding, degradation to empty history

### Modified Capabilities
- `agent-message-composition`: tools allowed (one), structured-output call shape, D49 continuity exception
- `alert-cycle`: `MessageRequest` carries `ContinuitySummary`; validator accepts traced day counts and dates

## Approach

Tool and validator read the same DynamoDB facts; continuity figures are computed in code, never by the LLM. Tool returns projected attributes only (no prior title/body). Any tool error returns empty history.

## Affected Areas

| Area | Impact |
|------|--------|
| `infra/lib/scheduled-cycle-stack.ts` | Modified |
| `agent/app.py`, `agent/prompts.py`, `agent/tests/` | Modified |
| `src/rain_alert/domain/` (continuity, `message_validation.py`) | New/Modified |
| `scripts/` seeding, `docs/` runbook + evidence | New |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Tool output and validator drift | Med | Contract test |
| Haiku skips the tool | Med | Prompt instruction; measured runs |
| Extra round trip breaks `agent_read_timeout` | Low | Measure; template fallback |
| Fake record in real partition suppresses alerts 72 h | Low | Demo partition only; never model-chosen |
| Policy detached from CLI-managed role on redeploy | Med | Runbook step + post-deploy check |
| `agent-message-composition` not yet archived to `openspec/specs/` | Med | Delta against composer-agent + agent-system-prompt specs |

## Open Question (design)

Partition binding: env var vs. payload field beside `prompt`. An env var on the shared production runtime would redirect production reads.

## Rollback Plan

Flip SSM composer flag to `template` (immediate); redeploy runtime v3 from `baseline-no-tools`; detach policy; delete demo partition.

## Success Criteria

- [ ] Same 3-day scenario inputs, 3 runs per day (9 messages): >= 5/6 day-2/3 messages reference continuity; 0/3 day-1 messages claim prior days
- [ ] 0 validator rejections caused by continuity numbers or dates
- [ ] Composition latency within `agent_read_timeout`; tool failure still yields an accepted or template message
- [ ] Real city partition unchanged by the demo

## Proposal question round (assumptions pending owner review)

1. IAM via SnapshotWriter pattern.
2. D49 amended for continuity only.
3. Partition never model-chosen; demo partition separate.
4. Baseline's 9 messages = 3 runs x 3 days (verify against `docs/evidence/` before scoring).
