# Apply Progress: Scheduled Cycle — Phase 1 (PR 1 of 4)

**Scope**: Phase 1 only — `DynamoDbAlertRepository` + `SsmConfigRepository`,
offline, `moto`-backed. Phases 2-4 and the Owner-Run section are explicitly
out of scope for this batch and were not started.

**Branch**: `feat/scheduled-cycle-adapters`, from `main` at `2095d4c`.
**Delivery strategy**: `ask-on-risk`, resolved by the orchestrator to
`stacked-to-main` — this PR merges to `main` on its own; PR 2 builds on it.
**Mode**: Strict TDD (RED → GREEN, mutation proofs on load-bearing
assertions).

## Commits

| SHA | Message |
|---|---|
| `dfb9174` | docs: plan the scheduled-cycle change (openspec artifacts, matching the `composer-agent` precedent) |
| `8dc4e57` | test: force dummy AWS credentials for the whole suite (+ `moto` dev dependency) |
| `3b8f488` | feat(adapters): add DynamoDbAlertRepository over DynamoDB |
| `f4d6b85` | feat(adapters): add SsmConfigRepository over SSM Parameter Store |
| `733a84b` | test(adapters): cover DynamoDbAlertRepository's outage-state methods (gap backfill, see Deviations) |

## Tasks completed

### Offline guarantee
- [x] 1.1 [RED] `test_aws_credentials_are_forced_to_dummy_values`
- [x] 1.2 [GREEN] Session-scoped `autouse` fixture in `tests/conftest.py`; `moto` added to `pyproject.toml`'s dev group

### `DynamoDbAlertRepository` — pure mapping
- [x] 1.3 [RED] key construction test
- [x] 1.4 [GREEN] `adapters/dynamodb_alert_repository.py` pure key functions
- [x] 1.5 [RED] sentinel-ordering test, parametrized over every `Level`
- [x] 1.6 [GREEN] `upper_bound_sort_key` + mutation proof (see below, with a stated discrepancy from the task's assumption)
- [x] 1.7 [RED] TTL relation test
- [x] 1.8 [GREEN] `compute_expires_at`, `ALERT_RETENTION_DAYS = 30` + mutation proof
- [x] 1.9 [RED] outage item carries no `expires_at`
- [x] 1.10 [GREEN] confirmed the mapper omits it entirely

### `DynamoDbAlertRepository` — client-touching, `moto`
- [x] 1.11 [RED] Query key condition test
- [x] 1.12 [GREEN] client-injecting constructor, lazy `boto3.client("dynamodb")`
- [x] 1.13 [RED] pagination test (oversized items to force a real second page)
- [x] 1.14 [GREEN] `boto3` paginator + mutation proof
- [x] 1.15 [RED] `record_alert` unconditional-write test
- [x] 1.16 [GREEN] plain `put_item`, no `ConditionExpression`
- [x] 1.17 [RED] full round-trip test
- [x] 1.18 [GREEN] confirmed the composed path satisfies it

### `SsmConfigRepository`
- [x] 1.19 [RED] full-load test
- [x] 1.20 [GREEN] `adapters/ssm_config_repository.py`, one `GetParametersByPath` call, JSON per-field
- [x] 1.21 Rationale recorded in the module docstring (checklist as JSON array, never `StringList`)
- [x] 1.22 [RED] stray-parameter typo-tolerance test
- [x] 1.23 [GREEN] confirmed the parser maps by an explicit allow-list
- [x] 1.24 [RED] missing-provenance test
- [x] 1.25 [GREEN] `coordinates_source` required, no inference path + mutation proof
- [x] 1.26 [RED] per-field strict-validation table, parametrized over all seven fields
- [x] 1.27 [GREEN] per-field validation implemented
- [x] 1.28 [RED] `active_channel` allow-list test
- [x] 1.29 [GREEN] validated against `["console"]`
- [x] 1.30 [RED] instance-memoization test
- [x] 1.31 [GREEN] cached on the instance after first successful load
- [x] 1.32 [RED] `moto`-backed pagination test (seeded to force a real second page)
- [x] 1.33 [GREEN] `boto3` paginator + mutation proof

### Close-out
- [x] 1.34 Security review — **done inline, not in a fresh context** (see Deviations)
- [x] 1.35 PR-1 verification gate — green (see Verification below)
- [x] 1.36 Whole default suite passes with AWS credential env vars unset — confirmed
- [x] 1.37 `uv add moto --dev` committed; confirmed no dev-only package (`moto`, `cryptography`, `cffi`, `pyyaml`, `requests`, `responses`, `werkzeug`, `xmltodict`, `charset-normalizer`) appears in `uv export --frozen --no-dev --no-emit-project`

**32 unit tests added** across `test_conftest_fixtures.py` (1),
`test_dynamodb_alert_repository.py` (22), `test_ssm_config_repository.py`
(27 — includes the 20-case per-field parametrized table). Total suite: 245
tests, all green.

## TDD Cycle Evidence

| Task | RED (real failure observed) | GREEN | REFACTOR |
|---|---|---|---|
| 1.1/1.2 | `AssertionError: assert '<redacted-real-looking-access-key-id>' == 'testing'` — the sandbox's real ambient AWS access key ID resolved before the fixture existed (see Discoveries; value deliberately not reproduced here, see Hygiene note) | Fixture added; same test green | n/a |
| 1.3/1.4 | `ModuleNotFoundError: No module named 'rain_alert.adapters.dynamodb_alert_repository'` | Module created, key-construction tests green | n/a |
| 1.5/1.6 | Same `ModuleNotFoundError` at collection (whole file); once the module existed, sentinel tests were green from the first correct implementation — see mutation proof below for the RED half of "not just presence" | `upper_bound_sort_key` implemented with `￿` | n/a |
| 1.7/1.8 | Same `ModuleNotFoundError` | `compute_expires_at`, `ALERT_RETENTION_DAYS = 30` | n/a |
| 1.11-1.14 | Same `ModuleNotFoundError`; pagination genuinely exercised (confirmed empirically: 20 oversized items → 2 real DynamoDB pages, 16+4) | `Query` via `get_paginator("query")`, `ConsistentRead=True`, `ScanIndexForward=True` | Added `_decimalize` after discovering `TypeSerializer` rejects raw `float` (`accumulated_mm`) — see Discoveries |
| 1.15-1.18 | Same `ModuleNotFoundError` | Plain `put_item`, round trip confirmed | n/a |
| 1.19-1.33 (SSM) | `ModuleNotFoundError: No module named 'rain_alert.adapters.ssm_config_repository'` | Full module, all 20 per-field fault cases green | Removed a planned `test_empty_string_parameter_raises` after discovering real SSM (`moto`'s emulation) rejects `Value=""` at `PutParameter` time — unreachable state, see Discoveries |
| Outage CRUD (backfill) | Tests written **after** implementation (see Deviations) — all passed immediately; teeth proven by mutation instead of by an initial failing state | n/a | n/a |

## Mutation Proofs (live, run and reverted)

1. **Sentinel choice (`upper_bound_sort_key`, task 1.6).** Temporarily set
   `UPPER_BOUND_SENTINEL = "~"`. Result: `test_the_sentinel_is_u_plus_ffff_not_ascii_tilde`
   and the dedicated `test_mutation_proof_tilde_sentinel_is_not_a_safe_substitute`
   went red (`AssertionError: the U+FFFF sentinel still holds`). **Deviation
   from the task's stated expectation**: task 1.6 predicted the *parametrized*
   "every `Level` member sorts below the bound" test would also go red for at
   least one current member. It did not — every current `Level` value
   (`none`, `prepare`, `imminent`) is plain ASCII lowercase, which sorts below
   `~` (0x7E) regardless. The real defect a `~` bound has is only exposed by
   a *hypothetical future* non-ASCII `Level` value, which is what the
   dedicated mutation test constructs directly. Recorded here rather than
   silently reported as "task 1.6 done as specified" — the task's assumption
   about current members was checked against actual behaviour and found
   false; the underlying design claim (D26: "the bound holds regardless of
   what a future `Level` value spells") is what the test now actually proves.
   Reverted to `￿`.

2. **TTL relation (`ALERT_RETENTION_DAYS`, task 1.8).** Temporarily set
   `ALERT_RETENTION_DAYS = 2`. Result:
   `assert (1788696000 - 1788523200) > (72 * 3600)` failed
   (`172800 > 259200` is false), and
   `test_the_retention_constant_is_30_days_per_the_spec_not_365` failed with
   `assert 2 == 30`. Reverted to `30`.

3. **Pagination, DynamoDB Query (task 1.14).** Temporarily added `break`
   after the first page in `alerts_with_window_start_between`. Result:
   `assert len(results) == 20` failed with `AssertionError: assert 16 == 20`
   — exactly the 16 items real DynamoDB/`moto` places on page 1 when 20
   items of ~60 KB each exceed the 1 MiB page cap (confirmed empirically
   with a standalone script before writing the test, and again via this
   mutation). Reverted.

4. **Pagination, SSM `GetParametersByPath` (task 1.33).** Temporarily added
   `break` after the first page. First attempt (stray parameters seeded
   *after* the real ones) did not go red — `moto` orders `GetParametersByPath`
   results by *creation order*, not lexically, so all 7 real parameters
   landed on page 1 regardless. Rewrote the test to seed the 10 stray
   parameters *first* (confirmed empirically), which reliably pushes all 7
   real parameters onto page 2. With that ordering and the `break` mutation
   in place: `ValueError: SSM parameter '/ferrenafe/rain-alert/location' is
   missing or empty` (a false "missing" error for a parameter that was only
   missing from the first page this adapter read). Reverted.

5. **Escalation overwrite (the sort key's whole reason to exist, per the
   task prompt's explicit instruction).** Temporarily changed `alert_item_sk`
   to return `window_start.isoformat()` alone (`TimeWindow.key()`
   equivalent, no level suffix). Result:
   `test_an_escalation_for_the_same_window_does_not_overwrite_the_prior_level`
   failed with `assert 1 == 2` — the `imminent` record silently overwrote the
   `prepare` record for the same window, exactly the failure design.md D26
   names. `test_query_upper_bound_is_inclusive_of_an_escalation_at_the_same_window_start`
   failed the same way. Reverted to `f"{window_start.isoformat()}#{level.value}"`.

6. **`coordinates_source` inference (task 1.24).** Temporarily replaced the
   raise with `document = {**document, "source": CoordinatesSource.PUBLIC_REFERENCE.value}`
   (the exact "naive fix" the task warns about). Result:
   `test_missing_source_raises_rather_than_defaulting` failed with
   `Failed: DID NOT RAISE ValueError`. Reverted.

7. **Outage-clear city isolation (backfilled coverage, not in tasks.md).**
   Temporarily hardcoded `clear_active_outage`'s delete key to
   `outage_item_pk("other-city")` regardless of the `city_slug` argument.
   Both `test_clear_active_outage_removes_it` and
   `test_clearing_one_city_never_touches_another_citys_outage_item` failed
   (the outage record for `ferrenafe` was never deleted). Reverted.

## Discoveries

- **The sandbox environment carries a real, live-looking AWS access key ID**
  in ambient credential resolution (surfaced by the very first RED run of
  task 1.1, before the dummy-credential fixture existed). Not printed here or
  in any committed artifact — only observed in transient tool output during
  this session. This is exactly the scenario design.md D28 names as the
  reason the fixture is required, not merely good practice, and validates
  the fixture's necessity beyond the design document's own hypothetical.
- **`boto3`'s `TypeSerializer` refuses raw `float`.** `AlertRecord`'s
  `ForecastThresholdReason.accumulated_mm` is a `float`, and DynamoDB's
  number type has no binary-float representation. Added `_decimalize`
  (recursive `float` → `Decimal` via `Decimal(str(value))`, not
  `Decimal(value)`, to avoid carrying the float's own binary-representation
  error into the stored number) before serializing `record_alert`'s item.
- **A truly empty-string SSM parameter value is not a reachable state.**
  `PutParameter` itself rejects `Value=""` with `ValidationException:
  Member must have length greater than or equal to 1` (confirmed against
  `moto`'s emulation of the real service). Removed a test that assumed
  otherwise rather than keep a test that could never fail against the real
  service it claims to guard.
- **`GetParametersByPath` (and DynamoDB `Query`) page by *creation order* in
  `moto`, not lexical parameter-name order.** This mattered for constructing
  a pagination test that actually forces a second page containing a
  *required* parameter (see mutation proof 4 above).
- **mypy's `ignore_missing_imports` for `boto3` needed widening.**
  `module = ["boto3", "botocore.*"]` does not cover the `boto3.dynamodb.*`
  submodule; changed to `["boto3", "boto3.*", "botocore.*"]`.

## Deviations from Design

1. **Security review (task 1.34) was done inline, not in a fresh context.**
   The launching prompt explicitly forbade dispatching sub-agents for this
   run ("Do not dispatch subagents"), which makes a genuinely fresh-context
   review impossible from within this session. Performed the review myself
   against the stated scope instead: (a) the forced-dummy-credentials
   fixture — confirmed it sets env vars *before* any test runs and that
   `boto3`'s credential provider chain checks environment variables first,
   so no accidental real client construction in this session's tests can
   authenticate against the real `ferrenafe` account; (b) SSM parameter
   shapes — none is secret-shaped (coordinates, thresholds, a public
   checklist, an enum-like channel/composer string, two integers); none is a
   `SecureString` candidate; (c) DynamoDB item shape — no PII (no name,
   email, or phone number anywhere in `AlertRecord`/`OutageRecord`); (d)
   grepped the full diff for `AKIA`, `SecureString`, `password`, `secret`,
   `api_key` — no matches; (e) confirmed every mutation-proof edit was
   actually reverted (`grep` for `MUTATION PROOF`, `break$` after the
   pagination loops, and the sentinel/retention constants' current values)
   before this report was written. **This is not a substitute for the
   fresh-context review the task calls for** — flagged as a risk below for
   the maintainer's own review before merge.
2. **Outage CRUD methods (`get_active_outage`/`save_active_outage`/
   `clear_active_outage`) were implemented ahead of their own tests.**
   `tasks.md`'s Phase 1 breakdown enumerates RED/GREEN pairs for the alert
   dedup path and for outage *item mapping* (1.9/1.10), but never for the
   outage *methods themselves*, even though design.md D26's item table and
   the `AlertRepository` port both require them. Implemented them alongside
   the alert methods (same client-injection pattern) without a preceding
   RED test, then backfilled moto-backed behavioral coverage in a follow-up
   commit once the gap was noticed. This is a genuine, stated strict-TDD
   deviation for this narrow scope — not the RED-first path the rest of this
   batch followed. The `alert-persistence` spec supplied for this change does
   not cover outage-state scenarios, so `sdd-verify` should not fail on this,
   but a reviewer should know the test-first discipline was broken here.
3. **`ALERT_RETENTION_DAYS = 30`, per explicit ruling, not design.md D27's
   proposed 365.** Implemented as instructed; the constant carries an
   in-code comment recording that the design proposed 365 and that the spec
   (written in parallel, never fed by the design) is what pins 30 and what
   `sdd-verify` checks.

## Issues Found

None beyond what is captured in Discoveries and Deviations above.

## Verification (run before every commit, all green)

```
uv run pytest                      # 245 passed
uv run --directory agent pytest    # unaffected by this change; not re-run (Phase 1 touches no agent/ files)
uv run ruff check                  # All checks passed
uv run ruff format --check         # all files already formatted
uv run mypy                        # Success: no issues found in 45 source files
env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN \
  -u AWS_DEFAULT_REGION -u AWS_PROFILE uv run pytest   # 245 passed, still offline
uv run pytest tests/hygiene -q     # 49 passed (run after every git add)
```

No AWS command was run against the real account. No `cdk` command was run
at all (out of scope for Phase 1).

## Workload / PR Boundary

- **Mode**: stacked PR slice (`stacked-to-main`), PR 1 of 4.
- **Current work unit**: Unit 1 — `DynamoDbAlertRepository` + `SsmConfigRepository`.
- **Boundary**: starts from `main` at `2095d4c`; ends with both adapters fully
  implemented, tested and offline-verified. Nothing in `main` constructs
  these adapters yet (wiring is Phase 2), so this PR is inert on its own —
  safe to merge and roll back independently.
- **Estimated review budget impact**: forecast was 700-950 changed lines;
  actual is ~1,320 across `src/`+`tests/`+`pyproject.toml` (excluding the
  454-line machine-generated `uv.lock` and the 6 openspec planning docs,
  which are not code review surface). Above forecast, consistent with this
  series' own stated pattern of running 2-10x over early estimates. Still a
  single, self-contained, revertible unit; not split further per the
  `stacked-to-main` decision already made by the orchestrator.

## Remaining Tasks (not started, out of scope for this batch)

- [ ] Phase 2 — `run_once` extraction, `lambda_handler`, `build_cloud_deps`, architecture test (PR 2)
- [ ] Phase 3 — `contracts/scheduled-cycle.json` + CDK stack (PR 3)
- [ ] Phase 4 — Runbook + region correction (PR 4)
- [ ] Owner-Run — live deploy and verification (not automated by sdd-apply)

## Status

**37/37 Phase 1 tasks complete** (1.1-1.37), plus 5 backfilled outage-state
tests beyond the enumerated list. Ready for the maintainer's review and, per
instructions, **no PR was opened and nothing was pushed** — this branch and
these commits are local only.
