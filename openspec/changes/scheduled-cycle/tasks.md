# Tasks: Scheduled Cycle (EventBridge → Lambda → SSM + DynamoDB, composing with the agent)

This document exceeds the generic 530-word task budget deliberately, per
explicit instruction to follow this repository's own precedent
(`openspec/changes/composer-agent/tasks.md`). The change spans two
languages, two deployment units and a live stack; compressing the
RED/GREEN/mutation detail here would move the cost to apply time, exactly
as `design.md` states about itself.

Four phases, one per design §6's File Changes table slice. Dependencies are
linear (1 → 2 → 3 → 4): slice 3 (infra) needs slices 1 and 2 already
merged, because the Lambda bundle **is** `src/rain_alert`. Strict TDD is
ON: `[RED]` names the test and the failure it expects and why; `[GREEN]`
is the minimal implementation that turns it green. `[Network]` marks a
live-documentation/API-shape lookup (no AWS account touched, no deploy).
`[Owner, AWS]` marks a task that touches the live `ferrenafe` account —
**sdd-apply MUST NOT run these**; they are the owner's to run and confirm.

## Review Workload Forecast

| Field | Value |
|---|---|
| Estimated changed lines | 2150–3350 |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 → PR 2 → PR 3 → PR 4 |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending — orchestrator asks before apply |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

**Why, per slice** — every prior change in this series overran its own
estimate 2–10x (composer-agent's slice 1 planned ~550–800, forecast
900–1400; slice 2 planned ~400–650, forecast 700–1100). This slice count
inherits the same granularity and the same risk:

| Slice | Design estimate basis | This forecast | Driver |
|---|---|---|---|
| 1 — adapters | 2 adapters, "mechanical mapping" per D28 | 700–950 | Pure mapper tests (pk/sk construction, sentinel-ordering table over every `Level`, TTL relation), 9-field SSM parser with a raise-path per field, `moto`-driven pagination for both `Query` and `GetParametersByPath`, the forced-dummy-credentials fixture and its own test. D28 undersells this as "mechanical" — the fault-path count is the multiplier. |
| 2 — entrypoints | `run_once` extraction + handler, "the shared sequence" per D29 | 550–800 | A golden-output byte-identity harness for the CLI (new infrastructure, not just a test), the handler's three independent invariants (ignores event, does not catch, logs unconditionally), the architecture-boundary scan with its own triangulation case, `build_cloud_deps`'s console-notifier-only twin. |
| 3 — contract + CDK | D25, D30–D33, D35 — "this design exceeds budget deliberately" | 800–1300 | The largest single-language surface: one Lambda, one table, one scheduler, three alarms, one SNS topic, one IAM role with five distinct grants, each asserted as its own `Template.hasResourceProperties` test plus a mutation record. If it lands over ~1000 lines alone, split it further into 3a (Lambda/table/IAM/schedule) and 3b (alarms/SNS/contract) before opening the PR — flagged here rather than pre-decided, because the true count is only known once written. |
| 4 — docs | Runbook + two doc corrections | 150–300 | Prose only; the low-risk slice. |

### Suggested Work Units

| Unit | Goal | PR | Notes |
|---|---|---|---|
| 1 | `DynamoDbAlertRepository` + `SsmConfigRepository`, pure + `moto` | PR 1 | Tasks 1.1–1.37. Offline (dummy credentials, no network). Independent of everything else; nothing in `main` constructs these adapters yet. |
| 2 | `run_once` extraction, `lambda_handler`, `build_cloud_deps`, architecture test | PR 2 | Tasks 2.1–2.23. Depends on PR 1's two adapters existing so `build_cloud_deps` can wire them. |
| 3 | `contracts/scheduled-cycle.json` + the CDK stack (synth-only, no deploy) | PR 3 | Tasks 3.1–3.30. Depends on PR 1 + PR 2 merged (the Lambda bundle is `src/rain_alert` as it stands after both). Split into 3a/3b if it overruns ~1000 lines — see forecast note above. |
| 4 | Runbook + region correction in `CLAUDE.md` and the design doc | PR 4 | Tasks 4.1–4.6. Depends on PR 3 only for accuracy of the runbook's commands; otherwise independent prose. |
| — | Owner-run live deploy and verification | not a PR | Tasks O.1–O.11. Runs against the real `ferrenafe` account after PR 1–4 are merged. Never part of an automated apply. |

---

## Phase 1: DynamoDB + SSM Adapters (PR 1, offline)

`moto` is used only for the `Query`/`GetParametersByPath` contract tests
(D28). This is not a `unittest.mock` violation: a hand-written fake would
have to re-implement DynamoDB's key-condition evaluator inside the test,
and the test would then pass against a `KeyConditionExpression` real
DynamoDB rejects — the exact "a check that looked like it ran and did
not" failure this project's `CLAUDE.md` names. `moto` is a service double
in the same category as `FileHtmlFetcher`, already shipped in
`tests/support/fakes.py`.

### Offline guarantee (must exist before either adapter's client-touching test)

- [x] 1.1 [RED] Add `test_aws_credentials_are_forced_to_dummy_values` to `tests/conftest.py` (or a new `tests/unit/test_conftest_fixtures.py`): inside the fixture's scope, `boto3.Session().get_credentials().access_key == "testing"`. Expects: fails today — no such fixture exists, so the resolved credential is whatever the ambient environment holds (or `None`).
- [x] 1.2 [GREEN] Add a session-scoped `autouse` fixture in `tests/conftest.py` forcing `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `AWS_DEFAULT_REGION` to dummy values via `monkeypatch`. Add `moto` to `pyproject.toml`'s dev group. Verify: `uv run pytest tests/unit/test_conftest_fixtures.py -q`

### `DynamoDbAlertRepository` — pure mapping (D26, D27)

- [x] 1.3 [RED] `tests/unit/adapters/test_dynamodb_alert_repository.py` — key construction: alert item `pk=f"ALERT#{city_slug}"`, `sk=f"{window_start.isoformat()}#{level.value}"`; outage item `pk=f"OUTAGE#{city_slug}"`, `sk="CURRENT"`. Expects `ModuleNotFoundError`: adapter does not exist.
- [x] 1.4 [GREEN] `adapters/dynamodb_alert_repository.py` — pure key-construction functions, reusing `adapters/serialization.py`'s `alert_record_to_dict`/`from_dict` and `outage_record_to_dict`/`from_dict` with no translation layer. Verify: `uv run pytest tests/unit/adapters/test_dynamodb_alert_repository.py -k key -q`
- [x] 1.5 [RED] Sentinel-ordering test, parametrized over every `Level` member: `f"{ts}#{level.value}" < upper_bound_sort_key(ts)` where the bound uses `￿` (U+FFFF), never `~`. Expects `AttributeError`: `upper_bound_sort_key` does not exist. Mutation proof recorded at 1.6.
- [x] 1.6 [GREEN] Implement `upper_bound_sort_key(latest_start) -> str`. Verify green, then temporarily swap `￿` for `~` and record that the parametrized case for at least one `Level` member now fails — proving the sentinel choice, not just its presence, is under test. Revert.
- [x] 1.7 [RED] TTL relation test: `expires_at - int(sent_at.timestamp()) > dedup_lookback_hours * 3600`, computed from the `AlertConfig` under test with `ALERT_RETENTION_DAYS = 30` (per `alert-persistence` spec — **this supersedes design D27's proposed 365**; the spec pinned 30 days after the design was written, and this task is where that resolution is recorded). Expects `AttributeError`: no `compute_expires_at` function.
- [x] 1.8 [GREEN] Implement `compute_expires_at(sent_at) -> int` with `ALERT_RETENTION_DAYS = 30`. Mutation proof: temporarily lower the constant to 2 days (below `dedup_lookback_hours=72h ≈ 3 days`) and record the relation test going red; revert. Verify: `uv run pytest tests/unit/adapters/test_dynamodb_alert_repository.py -k ttl -q`
- [x] 1.9 [RED] Outage item mapping carries no `expires_at` attribute at all (not `None` — absent). Expects: fails if the mapper writes the key with a null value instead of omitting it.
- [x] 1.10 [GREEN] Confirm/implement the outage mapper omits the attribute entirely.

### `DynamoDbAlertRepository` — client-touching, `moto` (D28)

- [x] 1.11 [RED] `alerts_with_window_start_between` builds `KeyConditionExpression: pk = :pk AND sk BETWEEN :lo AND :hi` and `ScanIndexForward=True`, against a `moto`-backed table. Expects: repository has no client-injecting constructor yet. **Correction (fix round 1)**: this task's original text also claimed `ConsistentRead=True` as proven by this same `moto`-backed test. It was not — `moto`'s DynamoDB backend is synchronous in-memory and cannot distinguish a consistent read from an eventually consistent one, so the "a just-written record is visible" scenario passes with or without the kwarg. A fresh-context review deleted `ConsistentRead=True` from both call sites and got the whole suite green. See task 1.11a for the actual proof.
- [x] 1.11a [RED] (fix round 1) `TestConsistentReadIsActuallyRequested` — a hand-written kwarg-recording spy wrapping the real `moto`-backed client, asserting `ConsistentRead=True` on both the `Query` (via the paginator) and the outage `GetItem` call. Expects: fails with `KeyError: 'ConsistentRead'` once the kwarg is actually removed from production code — verified by mutation (recorded in apply-progress).
- [x] 1.12 [GREEN] `DynamoDbAlertRepository(client: Any | None = None)`, lazily built on first call (mirrors `BedrockAgentCoreInvoker`). Verify: `uv run pytest tests/unit/adapters/test_dynamodb_alert_repository.py -k query -q`
- [x] 1.13 [RED] Pagination: seed more items than one `Query` page holds; assert every item across `LastEvaluatedKey` pages is returned. Expects: fails if the query reads only the first page.
- [x] 1.14 [GREEN] Use the `boto3` paginator. Mutation proof: temporarily return after the first page (skip the `LastEvaluatedKey` loop) and record the seeded-item test going red; revert.
- [x] 1.15 [RED] `record_alert` is a plain `PutItem` with no `ConditionExpression`: writing the same key twice overwrites (last-write-wins), never raises.
- [x] 1.16 [GREEN] Implement as an unconditional `put_item`. Verify: `uv run pytest tests/unit/adapters/test_dynamodb_alert_repository.py -k record_alert -q`
- [x] 1.17 [RED] Round-trip: a fully populated `AlertRecord` (including `reasons` and `composer`) written by `DynamoDbAlertRepository` and read back equals the original.
- [x] 1.18 [GREEN] Confirm the full path (1.4/1.12/1.16 composed) satisfies this with no new production code. Verify: `uv run pytest tests/unit/adapters/test_dynamodb_alert_repository.py -q`

### `SsmConfigRepository` (D28, D32)

- [x] 1.19 [RED] `tests/unit/adapters/test_ssm_config_repository.py` — full load from valid parameters returns an `AlertConfig` with SSM values for the seven operator-editable fields and code constants for `city`/`city_slug`/`region`/`timezone`. Expects `ModuleNotFoundError`.
- [x] 1.20 [GREEN] `adapters/ssm_config_repository.py` — one `GetParametersByPath` call, JSON document parsing per D32's layout (`location`, `thresholds`, `checklist` as a JSON array — **never `StringList`**, per the reproduction below — `active-channel`, `composer`, `forecast-hours`, `dedup-lookback-hours`). **Superseded (fix round 1, MUST item 2)**: `GetParametersByPath` forces a prefix-wide IAM grant that would let the adapter silently pick up anything an operator later parks under the same prefix. Switched to one `GetParameters` call over the seven exact names — `GetParameters` accepts up to ten names per call, well above seven — so the Phase 3 IAM grant can list seven exact ARNs instead of one wildcard resource.
- [x] 1.21 **Rationale task, no test**: record in the module docstring why `checklist` is `String` holding a JSON array and never `StringList` — two of the five shipped checklist items contain commas (`"Limpia canaletas, techos y desagües cercanos."`, `"Ten a mano una linterna, un botiquín y los teléfonos de emergencia."`), and SSM's `StringList` is a bare comma split with no escaping, so those two items would each arrive as two fragments a resident reads as separate instructions. This must be readable in the code, not only in this task, so a later "simplification" back to `StringList` is caught by someone reading the adapter, not only by someone reading `openspec/`.
- [x] 1.22 [RED] `city_slug` is never read from SSM even if a stray parameter existed under the prefix at that name (typo-tolerance test): the loader only maps the seven named keys and ignores anything else under the path.
- [x] 1.23 [GREEN] Confirm/implement the parser maps by an explicit allow-list of parameter names, not a wildcard merge. **Superseded mechanism (fix round 1, MUST item 2)**: once the adapter reads by `GetParameters(Names=[...])` rather than `GetParametersByPath`, the "allow-list filter over a wildcard response" this task describes no longer exists — a stray parameter is never requested at all, which is stronger than filtering one out after the fact. `TestGetParametersRequestsExactNamesOnly::test_only_the_seven_exact_names_are_requested` (fix round 1) proves the request itself, with a kwarg-recording spy, rather than only inferring it from the loaded result the way 1.22's original test does.
- [x] 1.24 [RED] Operator-supplied `coordinates` without `coordinates_source` raises, rather than defaulting to `public_reference`. Mutation proof: the naive fix is inferring a default when the key is absent — assert the specific raise, so a future "just default it" change is caught by this test, not merely by review.
- [x] 1.25 [GREEN] Implement: `coordinates_source` required whenever `coordinates` is present; no inference path exists in code at all (not merely untested).
- [x] 1.26 [RED] Missing/empty/malformed-JSON/out-of-range parameter raises, parametrized over each of the seven fields independently (location, each of the two threshold sub-shapes, checklist, active-channel, composer, forecast-hours, dedup-lookback-hours). Expects: at least one field currently has no validation and silently proceeds or defaults.
- [x] 1.27 [GREEN] Implement per-field strict validation, matching `StaticConfigRepository`'s existing strictness. Verify: `uv run pytest tests/unit/adapters/test_ssm_config_repository.py -q`
- [x] 1.28 [RED] `active_channel` other than the literal `"console"` raises (not merely "any non-empty string is fine").
- [x] 1.29 [GREEN] Validate against the one-member allow-list `["console"]`.
- [x] 1.30 [RED] Instance-level memoization: calling `.load()` twice on the same `SsmConfigRepository` instance issues exactly one `GetParameters` call (D29's "one cycle, one configuration" correctness argument, not merely a cost optimization). Expects: fails if each `.load()` re-fetches. (Fix round 1: reworded from `GetParametersByPath` to `GetParameters`, per MUST item 2; the spy and the assertion were unaffected by the mechanism change, only its name.)
- [x] 1.31 [GREEN] Cache the parsed result on the instance after the first successful load.
- [x] 1.32 [RED] `moto`-backed pagination test seeding more parameters under the prefix than one `GetParametersByPath` page holds. **Superseded (fix round 1, MUST item 2)**: `GetParameters` requests exactly seven names in one call and does not paginate at all — there is no pagination behaviour left to test. Replaced by `TestGetParametersRequestsExactNamesOnly`, which proves the adapter requests only those seven names and surfaces a genuinely-absent one through `InvalidParameters` by name.
- [x] 1.33 [GREEN] Use the `boto3` paginator. Mutation proof: return only the first page and record the seeded test going red; revert. Verify: `uv run pytest tests/unit/adapters/test_ssm_config_repository.py -q` **Superseded for the same reason as 1.32** — no paginator is used any more; the mutation-proof discipline instead applies to the new `InvalidParameters` handling (verified in apply-progress).

### Close-out

- [x] 1.34 **Security review** (fresh context) on the PR-1 branch diff before opening the PR. Scope: the forced-dummy-credentials fixture (confirm it cannot leak a real session's credentials into a `moto` test), the SSM parameter shapes (confirm no secret-shaped value — nothing here should ever be a `SecureString` candidate), the DynamoDB item shape (no PII).
- [x] 1.35 PR-1 verification gate: `uv run pytest tests/unit/adapters/test_dynamodb_alert_repository.py tests/unit/adapters/test_ssm_config_repository.py tests/unit/test_conftest_fixtures.py -q`; `uv run ruff check`; `uv run mypy src`.
- [x] 1.36 Confirm the whole default suite still passes with AWS credential env vars unset: `env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN uv run pytest -q`. **Reworded (fix round 1, MUST item 3)**: `tests/conftest.py`'s session-scoped fixture forces its own dummy credentials regardless of the ambient environment, so this run passing does not prove "no test needs credentials" — it always passes now, by construction. What it verifies is narrower and still worth running: that nothing in this suite depends on the *ambient* environment being clean, and that unsetting those variables doesn't change the outcome. The actual "no network, no credentials" guarantee is enforced structurally by the credentials fixture plus the session-scoped socket guard added in this fix round, verified directly by `tests/unit/test_conftest_fixtures.py`.
- [x] 1.37 `uv add moto --dev` diff and `pyproject.toml` lockfile committed; confirm no compiled-extension surprises for the offline suite (this is unrelated to D30's Lambda-bundle no-`.so` check, which is a PR-3 concern, but the dev dependency should not itself require network at test time).

---

## Phase 2: `run_once` Extraction, Lambda Handler, Cloud Wiring (PR 2)

**`run_once`'s extraction is the riskiest task in this whole change** — it
touches the CLI that residents' operators already run, and
`local-alert-cli`'s spec requires byte-identical output. It is sequenced
first in this phase, with its own verification, ahead of anything
(`lambda_handler`, `build_cloud_deps`) that depends on it existing.

### Golden baseline (captured before any code changes in this phase)

- [x] 2.1 **Setup, not TDD-gated.** Run `uv run rain-alert-cycle --offline-fixtures <dir> --now <pinned>` and its `--json` variant against the current, unextracted `cli.py:main`; save stdout, stderr and exit code as golden fixtures under `tests/fixtures/golden/`. This baseline is what every RED test below compares against — it must be captured **before** `cli.py` changes. **Done**: `--now 2026-09-28T12:00:00+00:00`, `--offline-fixtures` pointed at a copy of `tests/fixtures/senamhi/warnings_table.html`/`tests/fixtures/open_meteo/forecast_72h.json` renamed to `senamhi.html`/`open_meteo.json`; both the plain and `--json` runs captured before `cli.py` was touched.

### The extraction itself

- [x] 2.2 [RED] `tests/unit/entrypoints/test_run_once.py::test_run_once_executes_the_calibrated_sequence` — with `build_fake_deps`, `run_once(deps, publisher, report=...)` performs `config.load()`, `RunAlertCycle(deps).execute()`, and the forgiving snapshot guard, returning `(CycleResult, AlertConfig)`. Expects `ModuleNotFoundError`: `entrypoints/run_once.py` does not exist.
- [x] 2.3 [GREEN] Create `entrypoints/run_once.py`, extracting `cli.py:396-409` verbatim (including `SNAPSHOT_FAILURE_PREFIX`); move `default_now` here; re-export `default_now` from `cli.py` so `cli.default_now`, its `__all__` entry, and every docstring citation (`tests/hygiene/test_docstring_citations.py`) still resolve. Verify: `uv run pytest tests/unit/entrypoints/test_run_once.py`. **Amendment, apply time**: `as_json` (and its private helpers `source_notes`/`_recent_alerts`/`MAX_RECENT_ALERTS`) moved here too, not only `default_now` — see apply-progress's "A real gap this task's own text left open" for why.
- [x] 2.4 [RED] `tests/unit/entrypoints/test_cli.py::test_cli_output_is_byte_identical_after_extraction` — `cli.main`'s stdout/stderr/exit code, run against the same pinned `--now`/`--offline-fixtures` as 2.1, equal the golden fixture byte-for-byte. Expects: fails until `cli.py:main` is rewired to delegate. **Correction, apply time**: against the *original, unmodified* `cli.py` this assertion trivially holds (the golden fixture was captured from that exact code), so it cannot be RED at that point by construction — the real mutation proof recorded in apply-progress instead breaks the *rewired* `main` (bypassing `--json`) and shows this test catching that regression.
- [x] 2.5 [GREEN] Rewire `cli.py:main` to call `run_once`; argparse, `render()`, `as_json()` and the exit code stay in `cli.py`, untouched in shape. **This is the extraction's own gate — do not start `lambda_handler` (2.8+) until 2.4 is green.** Verify: `uv run pytest tests/unit/entrypoints/test_cli.py -k byte_identical`
- [x] 2.6 [RED] `test_run_once.py::test_snapshot_publish_failure_does_not_cost_the_alert` — `publisher.publish` raises after `notifier.send_alert` already ran; `run_once` returns normally and the `SNAPSHOT_FAILURE_PREFIX` line appears on the `report` stream.
- [x] 2.7 [GREEN] Confirm 2.3 already satisfies this. Mutation proof: temporarily remove the guard's `except` and record the test going red (the publish failure now propagates and costs nothing to notice, which is the wrong direction); revert. **Done**: reverted `except` removed, live `OSError: bucket on fire` propagated uncaught through `run_once`, confirming the guard's teeth (recorded in apply-progress).

### `lambda_handler` (D29)

- [x] 2.8 [RED] `tests/unit/entrypoints/test_lambda_handler.py::test_handler_ignores_event_payload` — a hostile `event` dict and an empty `event` dict produce an identical `CycleResult` and logged document. Expects `ModuleNotFoundError`: `lambda_handler.py` does not exist.
- [x] 2.9 [GREEN] Create `entrypoints/lambda_handler.py::handler(event, context)` — calls `wiring.build_cloud_deps()`, then `run_once`; reads nothing from `event`. Verify: `uv run pytest tests/unit/entrypoints/test_lambda_handler.py -k ignores_event`
- [x] 2.10 [RED] `test_lambda_handler.py::test_handler_does_not_catch_construction_failures` — a faked `build_cloud_deps` that raises propagates out of `handler` uncaught; no `CycleResult`, no logged document, no publish attempt.
- [x] 2.11 [GREEN] Confirm `handler` wraps nothing in `try`/`except` around dependency construction or `run_once`. Mutation proof: temporarily add a broad `except Exception` around the call and record the test going red (the exception is now swallowed, which is exactly the fabricated-success failure the `scheduled-execution` spec forbids); revert. **Done**: mutation made `test_handler_does_not_catch_construction_failures` fail with `Failed: DID NOT RAISE ValueError`; reverted.
- [x] 2.12 [RED] `test_lambda_handler.py::test_handler_logs_as_json_for_every_completed_outcome` — parametrized over `none`/degraded/deduplicated outcomes via `build_fake_deps`: one `as_json` line to stdout, `handler` never raises for a completed cycle. **Note**: implemented as `none`/`degraded` (via `Unavailable`) plus a separate `test_a_deduplicated_cycle_still_logs_a_document` for the dedup outcome — same three outcomes the task names, split for a clearer failure message per outcome.
- [x] 2.13 [GREEN] Implement the unconditional log line. Verify: `uv run pytest tests/unit/entrypoints/test_lambda_handler.py -k outcome`
- [x] 2.14 [RED] `test_lambda_handler.py::test_dedup_window_derives_from_actual_clock` — two `handler` invocations spaced other than six hours apart (fake clock) still derive the query window from `now`, never a hardcoded period constant.
- [x] 2.15 [GREEN] Confirm/implement the window bound computation reads the injected clock only. Verify: `uv run pytest tests/unit/entrypoints/test_lambda_handler.py`

### `build_cloud_deps` (D6, D14 unchanged)

- [x] 2.16 [RED] `tests/unit/entrypoints/test_wiring.py::test_build_cloud_deps_constructs_only_console_notifier` — the cloud twin of the existing local-only assertion.
- [x] 2.17 [GREEN] `wiring.py::build_cloud_deps()` — the second leaf set over `CycleDependencies`: `SsmConfigRepository` (memoized), `SenamhiWarningScraper(HttpxHtmlFetcher)`, `OpenMeteoForecastProvider`, `select_composer(config, ...)` **unchanged**, `StaticContactRepository`, `ConsoleNotifier`, `DynamoDbAlertRepository`. **Amendment**: `DynamoDbAlertRepository(table_name)` needed a table-name constant nowhere yet defined in this codebase — added `TABLE_NAME = "ferrenafe-alerts-sent"` to `dynamodb_alert_repository.py` (matching the CDK `tableName` design.md D26 already commits to), the same pattern `ssm_config_repository.py`'s `SSM_PATH_PREFIX` already uses.
- [x] 2.18 [RED] `test_wiring.py::test_build_cloud_deps_reuses_select_composer` — `select_composer` is invoked with the loaded config exactly as `build_local_deps` does; no second switch is introduced.
- [x] 2.19 [GREEN] Confirm/implement. Verify: `uv run pytest tests/unit/entrypoints/test_wiring.py`

### Architecture boundary (D29)

- [x] 2.20 [RED] `tests/architecture/test_layer_boundaries.py::test_lambda_handler_never_imports_local_only_modules` — AST scan asserting `entrypoints/lambda_handler.py` imports neither `entrypoints.cli` nor `adapters.local.json_alert_repository` nor `adapters.local.static_config_repository`; includes a planted-violation fixture proving the scan can fail, not only that a clean tree passes.
- [x] 2.21 [GREEN] Extend the forbidden-roots constants and implement the scan. Verify: `uv run pytest tests/architecture`. **Mutation proof**: temporarily made the new scanner return `set()` unconditionally; `test_the_scan_detects_a_planted_violation` failed (`assert set() == {...three modules...}`); reverted.

### Close-out

- [ ] 2.22 **Security review** (fresh context) on the PR-2 branch diff. Scope: the handler's uncaught-exception posture (confirm nothing beyond the intended `as_json` document reaches CloudWatch Logs), the hostile-`event` handling. **Not run by sdd-apply** — the launching prompt forbids dispatching subagents, so a genuinely fresh-context review cannot happen from within this session (same posture as Phase 1's task 1.34). Flagged as outstanding for the coordinator, not performed inline and called done.
- [x] 2.23 PR-2 verification gate: `uv run pytest tests/unit/entrypoints tests/architecture`; re-run 2.4's byte-identity check; `uv run ruff check`; `uv run mypy`; `env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN uv run pytest`. **Correction applied**: every command above had its `-q` dropped — `pyproject.toml`'s own `addopts` already sets `-q`, and a second one on the command line silently suppresses the final summary line (same gap Phase 1's fix round 1 found and recorded).

---

## Phase 3: `contracts/scheduled-cycle.json` + CDK Stack (PR 3, synth-only — no deploy)

Skills loaded for this phase: `aws-cdk`, `aws-serverless`. **No task in this
phase runs `cdk deploy`, `cdk bootstrap`, or any live AWS call.** Every
assertion below is against a synthesized `Template`.

### Facts to close before writing assertions (per the verified facts supplied)

- [x] 3.1 [Network] Confirm the CDK L2 `Schedule` construct's exact prop names for `state`, `scheduleExpressionTimezone`, and the retry policy shape, against the pinned `aws-cdk-lib` version (design D35 to-verify #3). Record in apply-progress. **Confirmed against the installed `aws-cdk-lib@2.270.0` source directly** (`infra/node_modules/aws-cdk-lib/aws-scheduler/lib/schedule.js`): the L2 `Schedule` construct has **no** `state` or `scheduleExpressionTimezone` prop of its own — those are `CfnSchedule` (L1) properties, rendered by `Schedule` from `enabled?: boolean` (`state: enabled ? 'ENABLED' : 'DISABLED'`) and from `ScheduleExpression.cron({ timeZone })`'s `TimeZone.timezoneName`. The retry policy (`maxEventAge: Duration`, `retryAttempts: number`) lives on `ScheduleTargetBaseProps` — the *target's* construction props (e.g. `new scheduler_targets.LambdaInvoke(fn, { maxEventAge, retryAttempts })`), not on `ScheduleProps` — exactly as the launch prompt's correction stated. Implemented accordingly in `infra/lib/scheduled-cycle-stack.ts`.
- [x] 3.2 [Network] Confirm `iam.ManagedPolicy.fromManagedPolicyArn`'s signature performs no ARN parsing that would reject the test placeholder `arn:aws:iam::ACCOUNT:policy/SnapshotWriter` (design D25 to-verify #2). **Confirmed** against the installed source: `fromManagedPolicyArn` constructs a plain `Import` resource carrying the ARN string verbatim — no parsing, no validation.
- [x] 3.3 [Network] Confirm CloudWatch filter-pattern quoting for a literal beginning with `[`, so Alarm 3's pattern is written correctly (design D33 to-verify #8). **Confirmed against AWS's live filter-pattern syntax documentation** (`docs.aws.amazon.com/AmazonCloudWatch/latest/logs/FilterAndPatternSyntax.html`, fetched at apply time): "Enclose exact phrases and terms that include non-alphanumeric characters in double quotation marks." The launch prompt's hypothesis was correct — implemented as `FilterPattern.literal('"[SNAPSHOT NOT PUBLISHED]"')`.
- [x] 3.4 **Retry policy, stated rather than assumed**: `MaximumEventAgeInSeconds` (60–86400) and `MaximumRetryAttempts` (0–185) are confirmed-valid ranges, but **the unspecified default is not documented** — this task sets both explicitly (`maximumEventAgeInSeconds: 3600`, `maximumRetryAttempts: 2`) in every task below rather than omitting either and inheriting an unknown default. Recorded here so the reason survives a later "why not just leave it default" review. **Corrected rationale (apply time)**: the default is not merely undocumented in prose — it is confirmed, in code, at `ScheduleTargetBaseProps`'s own JSDoc: `retryAttempts` defaults to **185** and `maxEventAge` defaults to **`Duration.hours(24)`**. Inheriting it would mean a failing invocation retried up to 185 times across 24 hours, overlapping the next four scheduled runs of this six-hourly cycle — a stronger, more specific justification than "undocumented."

### The timeout contract — two relations, two homes (D31), never collapsed into one task

- [x] 3.5 [RED] `tests/unit/adapters/test_agentcore_invoker.py::test_contract_matches_read_timeout` — asserts `contract["agent_read_timeout_seconds"] == agentcore_invoker.READ_TIMEOUT` and `contract["ssm_path_prefix"]` equals the prefix constant `ssm_config_repository.py` reads from. Expects `FileNotFoundError`: `contracts/scheduled-cycle.json` does not exist. **This is the Python-side half of Relation 1 — it lives here, not in `infra/`.** Confirmed RED live: `FileNotFoundError` on `contracts/scheduled-cycle.json`.
- [x] 3.6 [GREEN] Create `contracts/scheduled-cycle.json` (`schema_version`, `agent_read_timeout_seconds: 35.0`, `cycle_headroom_seconds: 60`, `ssm_path_prefix`). Mutation proof: bump `READ_TIMEOUT` in the Python constant without updating the file and record this test going red; revert. Verify: `uv run pytest tests/unit/adapters/test_agentcore_invoker.py -k contract -q`. **Mutation proof run live**: `READ_TIMEOUT` bumped 35.0→40.0, test failed (`assert 35.0 == 40.0`); reverted, green.
- [x] 3.7 [RED] `infra/test/scheduled-cycle-stack.test.ts::"env timeout at least the contract's read timeout"` — reads `contracts/scheduled-cycle.json` via `readFileSync`, asserts synthesized `Environment.Variables.RAIN_ALERT_AGENT_TIMEOUT_S >= contract.agent_read_timeout_seconds`. Expects: stack/env var do not exist yet. **This is the TypeScript-side half of Relation 1.**
- [x] 3.8 [RED] `infra/test/scheduled-cycle-stack.test.ts::"function timeout exceeds agent deadline plus headroom"` — asserts `Timeout > Number(env.RAIN_ALERT_AGENT_TIMEOUT_S) + contract.cycle_headroom_seconds` and `Timeout <= 900`. **This is Relation 2 — same template, no language crossing, and its own separate test from 3.7.**
- [x] 3.9 [GREEN] Scaffold `infra/lib/scheduled-cycle-stack.ts` with the Lambda function (env vars, an explicit `timeout: Duration.seconds(120)` literal — **not** derived from the contract file, per D31's "why not derive" rationale: deriving it would make the assertion tautological). Verify: `cd infra && npm test -- scheduled-cycle-stack -t "timeout"`
- [x] 3.10 Mutation proof for Relation 2: temporarily set the test context's `RAIN_ALERT_AGENT_TIMEOUT_S` to `"10"` and record 3.8's assertion going red (D19's "why not 5 s" kill-switch-by-accident case); revert. **Correction, apply time: this task's premise about *which* assertion goes red is wrong, and it is worth recording why.** With `RAIN_ALERT_AGENT_TIMEOUT_S = "10"`, Relation 2 (task 3.8: `Timeout(120) > env(10) + headroom(60) = 70`) still holds — 120 > 70 is true regardless, so lowering the env var makes Relation 2 *easier* to satisfy, not harder, and 3.8's test stays green. Verified live: ran `npm test -- scheduled-cycle-stack -t "exceeds the agent deadline"` under the mutation — **1 passed**, not red. What the mutation actually breaks is **Relation 1** (task 3.7: `env(10) >= contract.agent_read_timeout_seconds(35)`), which is exactly the "why not 5 s" kill-switch-by-accident case D19 names — a too-low `RAIN_ALERT_AGENT_TIMEOUT_S` is a Relation 1 violation (the deployed timeout falls below the measured cold-start deadline), not a Relation 2 one. Verified live: same run against `-t "env timeout at least"` **failed** with `Expected 35 but received 10`. Reverted after both runs.

### Lambda role and the imported `SnapshotWriter` policy (D25)

- [x] 3.11 [RED] `"lambda role attaches SnapshotWriter and defines no s3:PutObject"` — the role's own inline policy contains no `s3:PutObject` statement; `ManagedPolicyArns` includes the prop-supplied ARN via `fromManagedPolicyArn`.
- [x] 3.12 [GREEN] Wire the required props (`snapshotBucketName`, `snapshotWriterPolicyArn`), attach the imported policy, add no S3 grant of its own. Verify: `cd infra && npm test -- scheduled-cycle-stack -t "SnapshotWriter"`

### DynamoDB table (D26, D27, D34)

- [x] 3.13 [RED] `"table"` — `RemovalPolicy.RETAIN`, explicit `tableName: 'ferrenafe-alerts-sent'`, `timeToLiveAttribute: 'expires_at'`, no GSI, on-demand billing; the Lambda role's grant names this one table, never `*`.
- [x] 3.14 [GREEN] Add the table construct and the scoped grant. Verify: `cd infra && npm test -- scheduled-cycle-stack -t "table"`

### SSM grant — no parameters created (D32's trap)

- [x] 3.15 [RED] `"SSM grant"` — the role's SSM statement resource ARN ends with `contract.ssm_path_prefix`; action includes `ssm:GetParametersByPath`; **the synthesized template creates zero `AWS::SSM::Parameter` resources** — asserted as an absence, because CDK owning these would silently restore stale values on an unrelated deploy (D32). **Superseded mechanism (this task's text predates D32's fix-round-1 amendment)**: design.md D32's amendment explicitly moved the CDK grant from one `ssm:GetParametersByPath` wildcard resource to **seven exact `ssm:GetParameters` resource ARNs**, matching the Python-side move from `GetParametersByPath` to `GetParameters` — "the CDK test now asserts each of the seven granted ARNs starts with it, rather than asserting one granted resource ends with it" (design.md D32 amendment, verbatim). Implemented and tested per the amendment, not per this task's original wording. The absence assertion (zero `AWS::SSM::Parameter` resources) is unaffected and implemented as written. **Absence-assertion mutation proof, run live**: temporarily added `new ssm.StringParameter(this, 'PlantedParameter', {...})` to the stack; the "SSM grant" test failed (`Expected length: 0, Received length: 1`); reverted.
- [x] 3.16 [GREEN] Add the IAM grant only. Verify: `cd infra && npm test -- scheduled-cycle-stack -t "SSM"`

### AgentCore grant (no Bedrock model permission)

- [x] 3.17 [RED] `"agentcore grant"` — role holds `bedrock-agentcore:InvokeAgentRuntime` scoped to exactly the one context-supplied ARN, no wildcard, and **no** `bedrock:InvokeModel` statement anywhere on the role. **Gap filled at apply time**: neither design.md nor this task names the context key the ARN is supplied through. Added a third required `ScheduledCycleStackProps`/context prop, `agentRuntimeArn`, following the exact same pattern D25 already establishes for `snapshotWriterPolicyArn` (never committed — the ARN embeds the account id, supplied only via `-c agentRuntimeArn=<arn>` at deploy time). Recorded in apply-progress as a design elaboration, not a deviation from any explicit instruction.
- [x] 3.18 [GREEN] Add the scoped grant (action name confirmed at 3.1/3.2's live-doc check or the precedent `agentcore_invoker.py` already set). Verify: `cd infra && npm test -- scheduled-cycle-stack -t "agentcore"`

### EventBridge Scheduler (D35)

- [x] 3.19 [RED] `"schedule"` — `cron(0 0,6,12,18 * * ? *)`, `scheduleExpressionTimezone: 'America/Lima'`, `flexibleTimeWindow: OFF`, target is the Lambda, retry policy explicitly `maximumEventAgeInSeconds: 3600` / `maximumRetryAttempts: 2` (3.4), `reservedConcurrentExecutions: 1` on the function, `scheduleEnabled` context defaulting to `ENABLED` and producing `DISABLED` when `-c scheduleEnabled=false`.
- [x] 3.20 [GREEN] Add the `Schedule` construct and `reservedConcurrentExecutions`. Verify: `cd infra && npm test -- scheduled-cycle-stack -t "schedule"`
- [x] 3.21 Mutation proof: temporarily remove `reservedConcurrentExecutions: 1` and record the property-presence assertion going red (a CDK test can only prove the template carries the reservation, not that a real concurrent invocation was throttled — stated here rather than implied); revert. **Run live**: removed the prop, `"schedule"` test failed (`Missing key 'ReservedConcurrentExecutions'`); reverted, green.

### Observability (D33)

- [x] 3.22 [RED] `"alarms and topic"` — Alarm 1 (`Errors` `Sum >= 1`, `treatMissingData: NOT_BREACHING`), Alarm 2 (`Invocations` `Sum < 1` over 8h, `treatMissingData: BREACHING`), Alarm 3 (metric filter on the correctly-quoted `"[SNAPSHOT NOT PUBLISHED]"` pattern per 3.3, alarmed `>= 1`), one SNS `Topic`, all three alarms' `AlarmActions` include it. **No subscription is created** — an email address is personal data (`openspec/config.yaml → rules.archive`).
- [x] 3.23 [GREEN] Add the log group (`RetentionDays.ONE_MONTH`), the metric filter, the three alarms, the topic. Verify: `cd infra && npm test -- scheduled-cycle-stack -t "alarm"`

### App wiring and hygiene

- [x] 3.24 [RED] `infra/test/app.test.ts` — `bin/infra.ts` synthesizes `ScheduledCycleStack` only when both required context props are supplied, and **throws** a clear error when `snapshotWriterPolicyArn` is absent, never defaulting it. Implemented as an exported `buildScheduledCycleStack(app)` function so both outcomes (undefined vs. throw vs. constructed) are independently testable with hand-built `App({ context })` instances, since `snapshotWriterPolicyArn`/`agentRuntimeArn` are never present under a plain `npm test` run.
- [x] 3.25 [GREEN] Wire `bin/infra.ts`: `snapshotBucketName` from `cdk.context.json`, `snapshotWriterPolicyArn` from `-c` only (never committed). Verify: `cd infra && npm test -- app`
- [x] 3.26 Run `uv run pytest tests/hygiene -q` **after** `git add infra/` (never before — `git grep` does not see untracked files). Confirm every fixture ARN in `infra/test/` uses `arn:aws:iam::ACCOUNT:policy/SnapshotWriter`, not a plausible twelve-digit number, and that `cdk.context.json` carries `snapshotBucketName` only. **Run live, found a real defect**: the first run failed `test_no_docstring_or_comment_cites_a_path_git_does_not_have` — the new contract test's docstring cited `contracts/agent-composition.json` in backticks (copied from design.md D31's own hypothetical precedent), which was never actually created in this repository. Fixed by removing the backtick-wrapped phantom path from the docstring; re-ran, green (49 passed). This is exactly the class of defect `docs/blog/2026-09-21-three-docstrings-cited-a-file-that-was-never-written.md` and this hygiene check exist to catch.

### Lambda build script (D30)

- [x] 3.27 [GREEN, scripted] `infra/scripts/build-lambda.sh` — `uv export --frozen --no-dev --no-emit-project`, filter the excluded distributions, `uv pip install --target infra/build/lambda --python-version 3.12 --python-platform aarch64-manylinux2014`, copy `src/rain_alert`. The script **fails** if the output tree contains any `*.so` or `awscrt*` directory. Implemented filtering via `--no-emit-package awscrt` on the `uv export` call itself, rather than post-hoc text filtering of the requirements file.
- [x] 3.28 Run the build script once locally; record the resolved dependency list from `uv export --frozen --no-dev` in apply-progress (design to-verify #7), confirming it contains no compiled wheel; confirm the no-`.so`/no-`awscrt` guard is reachable code (temporarily point it at a directory containing a planted `.so` file and record the script exiting non-zero; remove the planted file). **Run live** — full output and the resolved 16-package list recorded in apply-progress. **Deviation from D30's predicted list, noted rather than silently corrected**: the real `uv export` resolves `six` and `typing-extensions` (not named in D30) and does **not** resolve `sniffio` (which D30 named). Built tree: 31 MB unzipped, zero `.so` files, zero `awscrt*` directories. **Guard mutation proof**: since the script's own `rm -rf` at the top would erase a file planted beforehand, the guard's exact find/conditional logic was extracted and run standalone against an isolated directory holding a planted `.so` file — exited 1, message named the planted file; the real build was then re-run clean to confirm the guard passes on the actual artifact.
- [x] 3.29 `infra/.gitignore` — ignore `build/`.

### Close-out

- [ ] 3.30 **Security review** (fresh context) on the PR-3 branch diff. Scope: every IAM grant is resource-scoped (no `*`), the imported managed policy is attached not re-granted, the SSM grant's resource ends with the contract prefix, no twelve-digit id or ARN containing one appears anywhere in the diff. **Not run by sdd-apply** — the launching prompt forbids dispatching subagents, so a genuinely fresh-context review cannot happen from within this session (same posture as Phase 1's task 1.34 and Phase 2's task 2.22). Flagged as outstanding for the coordinator.
- [x] 3.31 PR-3 verification gate: `cd infra && npm test`; `uv run pytest tests/unit/adapters/test_agentcore_invoker.py -k contract -q`; `uv run pytest tests/hygiene -q`. Confirm zero AWS credentials and zero network calls were used by anything in this PR — CDK synth and `moto` are both local. **All run live and green** — see apply-progress for exact counts. The one exception, stated rather than implied: `infra/scripts/build-lambda.sh` itself (task 3.28, run once to produce the asset `Code.fromAsset` needs at synth time) uses `uv export`/`uv pip install`, which may reach a package index over the network if a wheel is not already cached locally — this is ordinary Python build tooling, not an AWS call, and not part of `npm test`/`pytest`/`moto`'s own offline guarantee.

**If Phase 3 exceeds roughly 1000 changed lines once written**, split before opening the PR into **3a** (Lambda, table, IAM grants, schedule — tasks 3.1–3.21) and **3b** (alarms, SNS, app wiring, hygiene, build script — tasks 3.22–3.31), each independently verifiable via `npm test`.

---

## Phase 4: Docs and the Region Correction (PR 4)

- [ ] 4.1 `docs/runbooks/scheduled-cycle-deploy.md` — new, matching the pattern of `agentcore-deploy.md`/`public-page-deploy.md`: seed the seven SSM parameters (the `aws ssm put-parameter` commands, matching `contracts/scheduled-cycle.json`'s prefix and D32's seven names), deploy with `-c scheduleEnabled=false`, invoke once by hand, **verify rollback level 3 (hand-publish) before the first stack deploy**, enable the schedule, subscribe an operator email to the SNS topic and send a test notification to confirm delivery (the `PendingConfirmation` gotcha, D33), the `cdk destroy` order and the orphaned-table recovery note (D34).
- [ ] 4.2 `docs/superpowers/specs/2026-09-03-rain-alert-core-design.md` §10.3 — correct `us-east-1` to `us-east-2`, with the 2026-09-21 Bedrock quota-exhaustion rationale.
- [ ] 4.3 `CLAUDE.md` — correct "Deployment target is `us-east-1`" to `us-east-2`.
- [ ] 4.4 `docs/architecture/adapters.md`, `docs/architecture/entrypoints-and-testing.md` — document the two new adapters, the second entry point, and narrow the `TimeWindow.key()` line to "sort-key prefix, not the whole key" (D26 — an escalation can add a second record to the same window).
- [ ] 4.5 **Security review** (fresh context) on the PR-4 diff — docs-only; confirm no account id, no ARN, no personal data (an operator email) entered the runbook.
- [ ] 4.6 PR-4 verification gate: `uv run pytest tests/hygiene -q`; proofread that the runbook's seed commands match the contract file's prefix and D32's seven parameter names exactly.

---

## Owner-Run: Live Deploy and Verification (not automated by sdd-apply)

These touch the real `ferrenafe` AWS account. **sdd-apply MUST stop before
this section.** The owner runs and confirms each step, recording evidence
in `docs/evidence/` per that directory's convention.

- [ ] O.1 [Owner, AWS] Verify rollback level 3 (hand-publish, `RAIN_ALERT_SNAPSHOT_BUCKET` set) still works, before any stack deploy.
- [ ] O.2 [Owner, AWS] Seed the seven SSM parameters with `composer=template`.
- [ ] O.3 [Owner, AWS] `cdk deploy ferrenafe-scheduled-cycle -c scheduleEnabled=false -c snapshotWriterPolicyArn=<arn> -c snapshotBucketName=<from cdk.context.json>`.
- [ ] O.4 [Owner, AWS] Invoke once by hand; read the log line, the DynamoDB item, the new S3 object version; record `Init Duration`/`Duration` from the `REPORT` line (closes design's cold-start and `memorySize` to-verify items).
- [ ] O.5 [Owner, AWS] Deliberately supply a bad agent ARN in one invocation; confirm the template fallback still sends, still publishes, and logs exactly one `AGENT_FALLBACK_USED` notice.
- [ ] O.6 [Owner, AWS] Enable the schedule; watch several real cycles across the six-hourly boundary.
- [ ] O.7 [Owner, AWS] Subscribe an operator email to the SNS topic; send a test notification; confirm delivery.
- [ ] O.8 [Owner, AWS] Flip `composer=agent` in SSM (no deploy); record the first agent-composed message in the change artifacts for the owner to judge.
- [ ] O.9 [Owner, AWS] Confirm a standing `prepare` across two consecutive real cycles sends once.
- [ ] O.10 [Owner, AWS] Confirm Alarm 3's filter pattern matches a real `SNAPSHOT_FAILURE_PREFIX` log event (D33's stated gap — not provable in the CDK suite).
- [ ] O.11 [Owner, AWS] Run `cdk drift` and confirm the deployed function's `Timeout` and `RAIN_ALERT_AGENT_TIMEOUT_S` match the template with no console edit.

---

## Notes on Rules Applied

- `openspec/config.yaml → rules.tasks`: grouped by phase, hierarchical numbering, tooling scaffold (the `moto` dev dependency, task 1.2) is its own early task within the phase that introduces it.
- Strict TDD: every behavioral task is RED before GREEN; RED tasks name the exact expected failure; test tasks whose teeth are non-obvious carry an explicit mutation-proof step.
- The default suite stays offline: the forced-dummy-credentials fixture (1.1/1.2) is what makes that a property of the suite rather than of good intentions (D28); Phase 3's CDK suite is equally offline (`npm test` synthesizes locally).
- `ALERT_RETENTION_DAYS = 30` (task 1.7/1.8) resolves design §9's open item in the spec's favor — the design's proposed 365 is superseded.
- Hygiene runs after `git add`, never before (task 3.26) — `git grep` does not see untracked files.
- No `Co-Authored-By` or AI attribution in any commit, per `CLAUDE.md`.
