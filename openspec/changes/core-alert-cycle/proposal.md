# Proposal: Core Alert Cycle (Local, No Cloud)

Source of truth: `docs/superpowers/specs/2026-09-03-rain-alert-core-design.md`. This proposal slices it; it does not restate it. Section 3 (rejected alternatives) is closed.

## Intent

Ferreñafe floods on rainfall that is trivial elsewhere, and today nothing warns the community ahead of time. The full system spans AWS, an LLM composer, and IaC — too much to build or trust in one step. This change builds the **decision engine and its real data feeds locally**, so the risk rules can be calibrated against real SENAMHI and Open-Meteo data before a single cloud resource exists. Success is an operator running one command and seeing exactly what would have been sent, and why.

## Chained change map

| # | Change | Content |
|---|---|---|
| 1 | **core-alert-cycle** (this) | Domain, ports, `RunAlertCycle`, source adapters, local CLI, tooling, repo hygiene |
| 2 | composer-agent | Strands composer on AgentCore, output validator (spec 7.5), fallback wiring |
| 3 | cloud-deployment | CDK TypeScript, Lambda entrypoint, DynamoDB/SSM adapters, Telegram operator notifier, EventBridge Scheduler |

## Scope

### In Scope
- `domain/`: `Warning`, `Forecast`, `RiskAssessment`, source-status results (spec 6.1–6.2), `RiskEvaluator`, `AlertPolicy`, deterministic fallback message template.
- **New rule (not in spec):** both sources unavailable in one cycle → level `none`, no community alert, **one** deduplicated technical notice to the operator for the outage duration, one recovery notice when a source returns. Lives in the domain; notices use the `Notifier` port with a distinct notice type; dedup state via `AlertRepository`.
- `ports/`: all seven Protocols from spec 10.1, including `MessageComposer` (defined, not implemented).
- `application/RunAlertCycle`: steps 1–5 and 7 of spec 4; step 6 (compose) resolves to the deterministic template in this change.
- `adapters/`: `open_meteo` client, `senamhi_scraper` with breakage detection (spec 6.4), console `Notifier`, file/in-memory `AlertRepository` + `ContactRepository` + `ConfigRepository`.
- `entrypoints/`: local CLI running the real cycle against real sources, printing what would have been sent.
- Tooling scaffold: `uv` project, `pytest`, `ruff`.
- Public-repo hygiene (spec 11): Apache 2.0 `LICENSE`, `README` with the SENAMHI/INDECI-complement and no-warranty disclaimer plus Open-Meteo non-commercial note, `.gitignore`, `.env.example` (names only).

### Out of Scope
- Anything AWS: Lambda, DynamoDB, SSM, EventBridge, CDK, `moto` tests → change 3.
- Any LLM, Strands, AgentCore, or the output validator → change 2.
- Telegram/SES notifiers → change 3. Automatic recipient delivery → v2.
- Admin web, public site, SMS (spec 2 non-goals).
- Real contact data of any kind, ever.

## Capabilities

### New Capabilities
- `risk-evaluation`: source-status-aware rules producing `RiskAssessment` (spec 6.2, 6.3).
- `alert-dedup`: `AlertPolicy` new/escalation/no-de-escalation rules (spec 6.1).
- `source-outage-notices`: dual-outage rule, deduplicated operator notices, recovery notice.
- `weather-sources`: Open-Meteo forecast and SENAMHI warning acquisition with availability semantics and scraper-breakage detection (spec 5, 6.4).
- `alert-cycle`: `RunAlertCycle` orchestration of the cycle with the deterministic composer.
- `local-alert-cli`: dry-run execution against real sources for calibration (spec 10.1).
- `repo-hygiene`: license, disclaimer, secret/personal-data exclusion (spec 11).

### Modified Capabilities
- None. `openspec/specs/` is empty.

## Approach

Hexagonal, domain-first, strict TDD. The domain is pure Python with zero AWS/HTTP/HTML/LLM imports and is tested table-driven in milliseconds. Ports are `typing.Protocol`; dependencies are constructor-injected into `RunAlertCycle`, so the same use case runs today with fakes plus two real HTTP adapters, and in change 3 with AWS adapters — no domain edits. The `MessageComposer` port is declared now and satisfied by the deterministic template, which makes change 2 an adapter swap rather than a rewrite. Scraper resilience is a domain contract, not adapter courtesy: unrecognized HTML or zero rows is `unavailable`, never "calm".

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `pyproject.toml`, `uv.lock` | New | uv project, pytest, ruff |
| `src/rain_alert/domain/` | New | Entities, evaluator, policy, fallback template |
| `src/rain_alert/ports/` | New | Seven Protocols |
| `src/rain_alert/application/` | New | `RunAlertCycle` |
| `src/rain_alert/adapters/` | New | Open-Meteo, SENAMHI scraper, console notifier, local repositories |
| `src/rain_alert/entrypoints/` | New | Local CLI |
| `tests/` | New | Domain tables, HTML fixtures (incl. broken-structure), use-case fakes |
| `LICENSE`, `README.md`, `.gitignore`, `.env.example` | New | Public-repo hygiene |

## Success Criteria

Named scenarios from design spec 6.3, preserved verbatim, must pass as tests:

- [ ] 2017-03-12, 29.8 mm forecast in 24 h, probability ≥ 70 % → **imminent**.
- [ ] 10 mm in 48 h at ≥ 60 % with a SENAMHI yellow warning → **prepare**.
- [ ] 9 mm in 48 h at ≥ 60 % with a SENAMHI yellow warning → **none** (below the 9.5 mm *Prepare* threshold).
- [ ] SENAMHI unavailable, 15 mm in 48 h at 65 % → **none** (degraded mode requires 70 %).

Plus:

- [ ] A SENAMHI HTML fixture with altered structure yields `unavailable`, never "available with zero warnings".
- [ ] A healthy fixture with zero current warnings yields `available` with an empty warning list.
- [ ] Re-running the cycle with the same level and window sends nothing (dedup); escalation sends; de-escalation sends nothing.
- [ ] SENAMHI unavailable + qualifying forecast → alert body states the official source was unavailable, and one operator technical notice is emitted.
- [ ] Both sources unavailable across three consecutive cycles → level `none`, zero community alerts, exactly **one** operator notice; the next cycle with one source back emits exactly one recovery notice.
- [ ] `uv run pytest` and `uv run ruff check` pass; the domain package imports nothing from `adapters/`, `boto3`, `requests`, or any LLM SDK (enforced by test).
- [ ] The CLI runs the full cycle against real SENAMHI and Open-Meteo and prints level, reasons, and the message that would have been sent, sending nothing.
- [ ] `git grep` finds no phone numbers, emails, tokens, or AWS account IDs; `.env.example` contains names only.

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Spec 6.3 scenario 2 originally said 9 mm → *prepare*, contradicting the ≥ 9.5 mm *Prepare* rule | Resolved 2026-09-03 | Owner decision: the 9.5 mm rule governs (Reque p95 evidence). Scenario corrected to 10 mm → *prepare*; 9 mm → *none* added as a boundary case. Design spec updated |
| SENAMHI HTML changes and breaks the scraper | High | Breakage → `unavailable` + operator notice; fixtures pinned; failure degrades the system, never blinds it |
| Real-source CLI tests are flaky/networked | Medium | Real sources only in the CLI and opt-in integration tests; unit suite is fixture-only and offline |
| Ports designed against local fakes leak assumptions that break AWS adapters in change 3 | Medium | Port signatures derived from spec 9 storage shapes; `AlertRepository` dedup queries modelled on the `alerts-sent` key structure |
| Outage-notice dedup state has no durable store in this slice | Medium | File-based `AlertRepository`; the contract is what change 3 must honour, and it is covered by tests |
| Slice exceeds the 400-line review budget | High | Chained PRs (below) |

## Rollback Plan

Greenfield: nothing existing can break, and no external state is written. Per slice, rollback is `git revert` of that slice's commits or deleting its branch. The local CLI never sends and never mutates cloud resources, so a bad revert has no external blast radius. If the SENAMHI scraper proves unmaintainable, revert only the adapter — the domain, ports, and use case stand, and Open-Meteo degraded mode still produces alerts.

## Dependencies

- External: SENAMHI warnings page (HTML, no API), Open-Meteo API (no key, non-commercial). Both read-only, unauthenticated.
- Open items inherited from spec 12 that this change needs: exact Ferreñafe coordinates (may use a documented placeholder in the config fake).
- Downstream: change 2 consumes the `MessageComposer` port and the fallback template; change 3 consumes all seven ports and the `RunAlertCycle` signature. Changing a port after change 1 merges is a breaking change for both.

## Review Workload Estimate

Rough total: **900–1200 changed lines** including tests. This exceeds the 400-line PR budget; chained PRs are expected. Natural slice boundaries, each independently verifiable:

| Slice | Content | Est. lines |
|---|---|---|
| 1 | Tooling scaffold + repo hygiene (uv, pytest, ruff, LICENSE, README, `.gitignore`, `.env.example`) | ~150 |
| 2 | Domain + ports + `RunAlertCycle` with fakes, fully unit-tested | ~400 |
| 3 | Real adapters (Open-Meteo, SENAMHI scraper) + console notifier + local CLI | ~450 |

`sdd-tasks` owns the final forecast and the chain decision.
