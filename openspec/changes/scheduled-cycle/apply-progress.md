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
probe sent a real UDP byte to `8.8.8.8:53` to confirm it. The wording is
narrowed to **TCP** specifically, with the untouched paths named, since this
is the file every future session reads first and an overclaim here costs
more than anywhere else.

**Corrected again after the pre-PR security review.** The first correction
said "nothing in botocore's stack uses either mechanism", and that is false:
`botocore.monitoring.SocketPublisher` sends client-side monitoring events
over UDP `sendto`. It is off by default, its default host is loopback, and
nothing in this repository enables it — so the conclusion ("not currently
exploitable") survives, but the reason given for it did not.

This was the **fourth** instance of the same overclaim about this one guard
on this one branch: `design.md`'s DNS claim, `CLAUDE.md`'s original wording,
the test docstring, and then the sentence written to correct the third. The
pattern worth carrying forward is not "check the guard" — it is that a
claim about what *nothing* does is a claim about an entire dependency tree,
and it is cheaper to write than to verify. Prefer naming what was checked
("no path this project enables") over asserting a universal.

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

---

# Phase 2 (PR 2 of 4, stacked-to-main)

**Scope**: Phase 2 only — `run_once` extraction, `lambda_handler`,
`build_cloud_deps`, the architecture-boundary extension. Tasks 2.1–2.23.
Phase 3, Phase 4 and the Owner-Run section remain out of scope. Task 2.22
(fresh-context security review) is explicitly **not** performed here — see
Deviations.

**Branch**: `feat/scheduled-cycle-entrypoints`, from `feat/scheduled-cycle-adapters`
at `41080e6` (PR 1, still open as PR #19, not yet merged). This PR will
target `main` once #19 merges.
**Delivery strategy**: `ask-on-risk`, resolved by the orchestrator to
`stacked-to-main` — PR 2 of 4.
**Mode**: Strict TDD (RED → GREEN, mutation proofs on every load-bearing
assertion named in `tasks.md`).

## Golden baseline (task 2.1)

Captured **before** any change to `cli.py`, as the very first action of this
batch:

```
uv run rain-alert-cycle --now 2026-09-28T12:00:00+00:00 \
  --state-file /tmp/golden_fixtures/state.json \
  --offline-fixtures /tmp/golden_fixtures            # plain mode
uv run rain-alert-cycle --json --now 2026-09-28T12:00:00+00:00 \
  --state-file /tmp/golden_fixtures/state.json \
  --offline-fixtures /tmp/golden_fixtures            # --json mode
```

`--offline-fixtures` held copies of `tests/fixtures/senamhi/warnings_table.html`
and `tests/fixtures/open_meteo/forecast_72h.json`, renamed to `senamhi.html`/
`open_meteo.json`. Stdout, stderr and exit code from both invocations saved
under `tests/fixtures/golden/{plain,json}.{stdout,stderr,exit_code}.txt`,
committed. Both exit 0; the `--json` run's stderr carries one
`[OPERATOR NOTICE] source_unavailable` block (`open_meteo` fell outside the
fixture's own horizon at this `--now`) — a real, non-trivial branch, not an
empty baseline.

## Tasks completed

### The extraction itself
- [x] 2.2 [RED] `test_run_once_executes_the_calibrated_sequence` — `ModuleNotFoundError: No module named 'rain_alert.entrypoints.run_once'`
- [x] 2.3 [GREEN] `entrypoints/run_once.py` created (see "A real gap this task's own text left open" below for what else moved here)
- [x] 2.4 [RED→proof] byte-identity test (see "A RED that could not be RED" below)
- [x] 2.5 [GREEN] `cli.py:main` rewired to `run_once(deps, publisher, report=err)`
- [x] 2.6 [RED] `test_snapshot_publish_failure_does_not_cost_the_alert`
- [x] 2.7 [GREEN] confirmed by 2.3; mutation proof below

### `lambda_handler`
- [x] 2.8 [RED] `test_handler_ignores_event_payload` — `ModuleNotFoundError: No module named 'rain_alert.entrypoints.lambda_handler'`
- [x] 2.9 [GREEN] `entrypoints/lambda_handler.py::handler`
- [x] 2.10 [RED] `test_handler_does_not_catch_construction_failures`
- [x] 2.11 [GREEN] confirmed no `try`/`except`; mutation proof below
- [x] 2.12 [RED] `test_handler_logs_as_json_for_every_completed_outcome` (`none`/`degraded`) + `test_a_deduplicated_cycle_still_logs_a_document`
- [x] 2.13 [GREEN] the unconditional `print(as_json(...))` line
- [x] 2.14 [RED] `test_dedup_window_derives_from_actual_clock`
- [x] 2.15 [GREEN] confirmed — the window bound is `deps.now()` throughout, nothing hardcoded in the handler

### `build_cloud_deps`
- [x] 2.16 [RED] `test_build_cloud_deps_constructs_only_console_notifier` — `AttributeError: module 'rain_alert.entrypoints.wiring' has no attribute 'SsmConfigRepository'`
- [x] 2.17 [GREEN] `wiring.py::build_cloud_deps()` (see `TABLE_NAME` amendment below)
- [x] 2.18 [RED] `test_build_cloud_deps_reuses_select_composer`
- [x] 2.19 [GREEN] confirmed — `select_composer` is the same function, same call shape, as `build_local_deps`

### Architecture boundary
- [x] 2.20 [RED] `test_lambda_handler_never_imports_local_only_modules` + `test_the_scan_detects_a_planted_violation` — both new (the scanner itself is new test-file infrastructure, so there is no prior "clean tree" state to be RED against here; the planted-violation test is what actually starts red until the scanner exists — `NameError`)
- [x] 2.21 [GREEN] `LAMBDA_HANDLER_FORBIDDEN_MODULES` + `_forbidden_module_imports`; mutation proof below

### Close-out
- [ ] 2.22 **Not run** — see Deviations
- [x] 2.23 PR-2 verification gate — green (see Verification below)

## TDD Cycle Evidence

| Task | RED (real failure observed) | GREEN | REFACTOR |
|---|---|---|---|
| 2.2/2.3 | `ModuleNotFoundError: No module named 'rain_alert.entrypoints.run_once'` | `run_once.py` created; sequence extracted verbatim from `cli.py:396-409` | `as_json`/`source_notes`/`MAX_RECENT_ALERTS`/`_recent_alerts` moved alongside (see below) |
| 2.4 | Could not be a real RED against the *original* `cli.py` — see below | rewired `main`; byte-identity green | n/a |
| 2.6/2.7 | `ModuleNotFoundError` (same module) | guard confirmed; mutation proof below | n/a |
| 2.8/2.9 | `ModuleNotFoundError: No module named 'rain_alert.entrypoints.lambda_handler'` | `handler` created | n/a |
| 2.10/2.11 | Same `ModuleNotFoundError` at collection; once the module existed, the construction-failure test needed no separate RED beyond that | confirmed no `try`/`except`; mutation proof below | n/a |
| 2.12/2.13 | Same `ModuleNotFoundError` | one `as_json` line, unconditional | n/a |
| 2.14/2.15 | Same `ModuleNotFoundError`; first attempt asserted on `query_calls[0]`, which is `AlertPolicy`'s own dedup check (bounded by `assessment.window.end`, not `now`) and genuinely failed for the *wrong* reason — corrected to read `query_calls[-1]`, `_recent_alerts`'s own call | window bound is `deps.now()` throughout | Test also needed a real publisher (`RecordingPublisher`) — `_recent_alerts` is skipped entirely when the publisher is `None`, so the first version of this test exercised only `AlertPolicy`'s query and never `_recent_alerts`'s |
| 2.16-2.19 | `AttributeError: module 'rain_alert.entrypoints.wiring' has no attribute 'SsmConfigRepository'` | `build_cloud_deps` implemented | n/a |
| 2.20/2.21 | `NameError` (scanner helper did not exist) | scanner + constants implemented | n/a |

## Mutation Proofs (live, run and reverted)

1. **Snapshot-publish guard (task 2.7).** Removed the `try`/`except` around
   `publisher.publish(...)` in `run_once.py`. Result:
   `test_snapshot_publish_failure_does_not_cost_the_alert` failed with the
   real `OSError: bucket on fire` propagating uncaught out of `run_once` —
   confirming the guard is load-bearing, not redundant with anything
   upstream. Reverted.

2. **Handler's uncaught-exception posture (task 2.11).** Wrapped the
   handler's body in `try: ... except Exception: return {"sent": False}`.
   Result: `test_handler_does_not_catch_construction_failures` failed with
   `Failed: DID NOT RAISE ValueError` — the exception was swallowed exactly
   as the `scheduled-execution` spec forbids. Reverted.

3. **Architecture-boundary scanner (task 2.20/2.21).** Temporarily made
   `_forbidden_module_imports` return `set()` unconditionally. Result:
   `test_the_scan_detects_a_planted_violation` failed
   (`assert set() == {'rain_alert.entrypoints.cli', 'rain_alert.adapters.local.json_alert_repository',
   'rain_alert.adapters.local.static_config_repository'}`) — confirming the
   scanner, not merely the constant, is what the test exercises. Reverted.

4. **Byte-identity test's own teeth (task 2.4, see below for why this
   replaces the task's stated RED).** Mutated `cli.py:main`'s final `print`
   to call `render(result, config)` unconditionally, ignoring `--json`.
   Result: `test_cli_json_output_is_byte_identical_after_extraction` failed,
   comparing a human-readable block against the golden JSON document.
   Reverted.

## Discoveries

- **A RED that could not be RED (task 2.4).** The golden fixture was
  captured, by construction, from the exact pre-extraction `cli.py:main`.
  Running the byte-identity test against that same unmodified code
  necessarily passes — there is no behavior difference to detect yet, so
  "Expects: fails until `cli.py:main` is rewired to delegate" does not hold
  literally. Verified this directly: restored the original `cli.py` from
  `git show HEAD:...` mid-task and re-ran the test — it passed, not failed.
  The test's real value (that it *can* catch an extraction regression) is
  demonstrated instead by mutation proof 4 above, against the *rewired*
  code. Recorded here rather than silently reporting "2.4 was RED as
  specified."
- **`query_calls[0]` is not `_recent_alerts`'s call (task 2.14).**
  `application/policies.py::AlertPolicy` also calls
  `alerts_with_window_start_between`, as part of the dedup *decision* inside
  `RunAlertCycle.execute()` — bounded by `assessment.window.end` (the
  48-hour forecast horizon), not by `now`. The first version of
  `test_dedup_window_derives_from_actual_clock` asserted on `query_calls[0]`
  and failed for this unrelated reason before it was corrected to
  `query_calls[-1]` (`_recent_alerts`'s own call, made after
  `RunAlertCycle.execute()` returns) — worth naming so a future reader of
  this test does not mistake `AlertPolicy`'s query for `_recent_alerts`'s.
- **`_recent_alerts` never runs when `publisher is None`.** The same test
  originally monkeypatched `select_snapshot_publisher` to return `None`
  (mirroring several other tests in this batch), which meant `run_once`'s
  publish guard — and `_recent_alerts` inside it — never executed at all.
  Fixed by wiring in `RecordingPublisher` instead.

## A real gap this task's own text left open — `as_json`'s location

Task 2.5's literal text: "argparse, `render()`, `as_json()` and the exit
code stay in `cli.py`, untouched in shape." Task 2.20/design.md D29's own
architecture test: `entrypoints/lambda_handler.py` must import neither
`entrypoints.cli` nor either local-only adapter. Design.md D29 itself
states the handler "prints `as_json(result, config)` as one line to
stdout" — naming the exact function `cli.py` was told to keep.

These two instructions cannot both hold literally: if `as_json` stays
defined in `cli.py`, `lambda_handler.py` cannot call it without importing
`cli.py`, which the architecture test (also written by this same task list)
forbids. This is not a scope decision I get to make silently either way, so
it is named here rather than picked without comment.

**Resolution applied**: moved `as_json`'s *definition* to `run_once.py`
(already the shared module both entry points depend on for `default_now`
and `run_once` itself), and re-exported it from `cli.py` via a plain import
— exactly the treatment `design.md` already prescribes for `default_now`
alone ("`cli.py` re-exports `default_now`... so `cli.default_now`... still
resolves"). The result: `cli.as_json` is unchanged from every caller's
point of view (same import path, same signature, same behavior — confirmed
by the byte-identity test staying green with no changes to its own
assertions), `lambda_handler.py` calls the same function with no forbidden
import, and the architecture test holds for real rather than being routed
around. `MAX_RECENT_ALERTS`/`_recent_alerts`/`source_notes` (renamed from
`_source_notes`, since `cli.py`'s `render()` also needs it) moved for the
same structural reason — `_recent_alerts` is called from inside the
extracted publish guard and cannot stay behind in a module the new code
does not import.

`tests/unit/entrypoints/test_cli.py`'s own import of `MAX_RECENT_ALERTS`
was updated to pull it from `run_once` instead of `cli` (a test-only
change); its imports of `as_json`/`default_now` from `cli` were left
unchanged, since both are still re-exported from there.

## Deviations from Design

1. **Task 2.22 (fresh-context security review) was not performed.** The
   launching prompt is explicit: "Do not dispatch subagents." A genuinely
   fresh-context review cannot happen from within this single session — the
   same constraint Phase 1's task 1.34 hit, and the same resolution: **not**
   performed inline and reported as done. Flagged here as outstanding for
   the coordinator, which is the correct call per that same precedent.
2. **`as_json`'s definition location** — see the dedicated section above.
3. **Table name constant added, not specified by any task text.**
   `DynamoDbAlertRepository(table_name)` requires a table name, and neither
   `tasks.md` nor `design.md`'s Phase 2 material names one (design.md D26
   states the CDK-side literal, `ferrenafe-alerts-sent`, but Phase 3 has not
   landed yet). Added `TABLE_NAME = "ferrenafe-alerts-sent"` to
   `dynamodb_alert_repository.py`, matching the literal D26 already commits
   the CDK stack to, so both sides of the Python/TypeScript boundary name
   the same physical table without waiting for Phase 3.
4. **Task 2.12's three named outcomes (`none`/degraded/deduplicated)
   implemented as two parametrized cases plus one dedicated test**, not one
   parametrized-over-three test. Same coverage, split for a clearer failure
   message per outcome and because constructing a genuinely deduplicated
   `build_fake_deps()` graph (a prior `AlertRecord` inside the current
   window) needed enough extra setup to read better as its own test.

## Issues Found

None beyond what is captured in Discoveries and Deviations above.

## Verification (all commands run bare — no `-q` on the command line, since
`pyproject.toml`'s own `addopts` already sets it and a second one silently
suppresses the summary line, per Phase 1's own discovery)

```
uv run pytest                       # 1132 passed, 15 deselected
uv run --directory agent pytest     # 32 passed (unaffected by this change)
uv run ruff check                   # All checks passed
uv run ruff format --check          # all files already formatted (one file auto-formatted during this batch)
uv run mypy                         # Success: no issues found in 47 source files
uv run pytest tests/unit/entrypoints/test_cli.py -k byte_identical
                                     # 2 passed (re-run per 2.23's own instruction)
env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN \
  -u AWS_DEFAULT_REGION -u AWS_PROFILE uv run pytest
                                     # 1132 passed, 15 deselected, still offline
uv run pytest tests/hygiene/        # 49 passed (run after `git add`, per convention;
                                     # failed with two phantom-citation findings before
                                     # `git add` — both were the new, not-yet-committed
                                     # files themselves, not real defects)
```

**Real test count, measured, not estimated**: root suite grew from 1117
(Phase 1's fix-round-2 baseline) to 1132 — **15 tests added** this batch:
3 in `test_run_once.py`, 6 in `test_lambda_handler.py`, 2 in
`test_layer_boundaries.py`, 2 in `test_cli.py` (byte-identity), 2 in
`test_wiring.py` (`build_cloud_deps`). Counted via
`pytest --collect-only -q` on each new/changed test file, summed and
cross-checked against the root-suite delta (1132 − 1117 = 15, matches
exactly).

No AWS command was run against the real account. No `cdk` command was run
(out of scope for Phase 2).

## Workload / PR Boundary

- **Mode**: stacked PR slice (`stacked-to-main`), PR 2 of 4.
- **Current work unit**: Unit 2 — `run_once` extraction, `lambda_handler`,
  `build_cloud_deps`, the architecture-boundary extension.
- **Boundary**: starts from `feat/scheduled-cycle-adapters` at `41080e6`
  (PR 1, not yet merged); ends with the CLI unchanged in observable
  behavior, the scheduled entry point fully wired against the same
  `CycleDependencies` the CLI uses, and the architecture boundary that
  keeps the two paths from silently converging. This PR is inert on its
  own in the sense that matters for rollback: `lambda_handler.py` is never
  invoked by anything until Phase 3's CDK stack wires it to a real
  `EventBridge Scheduler` target, so merging this PR changes nothing about
  what the CLI does today.
- **Estimated review budget impact**: forecast was 550–800 changed lines;
  actual is markedly smaller — this slice mostly *moves* existing code
  (four functions and two constants relocated verbatim from `cli.py` to
  `run_once.py`) rather than writing new production logic, and the new
  production code (`lambda_handler.py`, `build_cloud_deps`) is short. Not
  split further; well inside budget.

## Remaining Tasks (not started, out of scope for this batch)

- [ ] Phase 3 — `contracts/scheduled-cycle.json` + CDK stack (PR 3)
- [ ] Phase 4 — Runbook + region correction (PR 4)
- [ ] Owner-Run — live deploy and verification (not automated by sdd-apply)
- [ ] Task 2.22 — fresh-context security review on the PR-2 branch diff (coordinator's, not sdd-apply's)

## Status

**22/23 Phase 2 tasks complete** (2.1–2.21, 2.23; 2.22 outstanding by
design — see Deviations). Branch `feat/scheduled-cycle-entrypoints`, not
pushed, no PR opened, per instructions.

---

# Phase 2, Fix Round 1

Two reviews came back **PASS** (spec compliance, code quality) and the
security review found no Critical/High/Medium. Four items came back: two
Important (the same defect class — asserting a precondition or a bound
without actually establishing/pinning it), one Minor, one stale-spec
correction. Same rules as the original apply: strict TDD, real RED pasted
before each fix, no `unittest.mock`, English artifacts, no AI attribution,
no AWS commands, no push, no PR.

## Important

**1. `test_snapshot_publish_failure_does_not_cost_the_alert` asserted a
precondition it never established.** The docstring and task 2.6 both claim
the publish raises "after `notifier.send_alert` already ran" — it did not:
`build_fake_deps()`'s calm default never authorizes a send, so
`send_alert` is never called and the `scheduled-execution` spec scenario's
GIVEN was unexercised while the docstring claimed coverage.

**RED, pasted**: added `assert len(deps.notifier.alert_calls) == 1` to the
*unmodified* test body (still `build_fake_deps()`'s calm default) —
`AssertionError: assert 0 == 1`, `where 0 = len([])`. Confirmed the gap
exactly as the reviewer found it.

**Fix**: added `_authorizing_forecast()` (same pattern as
`test_run_alert_cycle.py::_forecast`, clears `DEFAULT_CONFIG`'s
`imminent_mm_24h=20.0`/`imminent_probability_pct=70`) and wired it via
`build_fake_deps(forecast=_authorizing_forecast())`. The test now asserts
`result.sent is True`, `len(deps.notifier.alert_calls) == 1`, and the
ordering itself on the shared `CallLog` —
`deps.notifier.log.position_of("notifier", "send_alert") <
deps.alerts.log.position_of("alerts", "record_alert")` — rather than only
inferring the precondition from a later effect. Green: `uv run pytest
tests/unit/entrypoints/test_run_once.py` → 3 passed.

The four CLI-level equivalents at `test_cli.py:655-700` share the same gap
and were left as-is — pre-existing, not introduced by this phase, and out
of the scope the review named.

**2. `test_wiring.py` read `deps.alerts._table_name` through a
port-typed value.** `CycleDependencies.alerts` is typed `AlertRepository`;
reading a private attribute through it could pass while `build_cloud_deps`
wired the wrong table, since renaming or restructuring `_table_name` would
not be caught by anything reading it directly the way this test did.

**Fix**: added `_SpyDynamoDbClient`/`_SpyDynamoDbQueryPaginator` (the same
wrap-and-delegate spy shape `test_dynamodb_alert_repository.py`'s
`_ConsistentReadRecordingClient` already uses), monkeypatching
`dynamodb_alert_repository.boto3.client` — the same seam
`test_the_key_env_var_is_honoured_when_the_bucket_is_set` already uses for
`S3SnapshotPublisher`. The new
`test_build_cloud_deps_wires_the_alerts_repository_to_the_correct_table`
calls `deps.alerts.alerts_with_window_start_between(...)` and asserts on
`spy.query_calls[0]["TableName"]` — the real `TableName=` a `Query` call
carries — never touching `_table_name`. The old private-attribute
assertion was removed, not merely supplemented.

**Mutation proof, run live**: temporarily changed
`wiring.py::build_cloud_deps` to construct
`DynamoDbAlertRepository("wrong-table")`. Result:
`AssertionError: assert 'wrong-table' == 'ferrenafe-alerts-sent'`.
Reverted.

## Minor

**3. `test_dedup_window_derives_from_actual_clock` pinned only the upper
query bound.** `query_calls[-1][2] == now` was asserted; `query_calls[-1][1]`
(the lower bound, `earliest_start`) never was — so a hardcoded six-hour
lookback in `_recent_alerts` would still pass this test, since the upper
bound is `now` either way.

**RED, pasted, against a live mutation**: changed `run_once.py::_recent_alerts`
to `earliest_start = now - timedelta(hours=6)` (hardcoded, ignoring
`config.dedup_lookback_hours`). The *original* test (end-bound only)
**still passed** — `1 passed, 5 deselected` — confirming the gap exactly as
named: this mutation was invisible to the test as it stood.

**Fix**: added `assert first_call[1] == first_now - timedelta(hours=DEFAULT_CONFIG.dedup_lookback_hours)`
(and the same for `second_call`). Re-ran against the still-active mutation:
now failed for real (`AssertionError`, comparing the hardcoded 6h bound
against the real 72h-derived one). Reverted the production mutation;
re-ran — green: `uv run pytest tests/unit/entrypoints/test_lambda_handler.py`
→ 6 passed.

## Stale spec

**4. `specs/scheduled-execution/spec.md:35`** stated
`run_once(deps, publisher) -> CycleResult`; the implementation returns
`tuple[CycleResult, AlertConfig]` and takes a required keyword-only
`report: TextIO` (both sanctioned by design.md D29). Added an "Amendment
(Phase 2 apply, fix round 1)" paragraph in place, matching this project's
own precedent (design.md's `ALERT_RETENTION_DAYS`/D28/D32 amendments) —
the original text is left intact for the record, the correction states
what was actually built and why.

## A gotcha for whoever re-verifies this

`pytest -k lambda_handler` (lowercase, with the underscore) is a **partial**
selector on `TestLambdaHandlerNeverImportsLocalOnlyModules`, and the part
it silently drops is the one a mutation proof needs. Verified directly,
node ids visible (`--collect-only` with no extra `-q` — a second `-q`
stacks onto this project's own `addopts` and collapses the output to a
per-file count, hiding exactly what this check needs to see):

```
uv run pytest tests/architecture -k lambda_handler --collect-only
  -> only test_lambda_handler_never_imports_local_only_modules  (1/12 collected)
     test_the_scan_detects_a_planted_violation NOT selected

uv run pytest tests/architecture -k LambdaHandler --collect-only
  -> both tests in the class (2/12 collected)
```

The reason: `test_lambda_handler_never_imports_local_only_modules`'s own
*method name* happens to contain the lowercase substring `lambda_handler`,
so `-k lambda_handler` matches it by accident of naming, not by matching
the class. `test_the_scan_detects_a_planted_violation`'s method name
contains no such substring at all — it is only reachable through the
CamelCase class name, `TestLambdaHandlerNeverImportsLocalOnlyModules`,
which the lowercase-with-underscore query never matches. A reviewer
re-running "the lambda_handler architecture test" with `-k lambda_handler`
gets a clean pass while the triangulation test — the one that actually
proves the scanner can fail — never runs at all. The correct selector is
the class name (`-k LambdaHandlerNeverImports` or `-k LambdaHandler`) or
the explicit node id
(`tests/architecture/test_layer_boundaries.py::TestLambdaHandlerNeverImportsLocalOnlyModules::test_the_scan_detects_a_planted_violation`).

## Verification (fix round 1, all commands bare — no `-q`)

```
uv run pytest                       # 1133 passed, 15 deselected (+1 net: the new
                                     # table-wiring test; the other three fixes
                                     # strengthened existing tests, added none)
uv run --directory agent pytest     # 32 passed (unaffected)
uv run ruff check                   # All checks passed
uv run ruff format --check          # all files already formatted
uv run mypy                         # Success: no issues found in 47 source files
env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN uv run pytest
                                     # 1133 passed, 15 deselected, still offline
uv run pytest tests/hygiene/        # green (run after git add)
```

No AWS command was run. No `cdk` command was run. Nothing was pushed; no
PR was opened — the coordinator opens it.

## Files changed (fix round 1)

| File | What changed |
|---|---|
| `tests/unit/entrypoints/test_run_once.py` | `_authorizing_forecast()`; the publish-failure test now forces and proves a real, ordered send |
| `tests/unit/entrypoints/test_wiring.py` | `_SpyDynamoDbClient`/`_SpyDynamoDbQueryPaginator`; the table-wiring assertion moved off `_table_name` onto observable `TableName=` |
| `tests/unit/entrypoints/test_lambda_handler.py` | dedup-window test now pins both query bounds, not only the upper one |
| `openspec/changes/scheduled-cycle/specs/scheduled-execution/spec.md` | `run_once`'s signature amended in place to match what was built |

## Status (fix round 1)

Both Important items and the Minor item fixed and mutation/RED-verified
live, per the review's own probes. The stale spec line corrected in place.
Task 2.22 (fresh-context security review) remains outstanding for the
coordinator, as it was before this round — this round's own reviews were
external to this session, not performed by `sdd-apply`.

---

# Apply Progress: Scheduled Cycle — Phase 3 (PR 3 of 4)

**Scope**: Phase 3 only — `contracts/scheduled-cycle.json` + the CDK
`ScheduledCycleStack` (synth-only, no deploy). Task 3.30 (fresh-context
security review) is explicitly out of scope for `sdd-apply` and flagged
outstanding below, as tasks 1.34 and 2.22 were in the prior two phases.

**Branch**: `feat/scheduled-cycle-infra`, from `feat/scheduled-cycle-entrypoints`
(Phase 2's tip, open as PR #20, not yet merged).
**Delivery strategy**: `ask-on-risk`, resolved to `stacked-to-main` — PR 3 of
4 in the chain.
**Mode**: Strict TDD (RED → GREEN, mutation proofs on the tasks that name
one explicitly: 3.6, 3.10 (corrected — see below), 3.15, 3.21, 3.28).

**A note on the RED/GREEN cadence in this phase, stated rather than implied**:
CDK's synthesis-based testing model means the natural first RED for a
not-yet-existing construct is a compile error or a missing-resource
assertion failure, not a runtime exception the way Python's is. The contract
file (3.5/3.6) and the `bin/infra.ts` wiring (3.24/3.25) were each taken
through a genuine, isolated RED (`FileNotFoundError`; `TypeError: ... is not
a function` / `Cannot find module`) before their GREEN. The CDK stack itself
(3.7–3.23) was written and its test file developed together, iterating
against real synth failures observed live (a `CannotSupplyBothDayAndWeekDay`
CDK validation error, three `IAM::Role`-count assertion failures from an
unaccounted-for Scheduler invocation role, an `Fn::Join` shape mismatch) —
every failure shown below under "Real failures observed and resolved" was
genuine and unplanned, not scripted. The three tasks that call for a
standalone mutation proof (3.10, 3.15, 3.21) were each performed as their
own isolated revert-verified step, after the stack was green, exactly as
written.

## Facts confirmed (tasks 3.1–3.4)

**3.1** — Confirmed against the installed `aws-cdk-lib@2.270.0` source
(`infra/node_modules/aws-cdk-lib/aws-scheduler/lib/schedule.js`,
`.../lib/schedule.d.ts`, `.../aws-scheduler-targets/lib/target.d.ts`):

- The L2 `Schedule` construct has **no** `state` or `scheduleExpressionTimezone`
  prop. `state` (`'ENABLED'`/`'DISABLED'`) and `scheduleExpressionTimezone`
  are `CfnSchedule` (L1) properties, rendered by `Schedule` from
  `props.enabled ?? true` and from `ScheduleExpression.cron({ timeZone })`'s
  `TimeZone.timezoneName` respectively — confirmed by reading
  `schedule.js`'s compiled constructor directly:
  `state: props.enabled ?? true ? "ENABLED" : "DISABLED"`,
  `scheduleExpressionTimezone: props.schedule.timeZone?.timezoneName`.
- The retry policy props (`maxEventAge: Duration`, `retryAttempts: number`)
  are exactly as the launch prompt's correction stated — but they live on
  `ScheduleTargetBaseProps` (the *target's* construction props, e.g.
  `new scheduler_targets.LambdaInvoke(fn, { maxEventAge, retryAttempts })`),
  not on `ScheduleProps` directly. Implemented that way.
- `CfnSchedule`'s `State`/`ScheduleExpressionTimezone` are plain strings,
  confirmed against `scheduler.generated.d.ts`.

**3.2** — Confirmed: `iam.ManagedPolicy.fromManagedPolicyArn` compiles to a
plain `Import` class holding the ARN string verbatim, no parsing performed.
`arn:aws:iam::ACCOUNT:policy/SnapshotWriter` is accepted without error.

**3.3** — Confirmed against AWS's live documentation (fetched at apply time:
`docs.aws.amazon.com/AmazonCloudWatch/latest/logs/FilterAndPatternSyntax.html`):
*"Enclose exact phrases and terms that include non-alphanumeric characters
in double quotation marks."* The launch prompt's hypothesis was exactly
right. Implemented as `FilterPattern.literal('"[SNAPSHOT NOT PUBLISHED]"')`
— `FilterPattern.literal` itself performs no quoting; it emits whatever
string it is given verbatim (confirmed by reading its compiled source: a
one-line `LiteralLogPattern` wrapper), so the double-quote wrapping had to
be supplied by the caller.

**3.4** — Confirmed in code, not merely "undocumented": `ScheduleTargetBaseProps`'s
own JSDoc states `retryAttempts` defaults to **185** and `maxEventAge`
defaults to **`Duration.hours(24)`**. Recorded as a stronger correction to
the task's own rationale in `tasks.md`.

## Real failures observed and resolved (the CDK stack's own RED/GREEN cycle)

1. `CannotSupplyBothDayAndWeekDay` — the cron expression initially supplied
   both `day: '*'` and `weekDay: '?'` explicitly; CDK's `CronOptions`
   rejects supplying both. Fixed by omitting `weekDay` entirely (its
   omission implies the correct `?` once `day` is given), producing the
   intended `cron(0 0,6,12,18 * * ? *)`.
2. Three test failures asserting exactly one `AWS::IAM::Role` in the
   template — `scheduler_targets.LambdaInvoke` creates its own Scheduler
   invocation role, distinct from the Lambda's execution role, so the
   template legitimately carries two roles. Fixed the test helper
   (`findFunctionRoleStatements`) to locate the Lambda's own role by
   following the function's `Role` `Fn::GetAtt` reference, rather than
   assuming there is exactly one role in the template.
3. The SSM grant test's `Fn::Join` shape assertion initially expected the
   static suffix fragment to start with `parameter${prefix}`; the actual
   rendered fragment is `:parameter/ferrenafe/rain-alert/<name>` (with a
   leading colon, from the ARN's own `:` separator before the resource
   segment). Fixed the assertion to match the real shape.

## A gap the design left open, filled at apply time (task 3.17)

Neither `design.md` nor `tasks.md` names the context key the AgentCore
runtime ARN is supplied through — D25 only lists `snapshotBucketName`/
`snapshotWriterPolicyArn` as required stack props, but task 3.17 requires
the role's AgentCore grant to be "scoped to exactly the one context-supplied
ARN." Added a third required prop, `agentRuntimeArn`, following the exact
pattern D25 already establishes for `snapshotWriterPolicyArn`: never
committed to `cdk.context.json` (the ARN embeds the account id), supplied
only via `-c agentRuntimeArn=<arn>` at deploy time, and `bin/infra.ts`
throws a named error if it is absent once an attempt is underway. This is a
design elaboration to close a real gap the two phase artifacts left open,
not a deviation from an explicit instruction — flagged here for review.

## `bin/infra.ts` — the `buildScheduledCycleStack` gate (task 3.24)

`snapshotBucketName` gates the *attempt*; `snapshotWriterPolicyArn` and
`agentRuntimeArn`, once the attempt is underway, are each a hard throw if
missing, never a default. Refactored `bin/infra.ts` to export a standalone
`buildScheduledCycleStack(app: cdk.App)` function (rather than only building
one fixed stack at module top-level) specifically so all three outcomes —
not synthesized at all, throws naming the missing key, and successfully
constructed — are independently testable with hand-built `App({ context })`
instances. This matters because `snapshotWriterPolicyArn`/`agentRuntimeArn`
are *never* present under a plain `npm test` run (no `cdk.context.json`
read, no `-c` flag), and the existing `PublicSnapshotStack` tests in the
same file must keep passing with zero context supplied.

`infra/cdk.context.json` created, carrying `snapshotBucketName` only, per
the binding constraint. **Placeholder value**: the real `PublicSnapshotStack`
bucket has no explicit `bucketName` (CloudFormation auto-generates one at
deploy time — `infra/lib/public-snapshot-stack.ts:18`), so the true deployed
name is not knowable from source and `sdd-apply` runs no AWS command to look
it up. `cdk.context.json` currently holds `"ferrenafe-public-snapshot-example"`
as an explicit placeholder — **the owner must replace this with the real
deployed bucket name before running `cdk deploy ferrenafe-scheduled-cycle`**,
flagged here and should also be flagged in the PR-3 description.

## SSM grant — superseded task text (task 3.15/3.16)

Task 3.15's original wording (`ssm:GetParametersByPath` on one prefix-ending
resource ARN) predates design.md D32's fix-round-1 amendment, which moved
the CDK grant to **seven exact `ssm:GetParameters` resource ARNs** — matching
the Python-side move from `GetParametersByPath` to `GetParameters` in Phase
1. Implemented and tested per the amendment (design.md D32: *"the CDK test
now asserts each of the seven granted ARNs starts with it, rather than
asserting one granted resource ends with it"*), not per the stale task text.
Corrected in `tasks.md` in place.

## Mutation proofs (run live)

**3.6 — contract file.** `READ_TIMEOUT` bumped 35.0 → 40.0 in
`agentcore_invoker.py` without updating the contract file:
`test_contract_matches_read_timeout` failed (`assert 35.0 == 40.0`).
Reverted; green.

**3.10 — corrected, and the correction is the actual finding.** The task's
premise names the wrong assertion. With `RAIN_ALERT_AGENT_TIMEOUT_S`
mutated to `"10"`:
- Relation 2 (task 3.8, `Timeout(120) > env + headroom(60)`) becomes
  `120 > 70`, which still holds — lowering the env var makes Relation 2
  *easier* to satisfy, not harder. Verified live:
  `npm test -- scheduled-cycle-stack -t "exceeds the agent deadline"` →
  **1 passed**, not red.
- Relation 1 (task 3.7, `env >= contract.agent_read_timeout_seconds(35)`)
  is what actually breaks: `10 >= 35` is false. Verified live:
  `npm test -- scheduled-cycle-stack -t "env timeout at least"` → **failed**,
  `Expected 35 but received 10`. This is exactly D19's "why not 5 s"
  kill-switch-by-accident case — a too-low `RAIN_ALERT_AGENT_TIMEOUT_S` is a
  Relation 1 violation (the deployed value falls below the measured
  cold-start deadline), not a Relation 2 one. Corrected in `tasks.md`.
Reverted both; suite green.

**3.15 — SSM grant absence.** Temporarily added
`new ssm.StringParameter(this, 'PlantedParameter', { stringValue: '...' })`
to the stack: the "SSM grant" test failed
(`Expected length: 0, Received length: 1`, naming `PlantedParameter73DBFCD2`).
Reverted; green.

**3.21 — `reservedConcurrentExecutions`.** Temporarily removed the prop: the
"schedule" test failed (`Missing key 'ReservedConcurrentExecutions'`).
Reverted; green.

**3.28 — the build script's no-`.so`/no-`awscrt` guard.** The script's own
`rm -rf` at the top would erase a file planted beforehand, so the guard's
exact find/conditional logic was extracted and run standalone against an
isolated temp directory holding a planted `native.so`: exited 1, named the
planted file (`GUARD FIRED (expected): found .so at
/tmp/mutation-proof-lambda-build/planted_pkg/native.so`). The real build was
then re-run clean to confirm the guard passes on the actual artifact.

## Build script — real run, resolved dependencies (task 3.27/3.28)

`infra/scripts/build-lambda.sh` filters the one compiled wheel via
`uv export --frozen --no-dev --no-emit-project --no-emit-package awscrt`
(rather than post-hoc text-filtering the requirements file), then
`uv pip install --target infra/build/lambda --python-version 3.12
--python-platform aarch64-manylinux2014 --no-deps --requirements <file>`,
then copies `src/rain_alert` in.

Run once, live, against the real `uv.lock`. Resolved 16 packages (excluding
`awscrt`):

```
anyio==4.15.0
beautifulsoup4==4.15.0
boto3==1.43.98
botocore==1.43.98
certifi==2026.7.22
h11==0.16.0
httpcore==1.0.9
httpx==0.28.1
idna==3.19
jmespath==1.1.0
python-dateutil==2.9.0.post0
s3transfer==0.19.2
six==1.17.0
soupsieve==2.9.2
typing-extensions==4.16.0
urllib3==2.8.0
```

**Deviation from design.md D30's predicted list, stated rather than
silently corrected**: D30 named `boto3, botocore, httpx, httpcore, h11,
anyio, sniffio, idna, certifi, beautifulsoup4, soupsieve, jmespath,
python-dateutil, urllib3, s3transfer`. The real resolution additionally
includes `six` and `typing-extensions` (not named in D30) and does **not**
include `sniffio` (which D30 named). The conclusion D30 draws from its list
— "every remaining distribution is pure Python" — still holds: `find` over
the built tree confirms zero `*.so` files and zero `awscrt*` directories.
Built tree size: 31 MB unzipped (well inside the 250 MB Lambda limit, as
D30 predicted).

## Files changed

| File | Action | What |
|---|---|---|
| `contracts/scheduled-cycle.json` | Created | The golden contract (D31, D32) |
| `tests/unit/adapters/test_agentcore_invoker.py` | Modified | `TestScheduledCycleContract` (task 3.5/3.6) |
| `infra/lib/scheduled-cycle-stack.ts` | Created | `ScheduledCycleStack` — Lambda, table, IAM grants, schedule, alarms, topic (D25–D35) |
| `infra/test/scheduled-cycle-stack.test.ts` | Created | 9 tests across timeout relations, IAM, table, SSM, AgentCore, schedule, observability |
| `infra/bin/infra.ts` | Modified | `buildScheduledCycleStack`, the second stack's context gate |
| `infra/test/app.test.ts` | Modified | 5 new tests for `buildScheduledCycleStack` |
| `infra/cdk.context.json` | Created | `snapshotBucketName` only (placeholder — see above) |
| `infra/scripts/build-lambda.sh` | Created | The Lambda build script (D30) |
| `infra/.gitignore` | Modified | Ignore `build/` |
| `openspec/changes/scheduled-cycle/tasks.md` | Modified | Phase 3 tasks checked off; 3.1/3.2/3.3/3.4/3.10/3.15/3.17/3.26/3.28 corrected/elaborated in place |

## Verification (all run live)

```
cd infra && npm test                # 3 suites, 23 passed (was 9 before this phase)
cd infra && npx tsc --noEmit        # TypeScript compilation completed, exit 0
uv run pytest                       # 1134 passed, 15 deselected (+1: the new contract test)
uv run ruff check                   # All checks passed
uv run ruff format --check          # 115 files already formatted
uv run mypy                         # Success: no issues found in 47 source files
env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN uv run pytest
                                     # 1134 passed, 15 deselected, still offline
uv run pytest tests/hygiene -q      # 49 passed (run after `git add infra/` — found and
                                     # fixed one real phantom-citation defect, see below)
uv run pytest tests/unit/adapters/test_agentcore_invoker.py -k contract -q
                                     # 1 passed
```

No `cdk deploy`, `cdk bootstrap`, or AWS CLI command was run. `cdk synth`
(via `npm test`/`Template.fromStack`) and `moto` are both local.
`infra/scripts/build-lambda.sh` (task 3.27/3.28) uses `uv export`/`uv pip
install`, which may reach a package index over the network if a wheel is
not already cached — ordinary Python build tooling, not an AWS call, and
outside `npm test`/`pytest`'s own offline guarantee.

## Hygiene — a real defect found and fixed (task 3.26)

The first hygiene run (after `git add infra/`) failed
`test_no_docstring_or_comment_cites_a_path_git_does_not_have`: the new
contract test's docstring cited a hypothetical second contract file by path
in backticks, copied from design.md D31's own precedent list — that file
was never actually created in this repository. Removed the backtick-wrapped
phantom path from the docstring; re-ran, green. This is exactly the class
of defect `docs/blog/2026-09-21-three-docstrings-cited-a-file-that-was-never-written.md`
and this hygiene check exist to catch, and it was caught here, live, by
running the check rather than assuming it would pass.

## Deviations from design

None beyond the two gaps named above and filled with a documented,
pattern-consistent elaboration (the `agentRuntimeArn` context prop; the SSM
grant's amended mechanism, already specified by design.md D32's own
fix-round-1 text). No other place where the implementation diverges from
`design.md` or the (corrected) `tasks.md`.

## Issues found

None beyond the phantom-citation defect above (found and fixed) and the two
task-text corrections recorded in `tasks.md` (3.4's rationale, 3.10's
mislabeled relation).

## Line count (measured, not estimated)

`git diff --cached --stat`: **808 insertions, 33 deletions** across 10 files
(including the `tasks.md` documentation update). Excluding `tasks.md`'s own
62-line diff, the code/test diff is **777 insertions, 2 deletions across 9
files** — well under the ~1000-line split threshold `tasks.md` names for
this phase; no 3a/3b split was needed.

## Status

29/31 tasks complete (3.1–3.29, 3.31). Task 3.30 (fresh-context security
review) is outstanding — flagged for the coordinator, not performed inline,
matching this change's own precedent at tasks 1.34 and 2.22. Not pushed; no
PR opened.
