# Archive Report: Core Alert Cycle (Local, No Cloud)

**Change**: `core-alert-cycle` (Subproject 1 of 3)
**Project**: `ferrenafe-rain-alert`
**Archived**: 2026-09-07 at main @ a2f3af853a9e090eeb0194bf1c671cb936cd55af
**Artifact Store**: hybrid (openspec/changes/archive/2026-09-07-core-alert-cycle, engram sdd/core-alert-cycle/archive-report)

---

## Scope Summary

This change implemented the **alert decision engine and its real data feeds**, running locally against SENAMHI warnings and Open-Meteo forecasts, producing risk assessments and composing messages deterministically — without any cloud infrastructure, scheduler, LLM, or external delivery mechanism.

---

## What Shipped: Seven Domains

| Domain | Requirements | Scenarios | Purpose |
|--------|---|---|---|
| **risk-evaluation** | 7 | 18 | Rule engine: source-aware risk levels from warnings and forecasts, with degraded-mode thresholds, highlands-only early-signal handling, and five ordered evaluation branches |
| **alert-dedup** | 5 | 6 | Dedup policy: escalation/repeat/de-escalation rules guarding against redundant community alerts |
| **source-outage-notices** | 4 | 6 | Operator technical notices: single-source outage notices, dual-outage community-alert suppression, recovery notices, distinct from community messages |
| **weather-sources** | 7 | 20 | External data acquisition: Open-Meteo forecast fetch, SENAMHI multi-hazard warning scraper with breakage detection, flood-relevance filtering (no wind/heat/cold/snow/hail), zone classification for sierra vs. coast, scraper-anomaly auditing |
| **alert-cycle** | 4 | 8 | Use case orchestration: `RunAlertCycle` steps and ordering, deterministic message template satisfying `MessageComposer` port, degraded-mode disclosure, record-after-notify sequencing |
| **local-alert-cli** | 3 | 3 | Entrypoint: dry-run CLI against real adapters, console-only output, informative calibration display |
| **repo-hygiene** | 5 | 6 | Public-repo rules: Apache 2.0 license, no-warranty + SENAMHI/INDECI-complement + Open-Meteo non-commercial disclaimers, no secrets/personal data committed (enforced by `git grep` scan), domain/adapters boundary test |

**Total**: 35 requirements, 67 scenarios, all traced to passing tests.

---

## Architecture & Implementation

**Shipped code** (seven pull requests merged to main):

1. **PR 1** (`2026-09-04 15:51:21Z`, `feat/core-alert-cycle-1-tooling`): Tooling scaffold (uv/pytest/ruff/mypy), package skeleton, repo hygiene (LICENSE, README, .gitignore, .env.example), architecture boundary scanner. **324 authored insertions** (owner-accepted `size:exception` against 250-line estimate).

2. **PR 2** (`2026-09-04 20:12:57Z`, `feat/core-alert-cycle-2-domain`): Domain types (frozen dataclasses), ports (seven Protocols), `RiskEvaluator` (four ordered branches at this point; a fifth was added during slice 3 by owner decision), dedup rules, outage state machine, deterministic message template, `RunAlertCycle` use case, test fakes. **3151 changed lines** (1021 src, 1335 tests, high budget overrun; owner-accepted `size:exception`).

3. **PR 3** (`2026-09-05 16:55:08Z`, `feat/core-alert-cycle-3a-sources`): Open-Meteo adapter, SENAMHI scraper with multi-hazard filtering and zone classification (added 2026-09-04 after live-page inspection). **1366 changed lines** in the slice.

4. **PR 4** (`2026-09-05 17:05:09Z`, `feat/core-alert-cycle-3b-persistence`): Console notifier, local file + in-memory alert/contact/config repositories, JSON serialization. Chained to PR 3. **1208 changed lines**.

5. **PR 5** (`2026-09-05 17:05:58Z`, `feat/core-alert-cycle-3c-cli`): Local CLI entrypoint with real adapters, dry-run output, optional offline fixtures. Chained to PR 4. **933 changed lines**. Plus live-source canary and full-suite verification gate.

6. **PR 6** (`2026-09-06 03:19:31Z`, `feat/core-alert-cycle-3c-cli`): `docs/architecture/` guide (implementation strategy, port contracts, file layout, live-source testing notes), security fixes (message sanitization to prevent structure injection, forecast value range enforcement, CLI stdout clarity), and final integration of slice-3 commits. **129 openspec artifacts** plus ~300 lines of documentation.

7. **PR 7** (`2026-09-07 03:06:23Z`, `feat/verification-remediation`): Final verification-report amendments and spec refinements (W5b added `domain/sanitize.py` to design section; S3, S7 filed). **557 unit + 15 integration tests** (10 live, 5 skipped on non-rain days), all green.

---

## Shipped Capabilities (Proposal Success Criteria)

All 12 success criteria from proposal.md are implemented and proven:

1. ✅ 2017-03-12, 29.8 mm in 24 h @ 70% → `imminent` (spec: Risk Evaluation → Scenario: Imminent from forecast alone)
2. ✅ 10 mm in 48 h @ 60% + yellow warning → `prepare` (spec: Risk Evaluation → Scenario: Prepare on threshold)
3. ✅ 9 mm in 48 h @ 60% + yellow warning → `none` (spec: Risk Evaluation → Scenario: Below prepare threshold)
4. ✅ SENAMHI unavailable, 15 mm in 48 h @ 65% → `none` (spec: Risk Evaluation → Scenario: Degraded prepare requires 70%)
5. ✅ Altered HTML structure → `unavailable` (spec: Weather Sources → Scenario: Altered HTML structure)
6. ✅ Healthy fixture, zero warnings → `available` empty list (spec: Weather Sources → Scenario: Healthy page with no current warnings)
7. ✅ Dedup: same level, same window → send nothing; escalation → send; de-escalation → send nothing (spec: Alert Dedup → Scenarios: Repeat level, Prepare escalates, De-escalation)
8. ✅ Both sources unavailable across 3 cycles → level `none`, exactly 1 operator notice (spec: Source Outage Notices → Scenario: Three consecutive dual-outage cycles)
9. ✅ Both sources unavailable → level `none`, one recovery notice when one returns (spec: Source Outage Notices → Scenario: Recovery after dual outage)
10. ✅ `uv run pytest` and `uv run ruff check` pass; domain imports nothing from adapters/boto3/requests/LLM (spec: Repo Hygiene → Scenario: Import boundary test)
11. ✅ CLI runs real cycle, prints level/reasons/message, sends nothing (spec: Local Alert CLI → Scenarios: Real-source run, Console-only delivery, Full output)
12. ✅ `git grep` finds no phone numbers, emails, tokens, AWS IDs; `.env.example` has names only (spec: Repo Hygiene → Scenarios: Grep check passes, .env.example has no values)

---

## Verification Outcome

**Verdict**: PASS

- **Blockers**: 0
- **Critical findings**: 0
- **Requirements traced**: 35 of 35 (100%)
- **Scenarios traced**: 67 of 67 (100%)
- **Test exit code**: 0 (557 passed, 15 deselected)
- **Build exit code**: 0 (mypy strict green on 35 source files)
- **Revision verified**: main @ a2f3af8 (a2f3af853a9e090eeb0194bf1c671cb936cd55af)
- **Date verified**: 2026-09-07

**Final findings**: 1 WARNING, 5 SUGGESTIONs (no blockers; see verify-report.md for disposition).

---

## What Was NOT Delivered

**Explicitly out of scope (Proposal section 3):**

- **No cloud**: Lambda, DynamoDB, SSM, EventBridge, CDK, `moto` tests → Change 2 (composer agent, fallback wiring).
- **No LLM or agent framework**: Strands, AgentCore, output validator → Change 2.
- **No external notification channels**: Telegram, SES → Change 3.
- **No automatic recipient delivery**: v2 scope.
- **No scheduler**: EventBridge Scheduler → Change 3.
- **No storage outside the repo**: State file only, gitignored and disposable.

**Unfinished infrastructure concerns (carried to Changes 2–3):**

| Item | Why Deferred | Tracking |
|------|---|---|
| Message sanitization at all rendering boundaries | **Completed 2026-09-05 (security review remediation)**: `domain/sanitize.py` applied at four rendering boundaries (template, reasons renderers, scraper, serialization). Invariant stated on `MessageComposer` port so change 2 inherits it. | Design §3.3, tasks.md remediation, verify-report W5b |
| Serializer mapping for structured reasons | **Completed 2026-09-05**: `adapters/serialization.py` handles `Reason` → JSON/DynamoDB shape; change 3 reuses it. | tasks.md "Open Items for Apply"; design §12 additions |
| Advisory lock on state file | **Deferred to Change 3**: No file lock in local CLI. Two concurrent `JsonFileAlertRepository` instances lose each other's records (read-modify-write lost update, not fixable by write-time lock alone). For scheduler-driven CLI, recommend `fcntl.flock` with timeout + invalidate-on-entry semantics. Change 3 replaces this adapter with DynamoDB and will use conditional writes instead. | Design §8.3, tasks.md "Open Items", verify-report note on open items |
| Alert state file growth without bound | **Deferred to Change 3**: Every `record_alert` rewrites the whole JSON document. Documented retention rule (keep records within `dedup_lookback_hours * 2` of `now`) proposed but not implemented: needs a clock the port doesn't carry, and change 3's DynamoDB adapter would use TTL instead. | Design §8.3, tasks.md "Open Items", apply-progress Phase 3 remediation |
| Forecast horizons configurable | **Deferred pending calibration decision**: `AlertConfig.forecast_hours` controls fetch, not evaluation window. `IMMINENT_HORIZON_HOURS = 24` and `PREPARE_HORIZON_HOURS = 48` are domain constants. Thresholds are calibrated over 48 hours; making horizons configurable means deciding whether they still apply at 72 hours — a calibration question for the first rainy season, owner decision. | tasks.md "Open Items", verify-report S3 (partial) |
| Zone filter precision (`COSTA SUR` ambiguity) | **Narrowed 2026-09-04 by owner decision**: Coast-only filter was too blunt (dropped 144 of 243 live precipitation rows, including PRECIPITACIONES EN LA SIERRA). Highlands-only rainfall now kept as early signal (may contribute to `prepare`, never raises `imminent` alone). With that change, zone rule can only limit severity, never remove a warning. Residual: `COSTA SUR` (1 of 788 rows) is not precisely covered — calibration question for first rainy season. | Design §5.1.1, tasks.md "Open Items Resolved", risk-evaluation spec amendment |
| Duplicate constant in hygiene test | **Minor code debt, non-blocking**: `GIT_IDENTITY` defined twice in `tests/hygiene/test_repo_hygiene.py`; second shadows the first. No functional impact (both values identical). | Verify-report S-B |
| Scenario text stale (`Grep check passes` description) | **Specification/artifact debt, no code impact**: Scenario text says "GIVEN the full repository working tree" and names four shapes; shipped implementation scans all 84 reachable commits over ten patterns. Requirement is satisfied; scenario wording was never updated. | Verify-report S-A |

---

## Architecture Contracts for Changes 2 & 3

These are **breaking changes after this slice merges** (defined in design.md):

1. **Port signatures** (design §4, §10): All seven `Protocol` definitions are locked. Change 2's composer adapts `MessageComposer`; change 3 adapts all seven. Adding a parameter is breaking for both downstream changes.

2. **Message sanitization invariant** (design §3.3): `MessageComposer` MUST call `domain.sanitize.sanitize_for_message(...)` on any user-provided text before interpolating it into the Spanish community body. If skipped, structure-injection risk recurs.

3. **Reason serialization shape** (design §3.1, adapters/serialization.py): Structured `Reason` values serialize to JSON as `{type: string, ...per-reason-type fields}`. Change 3's DynamoDB adapter must honor this shape for dedup queries to work across changes.

4. **Run-cycle use-case signature** (design §7, application/run_alert_cycle.py): `RunAlertCycle(dependencies).execute()` → `CycleResult`. The result carries `message_request` (always populated, even on send-denied), `alert_sent`, `notices_sent`. Signature is locked.

5. **Configuration and state shapes** (design §4.1, §8.3): `TimeWindow.key()` is a change-3 affordance for DynamoDB queries; `AlertRecord`/`OutageRecord` document shapes in `adapters/serialization.py` are locked for cloud storage.

---

## Test Coverage Summary

**Strict TDD active** (per config.yaml `tdd: true`):

- **Unit layer**: 539 tests (domain rules table-driven, ports protocol compliance, adapters mocked HTTP)
- **Architecture layer**: 4 tests (layer-boundary, forbidden-import, package structure, domain-purity)
- **Hygiene layer**: 14 tests (repo license, README disclaimers, .gitignore, secrets scan, env example)
- **Live integration layer**: 15 opt-in tests (10 live SENAMHI canary + Open-Meteo fetch, 5 skipped on non-rainy days)
- **Total collected**: 572; **default suite**: 557 passed, 15 deselected, exit 0
- **Build**: mypy strict green on 35 source files, exit 0
- **Style**: ruff check clean, ruff format --check clean, exit 0

**TDD compliance**: 34 of 44 task IDs appear in TDD Cycle Evidence tables (apply-progress.md). The 10 not in tables are tooling scaffold, verification gates, Protocol-only ports module, test-support fakes, fixture capture, local wiring, manual live smoke, and one REFACTOR task — none are RED/GREEN work.

---

## Merged PRs Summary

| # | Title | Branch | Merged | Slice | Lines |
|---|---|---|---|---|---|
| 1 | build: scaffold tooling and repo hygiene | `feat/core-alert-cycle-1-tooling` | 2026-09-04 15:51:21Z | 1 of 3 | 324 authored |
| 2 | feat: add the alert domain, ports and cycle use case | `feat/core-alert-cycle-2-domain` | 2026-09-04 20:12:57Z | 2 of 3 | 3151 changed |
| 3 | feat: acquire and classify the live weather sources | `feat/core-alert-cycle-3a-sources` | 2026-09-05 16:55:08Z | 3a of 3 | 1366 in slice |
| 4 | feat: add the console notifier and local persistence | `feat/core-alert-cycle-3b-persistence` | 2026-09-05 17:05:09Z | 3b of 3 | 1208 in slice |
| 5 | feat: add the local alert-cycle CLI and live canaries | `feat/core-alert-cycle-3c-cli` | 2026-09-05 17:05:58Z | 3c of 3 | 933 in slice |
| 6 | docs: add the architecture guide, fix the first security review, land the remaining slice-3 commits | main | 2026-09-06 03:19:31Z | Integration | ~300 docs + 129 openspec |
| 7 | chore: complete verification remediation and land to main | main | 2026-09-07 | Verification | openspec/state.yaml only |

---

## Review & Remediation Timeline

| Date | Event | Finding Count | Action |
|------|---|---|---|
| 2026-09-04 | Fresh-context adversarial review (PR 1) | 1 CRITICAL | Fix committed: `c06a1a6` (relative-import scanner bypass) |
| 2026-09-04 | Fresh-context adversarial review (PR 2) | 2 CRITICAL, 6 WARNING, 3 SUGGESTION | All fixed on same branch under strict TDD (apply-progress "Phase 2 Review Remediation") |
| 2026-09-04 | Fresh-context adversarial review (PR 3) | 2 CRITICAL, 3 WARNING, 3 SUGGESTION, 1 owner decision | All fixed; multi-hazard filter added (contract change 3.5b) |
| 2026-09-05 | Fresh-context security review (slice 3) | 1 HIGH, 1 MEDIUM, 1 LOW | All fixed: message sanitization, forecast value range enforcement, CLI stdout clarity; `domain/sanitize.py` added |
| 2026-09-06 | Full spec-driven verification | 0 CRITICAL, 8 WARNING, 11 SUGGESTION | Spec amendments, artifact corrections, final counts (apply-progress "Added at 2026-09-06 verification remediation") |
| 2026-09-07 | Re-verification on main after final remediation merged | 0 CRITICAL, 1 WARNING, 5 SUGGESTION | Archive-ready: 35/35 requirements, 67/67 scenarios, all green |

---

## Files in This Archive

```
archive/2026-09-07-core-alert-cycle/
├── proposal.md                    — Original proposal (scope, approach, risks, rollback)
├── design.md                      — Design decisions pinning contracts for changes 2 & 3
├── specs/
│   ├── alert-cycle/spec.md        — RunAlertCycle orchestration (4 req, 8 scenarios)
│   ├── alert-dedup/spec.md        — AlertPolicy dedup rules (5 req, 6 scenarios)
│   ├── risk-evaluation/spec.md    — RiskEvaluator branches (7 req, 18 scenarios)
│   ├── source-outage-notices/spec.md — Outage notices (4 req, 6 scenarios)
│   ├── weather-sources/spec.md    — Open-Meteo + SENAMHI adapters (7 req, 20 scenarios)
│   ├── local-alert-cli/spec.md    — CLI entrypoint (3 req, 3 scenarios)
│   └── repo-hygiene/spec.md       — License, secrets, boundaries (5 req, 6 scenarios)
├── tasks.md                       — 44 tasks, all checked off; open items and decisions recorded
├── apply-progress.md              — Three slices' implementation details, TDD evidence, review remediation
├── verify-report.md               — Full verification with 35/35 requirements, 67/67 scenarios, 0 blockers
└── state.yaml                     — DAG state, phase completion, PR merge times, decision log

Main specs synced to `openspec/specs/`:
├── alert-cycle/spec.md
├── alert-dedup/spec.md
├── risk-evaluation/spec.md
├── source-outage-notices/spec.md
├── weather-sources/spec.md
├── local-alert-cli/spec.md
└── repo-hygiene/spec.md
```

---

## Traceability

**Engram observation IDs** (if applicable to artifact store mode):

- `sdd/core-alert-cycle/proposal` (Engram ID: persisted by orchestrator 2026-09-03)
- `sdd/core-alert-cycle/spec` (Engram ID: persisted by orchestrator 2026-09-03)
- `sdd/core-alert-cycle/design` (Engram ID: persisted by orchestrator 2026-09-03)
- `sdd/core-alert-cycle/tasks` (Engram ID: persisted 2026-09-04)
- `sdd/core-alert-cycle/apply-progress` (Engram ID: manually saved 2026-09-06 from apply-progress.md)
- `sdd/core-alert-cycle/verify-report` (Engram ID: manually saved 2026-09-07 from verify-report.md)
- `sdd/core-alert-cycle/archive-report` (this document, saved to Engram on archive completion)

---

## Next Steps

Changes 2 and 3 now consume the locked contracts:

- **Change 2 (composer-agent)**: Implements the `MessageComposer` port using Strands/AgentCore, adds fallback wiring for degraded mode, reuses message sanitization invariant from `domain/sanitize.py`.

- **Change 3 (cloud-deployment)**: AWS CDK (TypeScript), Lambda entrypoint, DynamoDB/SSM storage adapters, Telegram community notifier, EventBridge Scheduler. Reuses all seven ports, `RunAlertCycle` use case, storage shape contracts, and message sanitization from change 1.

---

## Sign-Off

**Status**: Archive complete. Change is closed.

**Shipped to production intent**: Ready for Change 2 (composer) and Change 3 (cloud deployment). Local testing, calibration, and live-source verification possible immediately with this change alone.

**Decision**: This change archive should never be modified. All future work proceeds through new changes in the active changes directory.

---

*Archive completed 2026-09-07. All 44 tasks complete, 35 requirements proven, 67 scenarios traced, 0 critical findings, 7 PRs merged to main, tests passing.*
