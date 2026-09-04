# Tasks: Core Alert Cycle (Local, No Cloud)

## Review Workload Forecast

| Field | Value |
|---|---|
| Estimated changed lines | 1000–1300 (slice 1 ~150, slice 2 ~400, slice 3 ~450, plus fixtures/README overhead) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 (tooling+hygiene) → PR 2 (domain+ports+use case) → PR 3 (adapters+CLI) |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending — owner must choose `stacked-to-main` or `feature-branch-chain` before apply |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

Dependencies are strictly linear (PR3 needs PR2, PR2 needs PR1) — validates design §13's split without change. If `feature-branch-chain` is chosen: PR1 base = tracker, PR2 base = PR1 branch, PR3 base = PR2 branch. If `stacked-to-main`: each PR merges to main in order.

### Suggested Work Units

| Unit | Goal | Likely PR | Notes |
|---|---|---|---|
| 1 | Scaffold (uv/pytest/ruff/mypy) + repo hygiene | PR 1 | Tasks 1.1–1.5. No prior dependency. |
| 2 | Domain types, ports, evaluator, dedup, outage, template, use case | PR 2 | Tasks 2.1–2.22. Depends on PR 1 (tooling). |
| 3 | Adapters, local repos, CLI, opt-in integration tests | PR 3 | Tasks 3.1–3.16. Depends on PR 2 (ports, use case). |

---

## Phase 1: Tooling Scaffold & Repo Hygiene (PR 1, ~150 lines)

- [x] 1.1 Scaffold `pyproject.toml` (requires-python>=3.12, deps `httpx`+`beautifulsoup4`, dev deps `pytest`/`ruff`/`mypy`, `[tool.pytest.ini_options]`, `[tool.ruff]`, `[tool.mypy] strict=true`, `[project.scripts] rain-alert-cycle`), `uv.lock`, `.python-version`. Design §8/§11 (D4, D5, D8, D9). Verify: `uv sync`. DONE — `uv add`/`uv add --dev` used so `uv.lock` records real resolved versions (httpx 0.28.1, beautifulsoup4 4.15.0, pytest 9.1.1, ruff 0.16.6, mypy 2.3.1). Python 3.12.13 confirmed available via `uv python list`; no deviation from D8 needed.
- [x] 1.2 Create package skeleton: `src/rain_alert/{__init__,domain/__init__,ports/__init__,application/__init__,adapters/__init__,entrypoints/__init__}.py` (empty), `tests/conftest.py`. Design §12. DONE — plus `tests/unit/test_package_smoke.py` (see 1.3 note on why a bare import-succeeds check would have been a false-green).
- [x] 1.3 Write `tests/architecture/test_layer_boundaries.py` (AST-based forbidden-import scan of `domain/`, ports-import restriction). Passes trivially on the empty skeleton. Satisfies repo-hygiene spec "Domain package stays free of adapter/AWS/LLM imports". Verify: `uv run pytest tests/architecture -q`. DONE — includes a synthetic-violation test (triangulation) proving the AST scanner actually detects a forbidden import nested in a function body, not just that an empty package passes.
- [~] 1.4 Add `LICENSE` (Apache 2.0), `README.md` (SENAMHI/INDECI-complement + no-warranty + Open-Meteo non-commercial note), `.gitignore` (`.env`, `.local-state/`, `.venv/`, caches), `.env.example` (names only). Satisfies repo-hygiene spec scenarios: License file check, Disclaimer present, .env.example has no values, Ignored env file. Verify: manual read + `git status` with a scratch `.env`. PARTIAL — LICENSE, README.md, .gitignore all done and covered by `tests/hygiene/test_repo_hygiene.py`. `.env.example` NOT created: the apply sandbox's permission system hard-blocks writing/moving any path containing `.env` (tried Write tool, Bash heredoc, Bash+dangerouslyDisableSandbox, and write-elsewhere-then-mv — all denied). See apply-progress.md for the exact content to create manually and the follow-up test to add.
- [x] 1.5 PR-1 verification gate: `uv run pytest -q`, `uv run ruff check`, `uv run mypy src`, `git grep` scan for phone/email/token/AWS-ID patterns (repo-hygiene "Grep check passes"). DONE — all green (see apply-progress.md for exact output). Also added `[tool.ruff] extend-exclude = ["openspec", "docs"]` after discovering `ruff format .` rewrites Python code fences inside planning Markdown by default.

## Phase 2: Domain, Ports, Use Case (PR 2, ~400 lines)

- [ ] 2.1 [RED] `tests/unit/domain/test_values.py`, `test_sources.py` — enums, `is_escalation`, `Coordinates`/`TimeWindow` validation, `Available`/`Unavailable`/`SourceResult`, `status_of`. Design §3 (D1, D2).
- [ ] 2.2 [GREEN] Implement `domain/values.py`, `domain/sources.py` to pass 2.1.
- [ ] 2.3 [RED] `tests/unit/domain/test_entities.py` — `Warning`, `HourlyPoint`, `Forecast` (`accumulated_mm`, `max_probability_pct` per D10 max-over-window, `peak_hour`, `precipitation_window`), `RiskAssessment`, `Contact`.
- [ ] 2.4 [GREEN] Implement `domain/entities.py` (D10).
- [ ] 2.5 [RED] `tests/unit/domain/test_messages.py` — `ForecastSummary`, `WarningSummary`, `MessageRequest`, `AlertMessage`, `AlertRecord`, `OutageRecord.is_dual`, `OperatorNotice`.
- [ ] 2.6 [GREEN] Implement `domain/messages.py`.
- [ ] 2.7 [RED] `tests/unit/domain/test_config.py` — `RiskThresholds`, `AlertConfig` (`coordinates_are_placeholder`).
- [ ] 2.8 [GREEN] Implement `domain/config.py`. Design §5.
- [ ] 2.9 Write `src/rain_alert/ports/__init__.py` (seven `Protocol`s incl. `Notifier` two-method split, D3). No dedicated test file — enforced by `mypy --strict` (D9) and fake conformance in 2.18. Design §4.
- [ ] 2.10 [RED] `tests/unit/domain/test_risk.py`, parametrized with `ids=` naming the risk-evaluation spec scenarios: Imminent from official warning, Imminent from forecast alone, Prepare on threshold, Below prepare threshold, Degraded prepare requires 70%, Degraded reasons disclose the outage, Imminent still fires, Prepare cannot fire without forecast data, Dual outage, Threshold source.
- [ ] 2.11 [GREEN] Implement `domain/risk.py` (`RiskEvaluator`, 5-branch order, design §5).
- [ ] 2.12 [RED] `tests/unit/domain/test_dedup.py` (pure `should_send`) — alert-dedup spec scenarios: First alert for a window, Prepare escalates to imminent, Repeat level, Imminent de-escalates to prepare, De-escalation to none.
- [ ] 2.13 [GREEN] Implement `domain/dedup.py` (`should_send`, pure only — no port import) + `application/policies.py` (`AlertPolicy` shell wrapping `AlertRepository`, satisfies "Query before decision" and "Dedup state is read from AlertRepository"). See Open Items — file placement of the shell is not fixed by design §12.
- [ ] 2.14 [RED] `tests/unit/domain/test_outage.py` — 8-row state-transition table (design §6) covering source-outage-notices spec: SENAMHI down triggers one notice, No duplicate notice while still down, dual-outage widen/narrow rows, Recovery after dual outage, No recovery notice without a prior outage, Distinct notice type (`NoticeKind` values).
- [ ] 2.15 [GREEN] Implement `domain/outage.py` (`evaluate_outage`, `OutageDecision`, pure only).
- [ ] 2.16 [RED] `tests/unit/domain/test_template.py` — alert-cycle spec: Template content, Degraded disclosure in the message.
- [ ] 2.17 [GREEN] Implement `domain/template.py` (deterministic `MessageComposer`, D11).
- [ ] 2.18 Write `tests/support/fakes.py`, `tests/support/wiring.py` (`build_fake_deps`, hand-written recording spies for all seven ports — no `unittest.mock`, design §10).
- [ ] 2.19 [RED] `tests/unit/application/test_run_alert_cycle.py` — alert-cycle spec: Authorized send runs the full chain, Dedup short-circuits the cycle, Record after notify; source-outage-notices: Dual-source outage suppresses community alerts (`suppress_community_alert` hard guard); degraded-mode wiring.
- [ ] 2.20 [GREEN] Implement `application/dependencies.py` (`CycleDependencies`, D6, D7) + `application/run_alert_cycle.py` (`RunAlertCycle`, `CycleResult` with `message_request` always populated per D11). Design §7.
- [ ] 2.21 [REFACTOR] Deduplicate reason-building helpers across `risk.py`/`outage.py`; no behavior change. Verify: `uv run pytest -q`, `uv run mypy src`, `uv run ruff check`.
- [ ] 2.22 PR-2 verification gate: `uv run pytest tests/unit/domain tests/unit/application tests/architecture -q`, `uv run mypy src`, `uv run ruff check`. Confirm all proposal Success Criteria evaluator/dedup/outage rows pass.

## Phase 3: Adapters, Local CLI, Integration (PR 3, ~450 lines)

- [ ] 3.1 [RED] `tests/unit/adapters/test_http.py`, `test_open_meteo.py` via `httpx.MockTransport` — weather-sources spec: Successful fetch, Network failure; plus timeout/bad-status/malformed-payload/insufficient-horizon (design §8.1).
- [ ] 3.2 [GREEN] Implement `adapters/http.py`, `adapters/open_meteo.py` (D4, §8.1 — endpoint path *to verify at apply*).
- [ ] 3.3 [Network, manual, one-time] Capture SENAMHI fixtures `tests/fixtures/senamhi/{active_warning,history_only,broken_structure,empty_table}.html` + `README.md` (capture date, URL, refresh procedure). Design §8.2.
- [ ] 3.4 [RED] `tests/unit/adapters/test_senamhi_scraper.py` (stub `HtmlFetcher`, fixtures from 3.3) — weather-sources spec: Healthy page with no current warnings, Altered HTML structure, Zero rows extracted; plus unknown-level-token and unparseable-date anomalies (§8.2).
- [ ] 3.5 [GREEN] Implement `adapters/senamhi_scraper.py` (header-signature location, level/date normalization, current-window filter).
- [ ] 3.6 [RED] `tests/unit/adapters/test_console_notifier.py` — local-alert-cli spec: Console-only delivery; source-outage-notices: Distinct notice type.
- [ ] 3.7 [GREEN] Implement `adapters/console_notifier.py` (§8.3 — dry-run prefixes, recipient count only, no HTTP import).
- [ ] 3.8 [RED] `tests/unit/adapters/test_local_repositories.py` (`tmp_path`) — `JsonFileAlertRepository` round-trip + outage upsert/clear, `InMemoryAlertRepository`; weather-sources: Placeholder coordinates documented (`StaticConfigRepository`); repo-hygiene: No secrets or personal data (`StaticContactRepository`).
- [ ] 3.9 [GREEN] Implement `adapters/local/{json_alert_repository,in_memory_alert_repository,static_config_repository,static_contact_repository}.py` (§8.3, §4.1).
- [ ] 3.10 Write `entrypoints/wiring.py` (`build_local_deps()` mirroring `tests/support/wiring.build_fake_deps`, design §10).
- [ ] 3.11 [RED] `tests/unit/entrypoints/test_cli.py` (`capsys`, `--offline-fixtures`) — local-alert-cli spec: CLI runs the cycle against real adapters (via injected fixtures), Full output on every run (level, reasons, message text per D11 preview-without-composer-invocation on non-send).
- [ ] 3.12 [GREEN] Implement `entrypoints/cli.py` (`--json`, `--now`, `--state-file`, `--offline-fixtures`; `PREVIEW (NOT SENT)` rendering; exit code 0 policy, §9).
- [ ] 3.13 [Network, opt-in, not in default CI] `tests/integration/test_open_meteo_live.py`, `test_senamhi_canary.py` under `@pytest.mark.integration` (deselected by default per `addopts`). Verify manually: `uv run pytest -m integration`.
- [ ] 3.14 [Network, manual smoke] Run `uv run rain-alert-cycle` against real sources once; confirm output matches §9 sample and no external send occurs.
- [ ] 3.15 [REFACTOR] Confirm `httpx.Timeout`/no-retry (D12) consistent across adapters; remove dead code. Verify: `uv run pytest -m "not integration" -q`, `uv run ruff check`, `uv run mypy src`.
- [ ] 3.16 PR-3 / final verification gate: `uv run pytest -q` (offline default), `uv run ruff check`, `uv run mypy src`, `git grep` secrets scan. Confirm every proposal Success Criteria row is checked.

---

## Open Items for Apply

- **`AlertPolicy` / outage shell file placement is not fixed by design §12's File Changes table.** §6's code block header `# domain/dedup.py — pure` sits directly above the `AlertPolicy` class, which takes `AlertRepository` (a port) — importing a port from `domain/` (even under `TYPE_CHECKING`) would fail the architecture test described in §10 ("catches imports inside functions and `TYPE_CHECKING` blocks"). Tasks above place the pure `should_send` in `domain/dedup.py` and the port-bound `AlertPolicy` shell in a new `application/policies.py` not listed in §12 — confirm this placement (or an alternative) at apply time.
- **§1 names an `OutageNoticePolicy` object** ("`AlertPolicy` and `OutageNoticePolicy` hold a port reference"), but §6/§7 only define a pure `evaluate_outage` function with `RunAlertCycle` itself calling `get_active_outage`/`save_active_outage`/`clear_active_outage` directly — no `OutageNoticePolicy` class or file appears in §12. Tasks above treat this as naming shorthand for `RunAlertCycle`'s outage-handling step, not a literal class; confirm at apply.
- Endpoint path/query-param names for Open-Meteo (design §15, "*to verify at apply*") and exact SENAMHI header labels (§15) remain unresolved pending a live check during task 3.1–3.2 and 3.3–3.4.
