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

**Corrected in fix round 1**: this line originally claimed "32 unit tests
added... total suite: 245 tests" without ever running a real count — both
numbers were wrong. Verified directly: `main` at `2095d4c` collects **1035**
tests (`uv run pytest --color=no -rN`, no `-q`, which was silently
swallowing the summary line in every prior run in this session — a tooling
gap, not a suite behavior). This batch, through the last commit before fix
round 1, added **60** tests (1095 total), not 32 — the per-field
parametrized table alone (`FIELD_FAULTS`, 20 cases) plus the round-trip,
`moto`-backed, and outage-state tests account for the rest. See "Fix Round
1" below for what was added on top of that.

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
uv run pytest                      # 1095 passed, 15 deselected (verified with `uv run pytest` bare, or
                                    # `-rN`; an explicit `-q` on the command line stacks with this
                                    # project's own `addopts = "-q ..."`, becoming `-qq`, which suppresses
                                    # the final summary line entirely -- see Discoveries)
uv run --directory agent pytest    # unaffected by this change; not re-run (Phase 1 touches no agent/ files)
uv run ruff check                  # All checks passed
uv run ruff format --check         # all files already formatted
uv run mypy                        # Success: no issues found in 45 source files
env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN \
  -u AWS_DEFAULT_REGION -u AWS_PROFILE uv run pytest   # 1095 passed, still offline
uv run pytest tests/hygiene -q     # all passed (run after every git add)
```

(Numbers above are this batch's state before fix round 1; see "Fix Round 1" for the current totals.)

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

---

# Fix Round 1

Two fresh-context reviews (code review: PASS, high quality; security: no
Critical, no High) found one structural gap in the production logic
(`ConsistentRead` unverifiable via `moto` alone) and a set of real hardening
items. All MUST and SHOULD items were addressed; MINOR items were addressed
except where explicitly noted as not applicable. Same rules as the original
apply: strict TDD, offline suite, no `unittest.mock`, English artifacts, no
AI attribution, hygiene after `git add`, no AWS commands, no push, no PR.

## MUST items

**1. `ConsistentRead` was asserted by nothing.** `moto`'s DynamoDB backend is
synchronous in-memory and cannot distinguish a consistent read from an
eventually consistent one — the "just-written record is visible" scenario
passed with the kwarg present or absent. Added
`TestConsistentReadIsActuallyRequested` (`test_dynamodb_alert_repository.py`)
— a hand-written kwarg-recording spy (`_ConsistentReadRecordingClient`,
`_RecordingQueryPaginator`) wrapping the real `moto`-backed client, asserting
`ConsistentRead=True` on both the `Query` (via the paginator) and the outage
`GetItem` call. **Mutation proof, run live**: deleted `ConsistentRead=True`
from both call sites in `dynamodb_alert_repository.py`; the whole suite
still reported `1 failed, 1100 passed` (only the new spy test failed, with
`KeyError: 'ConsistentRead'`) — confirming the reviewer's finding that no
prior test caught this, and that the new one does. Reverted. Corrected task
1.11's checkbox text in `tasks.md` (added 1.11a for the real proof) rather
than leaving a task marked done on a claim it did not keep.

**2. `GetParametersByPath` forced a prefix-wide IAM grant.** Switched
`SsmConfigRepository` to `client.get_parameters(Names=[...])` over the seven
exact names — `GetParameters` accepts up to ten names per call, well above
seven, so no paginator is needed. A missing parameter now raises explicitly
off the response's `InvalidParameters` list, named, rather than falling
through to `_require`'s generic message. **Mutation proof, run live**:
removed the `InvalidParameters` check — the "absent parameter" test failed
with the wrong message (`"...is missing or empty"` instead of `"not
found...composer"`), proving the explicit check is load-bearing, not
redundant with `_require`. Reverted. This also structurally resolves the
MINOR item about the untested allow-list filter (see below) and design.md
D32 is amended in place (new "Amendment (fix round 1)" subsection, original
text left intact for the record, matching the `ALERT_RETENTION_DAYS`
precedent). Replaced the old `moto`-backed pagination test (tasks 1.32/1.33,
which no longer applies — `GetParameters` doesn't paginate) with
`TestGetParametersRequestsExactNamesOnly`, proving via a request-recording
spy that only the seven exact names are ever sent.

**3. The dummy-credential fixture protected the account but broke the
offline guarantee.** Both reviewers independently found that forcing
`AWS_DEFAULT_REGION` gave a test with a forgotten `mock_aws()` decorator
enough configuration to construct a real client and reach
`dynamodb.us-east-2.amazonaws.com` over genuine HTTPS (`UnrecognizedClientException`
back — DNS resolved, TLS completed, AWS answered), where before the fixture
existed the same construction failed locally and instantly with
`NoRegionError`. Fixed by:
- Dropping `AWS_DEFAULT_REGION` from `tests/conftest.py`'s credential
  fixture — keeping only the three credential variables, which is what
  actually needs to be dummy.
- Adding a second session-scoped `autouse` fixture,
  `_block_non_loopback_network_egress`, that monkeypatches
  `socket.socket.connect` to raise on any non-loopback address. This is
  what actually makes "no network" true rather than merely changing which
  error a real request returns.
- Recording the amendment in `design.md` (D28's amendment subsection)
  rather than silently diverging from what it mandated verbatim.
- Rewording both `CLAUDE.md`'s verification-gate note and `tasks.md` task
  1.36 to describe what running with the AWS variables unset actually
  verifies now (that nothing depends on the *ambient* environment, not that
  no credential/network dependency exists at all — the credentials fixture
  supplies its own dummy values regardless).

New tests in `tests/unit/test_conftest_fixtures.py`: a non-loopback connect
attempt to `192.0.2.1` (TEST-NET-1, an IP literal — no real DNS lookup even
attempted) is blocked with the guard's own `RuntimeError`; a loopback
connect attempt to a port nothing listens on reaches the real socket layer
(`ConnectionRefusedError`, not the guard's exception) — proving the guard
discriminates rather than blocking everything. Verified the guard does not
break `moto` (`moto`'s `mock_aws()` patches botocore internals directly and
never opens a real socket at all — full suite still green with the guard
active, both with and without ambient AWS env vars set).

## SHOULD items

**4. Neither client carried a bounded `Config`.** Added the same
`CONNECT_TIMEOUT=3.0`/`READ_TIMEOUT=5.0`/`TOTAL_MAX_ATTEMPTS=2` pattern
`s3_snapshot_publisher.py` already established, to both
`DynamoDbAlertRepository` and `SsmConfigRepository`. New tests mirror
`test_s3_snapshot_publisher.py`'s own: a `boto3.client` factory swap
(`monkeypatch`, not `unittest.mock`) captures the `Config` actually passed
and asserts its three fields; a separate test asserts the bound stays under
20 seconds worst case. **Mutation proof, run live, both adapters**: deleted
the `config=` argument entirely from each `_resolved_client`; both new tests
failed with `TypeError: ...fake_boto3_client() missing 1 required
positional argument: 'config'` — confirming the bound is actually passed,
not merely documented. Reverted both.

**5. `checklist` was the one source-derived field the agent prompt did not
sanitize.** Verified first, since an existing test
(`test_operator_owned_text_is_not_sanitized`) explicitly asserted the
*opposite* — checklist was deliberately exempt while it was a Python code
constant (`MessageComposer`'s own port docstring: "excludes operator-owned
configuration — city, timezone, checklist"). Confirmed the real end-to-end
risk was already closed by defense-in-depth: `domain/template.py` sanitizes
checklist at render time regardless, and
`domain/message_validation.py::_message_is_safe`'s `has_unsafe_characters`
check on the model's final title/body rejects *any* unsafe character
reaching a resident, independent of what the prompt contained — so no
unsafe checklist text could ever have reached a community message. Made the
change anyway, because it removes a real, previously-accepted cost rather
than only closing a hypothetical: the validator's verbatim-quotation
exemption already compares against the *sanitized* form of each checklist
item, so an operator item that sanitizes differently than what the model
saw could never earn that exemption before this fix — now it can, because
the model is shown the same sanitized text the validator compares against.
Renamed the existing test to `test_code_owned_text_is_not_sanitized`
(covering only `city`/`timezone`, which remain genuine code constants) and
added `test_checklist_items_are_sanitized`. Updated the stale "excludes
operator-owned configuration... checklist" claim in both
`ports/__init__.py`'s `MessageComposer` docstring and
`docs/architecture/ports-and-application.md`, which would otherwise still
describe behavior this fix changed.

**6. DynamoDB read-back title/body were published to the public snapshot
with no type check.** `alert_record_from_dict` passes `title`/`body`
through with no `isinstance` check; the trust boundary changed from a local,
single-writer JSON file to a shared, multi-writer DynamoDB table. Added
`_published_text` in `domain/snapshot.py`: raises `ValueError` naming the
field if the value is not a `str`, otherwise sanitizes via
`sanitize_source_text` (the same rule `render_reason_es` already follows).
Applied to the `alert` dict's `title`/`body` and to each `recent_alerts[]`
entry's `title`. New tests in `test_snapshot.py`
(`TestPublishedTextIsGuardedAtTheSnapshotBoundary`), constructed by
`dataclasses.replace`-ing a real `CycleResult`/`AlertRecord`'s message with
a `Decimal` in place of a `str` — confirmed RED first (`DID NOT RAISE
ValueError` / a literal newline surviving into the published title) before
implementing the guard.

**7. Operator-supplied SSM values were echoed whole into exception
messages.** Up to several kilobytes into CloudWatch Logs on a mistake (e.g.
an operator pasting a credential into the wrong parameter). Added
`_echo(value)` (`sanitize_source_text(str(value))[:80]`) and applied it at
every site that previously interpolated a raw operator value:
`coordinates_source`, `active_channel`, `composer`, both integer fields, and
the thresholds document's generic exception-message fallback (the last one
not explicitly named in the review but the same vulnerability class — a
huge numeric string handed to `float()`/`int()` produces a `ValueError`
whose own message echoes the value verbatim). **Mutation proof, run live**:
reverted `_echo` to bare `str(value)`; 4 of the 5 new tests
(`TestExceptionMessagesNeverEchoAnOperatorValueInFull`) failed with the full
5000-character oversized value visible in the assertion diff. Reverted.

## MINOR items

- **The TTL mutation-proof test touched zero production code.** Rewrote
  `test_mutation_proof_a_too_short_retention_breaks_the_relation` to
  `monkeypatch.setattr(dynamodb_alert_repository, "ALERT_RETENTION_DAYS",
  2)` and call the real `compute_expires_at`, rather than recomputing the
  relation over local literals.
- **The SSM allow-list filter was untested and its comment overstated it.**
  Resolved structurally by MUST item 2: `GetParameters` requests only the
  seven exact names, so there is no wildcard response left to filter, and
  no filter-correctness claim left to test or overstate. Updated the
  now-superseded comments in `ssm_config_repository.py` and the docstring
  of `test_city_slug_is_the_code_constant_regardless_of_a_stray_parameter`
  to say so explicitly, and added `test_only_the_seven_exact_names_are_requested`
  as the new, accurate proof of the actual mechanism.
- **A stray U+FFFD-adjacent docstring that argued with itself.** Rewrote
  `test_mutation_proof_tilde_sentinel_is_not_a_safe_substitute`'s docstring
  to the three sentences it needs; removed the confusing "what we don't
  want to show" aside.
- **`compute_expires_at` trusted an untyped `sent_at`.** Added the same
  timezone-aware-UTC enforcement `TimeWindow.__post_init__` already has.
  Confirmed RED first (`DID NOT RAISE ValueError` for both a naive and a
  non-UTC input) before implementing.
- **`_decimalize` was asymmetric.** Applied to `save_active_outage` too, for
  symmetry with `record_alert`. Cannot be exercised by a failing test today
  — `OutageRecord` carries no `float` field — so this was not RED-first;
  stated here rather than silently done, per this project's own strict-TDD
  discipline.
- **`apply-progress.md`'s test counts were wrong**, and were corrected
  above and in the Verification section, with a real `git worktree`
  comparison against `main` to establish the actual baseline (1035) and
  actual delta (60 tests added, not 32) — not merely a corrected guess.

## Discoveries

- **`-q` on the command line, stacked on this project's own `addopts = "-q
  ..."`, becomes `-qq` and suppresses pytest's final summary line
  entirely.** Every `uv run pytest -q` invocation in the original apply
  batch (and the count claims derived from watching its output) silently
  lost the "N passed" line — the suite was never actually failing to report
  it, the command was asking twice. Verified reproducibly: `uv run pytest
  -q` produces no summary; bare `uv run pytest` or `uv run pytest -rN`
  does. All test-count claims in this fix round were re-verified with the
  summary line actually visible.
- Confirmed empirically that `moto`'s `mock_aws()` never opens a real
  socket at all (it patches botocore internals directly), so the new
  socket guard does not interfere with any existing `moto`-backed test —
  full suite green with the guard active.

## Verification (fix round 1, all green)

```
uv run pytest                      # 1115 passed, 15 deselected
uv run --directory agent pytest    # 32 passed (unaffected by this change)
uv run ruff check                  # All checks passed
uv run ruff format --check         # all files already formatted
uv run mypy                        # Success: no issues found in 45 source files
env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN \
  -u AWS_DEFAULT_REGION -u AWS_PROFILE uv run pytest   # 1115 passed, still offline
uv run pytest tests/hygiene -q     # all passed (run after every git add)
```

## Files changed (fix round 1)

| File | What changed |
|---|---|
| `src/rain_alert/adapters/dynamodb_alert_repository.py` | Bounded `Config`; `compute_expires_at` UTC guard; `_decimalize` on outage save |
| `src/rain_alert/adapters/ssm_config_repository.py` | `GetParameters` by exact name (not `GetParametersByPath`); bounded `Config`; `_echo` truncation/sanitization |
| `src/rain_alert/adapters/agent_prompt.py` | `checklist` sanitized in the prompt payload |
| `src/rain_alert/domain/snapshot.py` | `_published_text` guard on `alert.title`/`.body` and `recent_alerts[].title` |
| `src/rain_alert/ports/__init__.py` | `MessageComposer` docstring corrected (`checklist` no longer exempt) |
| `docs/architecture/ports-and-application.md` | Same correction, doc copy |
| `CLAUDE.md` | Reworded what the "AWS vars unset" verification step actually proves |
| `tests/conftest.py` | Dropped `AWS_DEFAULT_REGION`; added the socket-guard fixture |
| `tests/unit/test_conftest_fixtures.py` | Socket-guard tests |
| `tests/unit/adapters/test_dynamodb_alert_repository.py` | `ConsistentRead` spy test; bounded-`Config` tests; UTC-guard tests; corrected TTL mutation test; cleaned-up docstring |
| `tests/unit/adapters/test_ssm_config_repository.py` | Exact-name request tests; bounded-`Config` tests; message-truncation tests; memoization spy updated for `get_parameters` |
| `tests/unit/adapters/test_agent_prompt.py` | Split the operator-owned-text test; added checklist-sanitization test |
| `tests/unit/domain/test_snapshot.py` | Snapshot-boundary guard tests |
| `openspec/changes/scheduled-cycle/tasks.md` | Corrected 1.11's claim (+1.11a); superseded 1.20/1.22-23/1.30/1.32-33's `GetParametersByPath` text; reworded 1.36 |
| `openspec/changes/scheduled-cycle/design.md` | Amendments to D28 (socket guard, region) and D32 (`GetParameters`) |

## Status (fix round 1)

All MUST and SHOULD items addressed and mutation-verified where the review
demanded it; all MINOR items addressed or explicitly stated as not
applicable. No AWS command was run. No `cdk` command was run. Nothing was
pushed; no PR was opened.

---

# Fix Round 2

The re-review came back **fit to open** (PR 1 of 4), having verified rather
than read: two independent mutations on `ConsistentRead` re-confirmed
load-bearing, an AST walk confirming the seven requested SSM names exactly
match what `_parse` consumes with no asymmetry, eight live socket probes,
and the test-count arithmetic (1035 + 60 + 20 = 1115) re-derived
independently in a throwaway worktree. Five small items remained, three of
them claims in this round's own artifacts that reached further than the
code — exactly the genre this round existed to remove.

## 1. `design.md` — "including DNS resolution" was empirically false

A live probe found the guard's own blocked-address message naming a
**resolved public IP** (`('35.71.102.11', 443)`), meaning `getaddrinfo` ran
and real DNS traffic left the host before the guard fired.
`socket.getaddrinfo` on its own is not intercepted at all — only
`socket.socket.connect` is. Corrected D28's amendment to say what the guard
actually does (intercepts TCP `connect`) and to state the DNS gap
explicitly, rather than claiming coverage the code does not have. The IP
literal (`192.0.2.1`) in the tests was always only there so the *tests*
needed no real lookup — that part was accurate and is unchanged.

## 2. `CLAUDE.md` — "refuses any non-loopback connection outright" was broader than the code

`connect_ex` and UDP `sendto` both pass through the guard untouched — a live
probe sent a real UDP byte to `8.8.8.8:53` to confirm it. Nothing in
botocore's stack uses either mechanism, so this is not currently
exploitable, but the wording is narrowed to **TCP** specifically, with the
untouched paths named, since this is the file every future session reads
first and an overclaim here costs more than anywhere else.

## 3. `dynamodb_alert_repository.py` — a stale reference to a removed mechanism

The module docstring still read "Both `Query` (here) and
`GetParametersByPath` (`ssm_config_repository.py`) paginate" after fix round
1 moved the SSM adapter to `GetParameters` with no pagination at all. This
was the one remaining source-tree reference making that false claim — the
same defect class already caught and fixed in `ports/__init__.py` during
fix round 1, just out of sight in a different file. Corrected to describe
`Query`-only pagination and note the SSM adapter's mechanism change
explicitly, rather than silently dropping the cross-reference.

## 4. `ssm_config_repository.py` — one path still interpolated a full value

`_parse_positive_int`'s `value <= 0` branch raised with the parsed integer
interpolated directly, not through `_echo`. A probe with a 3000-digit
negative value produced a 3076-character exception message. Low severity —
the value is numeric-only by construction, since anything non-numeric fails
`int()` first and already goes through `_echo` via the `except ValueError`
path — but the apply-progress claim that `_echo` was applied at "every
site... both integer fields" was not true for this branch. Bounded it:
`_echo(value)` instead of `value`.

**Mutation proof, run live**: added
`test_an_oversized_negative_integer_value_is_truncated_in_the_message`
(a **3000**-digit negative value, deliberately under Python's own
~4300-digit integer-string-conversion limit, so it parses successfully and
reaches this branch rather than failing inside `int()` the way the existing
all-`9`s test does — confirmed by checking why that existing test passes at
all, since `"9" * 5000` is a *valid* positive integer and the earlier test
was unknowingly relying on Python's own conversion-limit `ValueError`, not
on `_parse_positive_int`'s logic). Confirmed RED first
(3076-character message, exact match to the probe) against the
unfixed code, then GREEN, then reverted the fix to re-confirm RED, then
restored it.

## 5. The socket guard's coverage of the actual regression path lived only in a review transcript

The existing tests prove the guard fires on a raw socket and discriminates
loopback from non-loopback, but the regression that motivated the guard
(design.md D28's amendment) is specifically a forgotten `mock_aws()`
letting `boto3` reach AWS — and that path was pinned by a reviewer's probe,
not by the suite. Added
`test_a_forgotten_mock_aws_still_cannot_reach_the_real_account`: constructs
a real `boto3.client("dynamodb", region_name="us-east-2")` outside any
`mock_aws()` context and calls `list_tables()`.

**Discovery while writing this test**: the first version asserted
`pytest.raises(RuntimeError, ...)` and failed — not because the guard
didn't fire, but because `botocore`'s HTTP layer catches the guard's
`RuntimeError` at the socket layer and re-raises it wrapped as
`botocore.exceptions.HTTPClientError`. The failure output confirmed the
guard fired for real, against a real `boto3` call, naming a real resolved
AWS IP (consistent with item 1's finding above) — this is itself the live
proof the test exists to pin, not a bug in the test. Corrected the
assertion to expect `HTTPClientError` with the guard's message inside it,
which is what a real caller of this client would actually see.

No additional mutation was performed on this one: disabling the guard to
prove it would require letting a real socket connection actually complete
against AWS's endpoint, which conflicts with this task's own "no AWS
commands, no live network" constraint. The RED run above (before the
assertion was corrected) already is that evidence — it shows the guard's
message with a genuine resolved AWS IP address, produced by a real,
unmocked `boto3` call.

## Verification (fix round 2, all green)

```
uv run pytest                      # 1117 passed, 15 deselected (no -q: this project's own
                                    # addopts already sets -q; a second one on the command
                                    # line was silently swallowing the summary all session)
uv run --directory agent pytest    # 32 passed
uv run ruff check                  # All checks passed
uv run ruff format --check         # all files already formatted
uv run mypy                        # Success: no issues found in 45 source files
uv run pytest tests/hygiene/       # 49 passed (run after git add)
```

## Files changed (fix round 2)

| File | What changed |
|---|---|
| `openspec/changes/scheduled-cycle/design.md` | Corrected D28 amendment's DNS-resolution claim |
| `CLAUDE.md` | Narrowed the socket guard's description to TCP |
| `src/rain_alert/adapters/dynamodb_alert_repository.py` | Removed the stale `GetParametersByPath` cross-reference |
| `src/rain_alert/adapters/ssm_config_repository.py` | `_echo()` applied to the `value <= 0` branch of `_parse_positive_int` |
| `tests/unit/adapters/test_ssm_config_repository.py` | New oversized-negative-integer truncation test |
| `tests/unit/test_conftest_fixtures.py` | New end-to-end `boto3`-shaped guard test |

Nothing else was changed: no test made inert, no assertion weakened, no
production path loosened, per the re-review's own finding and this round's
explicit instruction.

## Status (fix round 2)

All five items addressed. No AWS command was run (the new `boto3` test
performs one real DNS lookup as an unavoidable consequence of exercising
the actual regression path — the TCP connect itself is blocked locally, per
item 1's own corrected description of the guard's scope). No `cdk` command
was run. Nothing was pushed; no PR was opened — the coordinator opens it.
