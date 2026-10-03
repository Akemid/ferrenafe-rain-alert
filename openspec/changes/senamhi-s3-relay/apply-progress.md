# Apply Progress: SENAMHI S3 Relay, Phase 1 (PR 1 of 5)

**Scope**: Phase 1 only. Domain reasons, contract file, pure helpers,
`RelayWarningProvider`, `FakeRelayReader`. No wiring, no S3 client, no infra.
Phases 0 and 2-5 and Phase L were not started. Task 1.27 (security review)
belongs to the orchestrator and is open.

**Branch**: `feat/senamhi-relay-1-domain`, stacked on `docs/builder-center-draft`.
**Delivery strategy**: `ask-on-risk`, resolved to chained PRs, `stacked-to-main`.
**Mode**: Strict TDD. Every RED was run and failed for the stated reason.

## Commits

| SHA | Message |
|---|---|
| `a3866ec` | feat(relay): pin the relay object contract and its constants |
| `5973694` | feat(domain): name the three relay outage reasons |
| `169056f` | feat(relay): add the pure freshness, skew and detail helpers |
| `411ec7c` | feat(relay): add RelayWarningProvider and FakeRelayReader |
| `9b2a0a5` | test(relay): pin operator notices, resident text and layer boundary |

## Tasks completed

1.1 to 1.26 `[x]`. 1.27 open (orchestrator).

## TDD evidence (RED failure lines)

| Task | RED failure, as run |
|---|---|
| 1.1 | `ModuleNotFoundError: No module named 'rain_alert.adapters.senamhi_relay'` (module absent, so the contract-file `FileNotFoundError` named in the task was never reached; both were absent) |
| 1.3 | `AttributeError: type object 'UnavailableReason' has no attribute 'STALE_RELAY'` |
| 1.5 | `ImportError: cannot import name 'is_stale' from 'rain_alert.adapters.senamhi_relay'` |
| 1.7 | `ImportError: cannot import name 'skew' ...` |
| 1.9 | `ImportError: cannot import name 'relay_detail' ...` |
| 1.11 | `ImportError: cannot import name 'RelayObject' ...` |
| 1.12 | `ImportError: cannot import name 'RelayWarningProvider' ...` |
| 1.14 | `assert False  where False = isinstance(Available(data=(), fetched_at=...), Unavailable)` (freshness unchecked, so a 4 h calm page returned `Available`) |
| 1.16 | `FetchError: structure_unrecognized ...` / `TypeError: Incoming markup is of an invalid type: None` escaped the provider |
| 1.18 | `RuntimeError: botocore raised something new` and both `FetchError`s escaped `fetch_current_warnings` |
| 1.20 | `assert [] == [RelayReadRecord(degraded=0, ...)]` (callback never invoked) |

## Mutation proofs (all reverted, tree clean afterwards)

- 1.2: `MAX_AGE` 10800 to 10801 turned `test_the_age_limit_matches_the_contract_file` red.
- 1.6: `>=` to `>` in `is_stale` turned the `exactly-3h-stale` row red.
- 1.22: dropping ` — {detail}` from `outage._failure_lines` turned 5 notice tests red.
- 1.23: appending the reason value to `senamhi_status` in `RiskEvaluator.evaluate` turned all three resident-text parametrizations red.
- 1.25: adding `from rain_alert.entrypoints import wiring` to `senamhi_relay.py` turned the real-file scan red.

## Characterization tests (passed at once, recorded honestly)

- 1.22 stale/unreadable/no-renotify and 1.23 resident-text identity passed on first run, because `outage.py` and `snapshot.py`/`template.py` render generically (design claim verified, not assumed). 1.24 therefore needed **no `outage.py` change**.

## Deviations and findings

- Task 1.12 names `warnings_table.html`, but that fixture's only in-force row is the heat warning, which the parser discards, so it yields zero warnings and one discard note. The equality-with-scraper test is parametrized over it and `precipitation_coast_current.html` (one flood-relevant warning) so the "parse really ran" assertion is non-trivial.
- 1.3: the `fallback_reason_for` totality test lives in `tests/unit/adapters/test_agent_composer.py` (where the function is tested), not `test_values.py`.
- `skew` sign convention: claimed minus S3 `LastModified`; positive means the producer clock runs ahead.
- `relay_detail` bounds a hostile VersionId to 100 chars and the whole string to 160, so the VersionId always survives the notice cap.
- `docs/architecture/domain.md` reason table updated (one line) with the three members. Other docs are PR 5.
- `on_read` raising would escape `fetch_current_warnings`. Originally unguarded; now guarded, see the judgment-day fixes below.
- V.* items: none were needed by Phase 1 tasks (V.1/V.5 gate task 2.9, V.4 gates 2.13).

## Gotcha

A same-length `sed` mutation within one second leaves a stale `.pyc` (mtime and size unchanged), so the mutated run silently used the old code and the revert looked broken. Delete `__pycache__/<module>*.pyc` after a same-size mutation.

## Gate (PR 1)

`uv run pytest` 1217 passed, 15 deselected. `uv run --directory agent pytest` 32 passed. `uv run ruff check` clean. `uv run ruff format --check` 118 files formatted. `uv run mypy` no issues in 48 files. `cd infra && npm test` 27 passed.

## Judgment-day fixes (PR 1, two independent reviewers)

One work-unit commit each, test first, RED confirmed for the intended reason.

1. HIGH `on_read` unguarded. RED: `RuntimeError: log sink exploded` escaped `_failed` (`senamhi_relay.py:243`). Fix: private `_emit` swallows `Exception` (one stderr line). Commit 47aeffc. Tests: `TestAFailingOnReadCallbackNeverChangesTheResult` (available, stale, reader-failure).
2. HIGH naive/aware mixing. RED: `TypeError: can't subtract offset-naive and offset-aware datetimes`. Fix: naive `now` or `last_modified` is `RELAY_UNREADABLE`; naive `claimed_fetched_at` is ignored (no skew). Documented in the `fetch_current_warnings` docstring. Commit 59fe34a. Tests: `TestNaiveTimestampsNeverEscape` (all three fields).
3. MEDIUM future `LastModified`. RED: `+61 s` returned `Available`. Fix: `FUTURE_TOLERANCE` 60 s, also in `contracts/senamhi-relay.json` (`future_tolerance_seconds`) and pinned by the contract test; beyond it `RELAY_UNREADABLE` with a capped detail. Commit ce5797d. Tests: `TestAFutureLastModifiedIsBoundedNotFresh` (+59 s fresh, +61 s unreadable). Gotcha: the `_relay_object` default `last_modified` was built from `NOW` (October) while tests parse at `PARSE_NOW` (September); the old clamp hid it, the bound exposed it, so the default now derives from `PARSE_NOW`.
4. MEDIUM unbounded exception message in detail. RED: newline survived in `unexpected RuntimeError: ...`. Fix: `_unexpected` runs the message through `sanitize_source_text` and a 100-char cap, keeping the `unexpected <Type>: ` prefix. Short clean messages are byte-identical (scraper parity kept); long or dirty ones diverge on purpose. Commit 41b44e6. Tests: `TestAnUnexpectedExceptionMessageIsSanitizedAndCapped`.

Tasks 2.17a (log writer JSON-encodes and sanitizes `version_id`) and 2.17b (reader enforces `MAX_OBJECT_BYTES` before read/decode) were added to Phase 2 in tasks.md.

Gate after fixes: `uv run pytest` 1230 passed, 15 deselected; `uv run --directory agent pytest` 32 passed; `ruff check` clean; `ruff format --check` 118 files formatted; `mypy` no issues in 48 files.
