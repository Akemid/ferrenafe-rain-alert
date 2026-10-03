# Tasks: SENAMHI S3 Relay (off-AWS producer, freshness-checked reader)

This document exceeds the generic task-word budget deliberately, following
this repository's precedent (`scheduled-cycle/tasks.md`,
`composer-agent/tasks.md`). The change spans Python, TypeScript, a launchd
job and a live account; compressing RED/GREEN detail here only moves the
cost to apply time.

Conventions, as in the precedent: `[RED]` names the failing test and the
reason it must fail; `[GREEN]` is the minimal implementation. `[Docs]` marks
a lookup against current AWS documentation (no account touched).
`[Owner, AWS]` / `[Owner, Mac]` mark operator actions on the live
`ferrenafe` account or the producer machine. **sdd-apply MUST NOT run
`[Owner]` tasks.** Strict TDD is ON: the test is written and run first and
must fail for the stated reason, never for an import typo. No
`unittest.mock`; hand-written fakes only (`tests/support/fakes.py` style).
Commits are work units (behaviour plus its tests plus its docs), never
"models, then services, then tests". Commit messages follow Conventional
Commits with no AI attribution.

Rule references: R1-R9 are `proposal.md`; D36-D47 are `design.md` (D40, D46 amended and D47 added after Phase 0, 2026-10-02).

## Review Workload Forecast

| Field | Value |
|---|---|
| Estimated changed lines | 2900-4000 |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 -> PR 2 -> PR 3 -> PR 4 -> PR 5 (PR 4 may split into 4a/4b) |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending, orchestrator asks before apply |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

**Estimate by area** (additions plus deletions; precedent: prior changes in
this series overran their own estimates 2-10x, so the upper bound is the
planning number):

| Area | Files | Lines |
|---|---|---|
| Python domain and adapters | `values.py` (+3 members), `senamhi_relay.py`, `s3_relay_reader.py`, `contracts/senamhi-relay.json` | 450-600 |
| Entrypoints | `wiring.py` (`select_warning_provider`, `build_cloud_deps(env)`), `relay_push.py`, `pyproject.toml`, `ops/launchd/*.plist` | 250-350 |
| Python tests and fakes | `fake_s3.py`, `fakes.py`, `test_senamhi_relay.py`, `test_s3_relay_reader.py`, `test_relay_push.py`, `test_wiring.py`, `test_outage.py`, architecture test, contract test | 1100-1500 |
| Infra TS | `scheduled-cycle-stack.ts` (bucket, grants, user, policy, alarm, trail, log bucket, `relayEnabled`), `bin/infra.ts` | 200-300 |
| Infra TS tests | `scheduled-cycle-stack.test.ts` (design tests 9 and 10, mutation proofs) | 350-500 |
| Docs | runbook, ADR, architecture docs, evidence files | 550-750 |
| **Total** | | **2900-4000** |

### Suggested Work Units (proposed slice plan)

Each slice is independently reviewable, ends with a green gate, and has its
own rollback boundary. Nothing reads the relay in production until PR 4
sets `RAIN_ALERT_RELAY_BUCKET`, so PR 1-3 are inert on `main`.

| Unit | Goal | PR | Est. lines | Rollback boundary |
|---|---|---|---|---|
| 0 | Documentation lookups that code depends on (Phase 0), recorded in apply-progress and evidence | no PR (feeds PR 1-4) | n/a | none, read-only |
| 1 | Domain reasons, `contracts/senamhi-relay.json`, pure helpers, `RelayWarningProvider`, `FakeRelayReader`, outage-notice tests. **No wiring, no S3.** | PR 1 | 700-950 | Revert: nothing constructs the provider |
| 2 | `S3RelayReader` + `FakeS3Client`, `select_warning_provider`, `build_cloud_deps(env)`, `on_read` stdout record | PR 2 | 650-900 | Revert: env var is unset everywhere, scraper path unchanged |
| 3 | Producer `rain-alert-relay-push` + launchd plist template | PR 3 | 450-650 | Revert: producer is a new script nothing imports |
| 4 | Infra: bucket, grants, user, `RelayWriter`, alarm, `relayEnabled`, then D46 trail. Split 4a (tasks 4.1-4.17) / 4b (4.18-4.25) if it passes ~1000 lines | PR 4 (4a/4b) | 850-1250 | Revert, or deploy `-c relayEnabled=false` (D45); trail and bucket are retained |
| 5 | Runbook, ADR, architecture docs | PR 5 | 500-700 | Docs only |
| L | Live deploy and first live verification, operator-run, evidence in `docs/evidence/` | not a PR (evidence PR after) | 150-300 | D45 rollback list |

Dependencies are linear for correctness (1 -> 2 -> 4, 1 -> 3, 4 -> 5); PR 3
may be authored before PR 2 but must merge after PR 1. PR 4 needs PR 1-3
merged because the Lambda asset is `src/rain_alert`. Phase L needs PR 1-5
merged.

When chained PRs apply, the `chained-pr` skill is required: each child PR
carries a dependency diagram with the current PR marked, start, end,
prior dependencies, follow-ups and out-of-scope items.

---

## Phase 0: Verify AWS and platform behaviour before the code that depends on it

Design "To Verify at Apply" items 1-10. The design phase could not reach
the AWS documentation tools; nothing below is trusted from memory. Use
`mcp__aws-mcp__search_documentation` / `read_documentation` (project
preference over `awsknowledge`), `awspricing` for pricing, and
`awsiac`/Context7 for CDK. Record each answer (URL, date, verbatim
sentence, and the design line it confirms or changes) in apply-progress.
If an answer contradicts the design, **stop and report**, do not code
around it. Items that cannot be settled by documentation are carried to
Phase L as `[Owner]` tasks, named in the "Carried to" column.

**Phase 0 result (2026-10-02).** Source: Engram observation 2706 (topic `sdd/senamhi-s3-relay/phase0-verification`), plus the orchestrator's Phase 0 summary. 2706 records the details for V.3, V.7 and V.10. For the items marked CONFIRMED, it records no per-item quote. Three CONTRADICTED items were amended in design.md (D40, D46, D47) on 2026-10-02 before any dependent code. Verdicts:

| Item | Verdict | Effect |
|---|---|---|
| V.1 | CONFIRMED | D39 unchanged; L.6 still observes a real `NoSuchKey` |
| V.2 | PARTIAL | Contract as designed; boto3 `credential_process` plus `security find-generic-password -w` under launchd is verified live in **L.3** |
| V.3 | CONTRADICTED, amended | A 21600 s alarm is a sliding window; wall-clock windows exist only for 60/300/3600/86400/604800 s. D40 rewritten; config 21600/2/2/Maximum/breaching kept; evaluation cadence verified live in **L.9** |
| V.4 | PARTIAL | Single-line JSON record already implemented in PR 2 (2.15/2.16/2.17a); matching against a real event is **L.5** |
| V.5 | CONFIRMED | Reader-side cap is the real guard (D37) |
| V.6 | CONFIRMED | Live page is UTF-8 (header `charset=UTF-8`, `<meta charset="utf-8">`), strict decode OK, 523,653 bytes vs 4 MiB cap — `docs/evidence/2026-10-02-senamhi-unreachable-from-aws.md` §9 (checked with curl, not HttpxHtmlFetcher) |
| V.7 | CONTRADICTED, amended | `StartInterval` misses firings during sleep; `StartCalendarInterval` coalesces them into one run on wake (`man launchd.plist`). New D47; 3.11 rewritten; wake behaviour observed in **L.4** |
| V.8 | CONFIRMED | Relay read worst case 16 s, D31 relation holds |
| V.9 | CONFIRMED | Cost figure stands for the ADR |
| V.10 | CONTRADICTED (a), amended; PARTIAL (e, f) | (a) The `Trail` L2 emits classic selectors only, and CFN forbids mixing them with advanced ones. D46 rewritten; 4.18/4.19 rewritten. `describe-trails` in us-east-2 shows zero trails. (e) denied `PutObject` logged and (f) `versionId` in the event go to **L.7** |
| V.11 | CONFIRMED | CDK prop names as used in 4.x |

| Task | Item | Must land before | Carried to |
|---|---|---|---|
| - [x] V.1 [Docs] `GetObject` returns 403 vs 404 without `s3:ListBucket`; whether an `s3:prefix` condition satisfies the implicit list check (API_GetObject permissions section; repost article cited in D39) | design #1 | task 2.9 (reader mapping) and 4.5 (grant) | L.6 observes real `NoSuchKey` |
| - [x] V.2 [Docs] `credential_process` JSON contract (`Version`, `AccessKeyId`, `SecretAccessKey`) and that boto3 honours it from `~/.aws/config`; `security find-generic-password -w` output and `-T` pre-authorization semantics | design #2 | 5.x runbook text | L.3 observes it under launchd |
| - [x] V.3 [Docs] CloudWatch alarm period alignment for a 6 h period; `treatMissingData` behaviour with a metric-filter metric that has no `defaultValue`; confirm the 05/11/17/23 UTC cron vs 6 h UTC boundaries | design #3 | task 4.13 | L.9 observes a transition |
| - [x] V.4 [Docs] Python Lambda runtime log splitting of multi-line `print` output, and JSON metric-filter matching against single-line records under the stack's log format | design #4 | task 2.13 (`on_read` record shape) | L.5 matches a real event |
| - [x] V.5 [Docs] No IAM condition key bounds `PutObject` size (so the reader-side cap is the real guard) | design #5 | task 2.9 | none |
| - [x] V.6 [Mac] Read-only check of the live SENAMHI response charset: `uv run python -c` fetch through `HttpxHtmlFetcher`, print `response.encoding`, declared `Content-Type`, byte length; compare with the 4 MiB cap (fixture README says ~512 KB). Plain GET, no AWS | design #6 | task 3.5 (producer body) | recorded in `docs/evidence/` |
| - [x] V.7 [Docs] launchd `StartInterval` across sleep (missed runs coalesce on wake?) | design #7 | task 3.11 (plist) | L.4 observes sleep/wake |
| - [x] V.8 Compute D31 headroom: relay read worst case (2 attempts x (3 s + 5 s)) vs `cycle_headroom_seconds` 60 against the Lambda `Timeout` 120; confirm Relation 2 still holds | design #8 | task 4.3 | none |
| - [x] V.9 [Docs] S3 Standard pricing for the 90-day, ~1.1 GB noncurrent-version estimate (awspricing) | design #9 | ADR (5.5) | none |
| - [x] V.10 [Docs] D46 facts: CDK `cloudtrail.Trail` API for management events off plus an advanced event selector (or `addS3EventSelector` with `includeManagementEvents: false`) against the pinned `aws-cdk-lib`; CloudTrail data-event pricing; that a denied `PutObject` is logged; that the `PutObject` event carries `versionId`. Also `aws cloudtrail describe-trails` read-only against `ferrenafe` to record (not reuse) any existing account trail | design #10 | tasks 4.18-4.19 | L.7 observes a real event |
| - [x] V.11 [Docs] Confirm `aws-cdk-lib` `iam.User`, `s3.Bucket` lifecycle (`noncurrentVersionExpiration`, `abortIncompleteMultipartUploadAfter`) and `logs.FilterPattern.stringValue` prop names against the installed `infra/node_modules/aws-cdk-lib` source, as scheduled-cycle task 3.1 did | CDK props | tasks 4.5-4.13 | none |

---

## Phase 1: Domain reasons, contract file and `RelayWarningProvider` (PR 1, offline, inert)

Satisfies: `senamhi-relay` (freshness, three reasons, parse pass-through),
`weather-sources` (reasons named), `source-outage-notices` (relay reason,
VersionId, skew, resident text byte-identical). Design D37, D38, D40
(record shape only).

### Contract file (D37)

- [x] 1.1 [RED] `tests/unit/adapters/test_senamhi_relay_contract.py` -- loads `contracts/senamhi-relay.json` and asserts `object_key`, `max_age_seconds`, `max_object_bytes`, `skew_tolerance_seconds` equal the Python constants; asserts the key's second segment equals `region_slug(REGION)`. Expected failure: `FileNotFoundError` on the contract file (then `ImportError` for the constants), not an assertion typo.
- [x] 1.2 [GREEN] Create `contracts/senamhi-relay.json` (D37 values: `senamhi/lambayeque/latest.html`, 10800, 4194304, 900) and constants `OBJECT_KEY`, `MAX_AGE`, `MAX_OBJECT_BYTES`, `SKEW_TOLERANCE` in `adapters/senamhi_relay.py`. Mutation proof: change one constant, record the test red, revert. Verify: `uv run pytest tests/unit/adapters/test_senamhi_relay_contract.py`.

### Domain reasons (D38)

- [x] 1.3 [RED] `tests/unit/domain/test_values.py` (or the existing reasons test) -- `UnavailableReason.STALE_RELAY/RELAY_MISSING/RELAY_UNREADABLE` exist with values `stale_relay`, `relay_missing`, `relay_unreadable`; `fallback_reason_for` stays total over every member (parametrized over `UnavailableReason`). Expected failure: `AttributeError` on the new members.
- [x] 1.4 [GREEN] Add the three members to `domain/values.py`. Verify: `uv run pytest tests/unit/domain` plus `uv run mypy`. Confirm no `contracts/` or `web/` file enumerates reasons (design says checked; re-check with `git grep`).

### Pure helpers (D38, design tests 1 and 2)

- [x] 1.5 [RED] `test_senamhi_relay.py` `is_stale`/`relay_age` table: 2h59m59s fresh, **exactly 3h stale**, 4h stale; future `last_modified` clamps age to 0 and `fetched_at = min(last_modified, now)`. Expected failure: `ImportError` (`is_stale` absent).
- [x] 1.6 [GREEN] Implement `relay_age`, `is_stale(age, max_age)` with `>=`. Mutation proof: swap `>=` for `>` and record the "exactly 3h" row red; revert.
- [x] 1.7 [RED] `skew(claimed, last_modified)` table: within 15 min unflagged, over 15 min flagged with signed minutes; absent claim gives no note. Expected failure: `ImportError`.
- [x] 1.8 [GREEN] Implement `skew` and the skew note text.
- [x] 1.9 [RED] `relay_detail(...)`: VersionId first; at most 160 chars after `_failure_lines` sanitising; no `version=` segment when VersionId is `None`; truncation drops the tail not the VersionId. Expected failure: `ImportError`.
- [x] 1.10 [GREEN] Implement `relay_detail`. Verify: `uv run pytest tests/unit/adapters/test_senamhi_relay.py -k "stale or skew or detail"`.

### Test support

- [x] 1.11 Add `FakeRelayReader` to `tests/support/fakes.py` (returns a programmed `RelayObject` or raises a programmed `FetchError`). Hand-written, no `unittest.mock`. Covered by the provider tests below (a fake with no test is a defect in this project, per the `FileHtmlFetcher` precedent): add one self-test that it returns and raises as programmed.

### `RelayWarningProvider` (D38, design test 5)

- [x] 1.12 [RED] Fresh `tests/fixtures/senamhi/warnings_table.html` through `FakeRelayReader` yields `Available` equal to the scraper's result on the same fixture except `fetched_at == last_modified` and notes. Expected failure: `ImportError` (`RelayWarningProvider` absent).
- [x] 1.13 [GREEN] Implement `RelayObject`, `RelayObjectReader` Protocol, `RelayReadRecord`, `RelayWarningProvider.fetch_current_warnings` steps 2-4. The module imports nothing from `entrypoints`.
- [x] 1.14 [RED] Staleness is never a calm: a 4 h old *calm* page returns `Unavailable(STALE_RELAY)`, not `Available(())`, and `parse_warnings_page` is **not called** (assert via a recording parser seam or a poisoned page that would raise if parsed). Expected failure: returns `Available`, because freshness is not checked yet (write this test before 1.13's freshness branch lands, or temporarily remove the branch and record red).
- [x] 1.15 [GREEN] Freshness branch before parse; `now` is the injected clock, never `datetime.now()`.
- [x] 1.16 [RED] `broken_structure.html` fresh object gives the scraper's **exact** reason and detail (compare against `SenamhiWarningScraper` on the same fixture); a non-`FetchError` parser exception becomes `TRANSPORT_ERROR`. Expected failure: wrong reason or exception escapes.
- [x] 1.17 [GREEN] Parse-failure pass-through per D38 step 3.
- [x] 1.18 [RED] Reader raising `FetchError(RELAY_MISSING)` / `FetchError(RELAY_UNREADABLE)` maps through unchanged; reader raising an arbitrary `RuntimeError` becomes `RELAY_UNREADABLE`; **nothing escapes** `fetch_current_warnings`. Expected failure: the arbitrary exception propagates.
- [x] 1.19 [GREEN] Step 1 mapping.
- [x] 1.20 [RED] `on_read` is called exactly once per call, for success, stale, missing, unreadable and parse-failure outcomes, with a `RelayReadRecord` carrying `degraded` (1 for every `Unavailable`, including a fresh-object parse failure, D40), `reason`, `version_id`, `last_modified`, `age_seconds`, `skew_seconds`. Expected failure: callback never invoked.
- [x] 1.21 [GREEN] Emit the record on every path.

### Operator notice and resident text (design test 7)

- [x] 1.22 [RED] `tests/unit/domain/test_outage.py` -- the notice for `STALE_RELAY` names `stale_relay` and the VersionId, within 160 chars; `RELAY_UNREADABLE` has its own scenario; changing reason between cycles (`relay_missing` -> `relay_unreadable`) does **not** re-notify (same outage). Expected failure: notice lacks the relay detail, or re-notifies.
- [x] 1.23 [RED] Resident message rendered for `Unavailable(STALE_RELAY)` is byte-identical to `Unavailable(TIMEOUT)` (golden comparison, not a substring check). This passes at once, because `snapshot.py` renders only `assessment.reasons`; record that honestly in apply-progress as a characterisation test, and prove its teeth by temporarily threading `reason.value` into the template and recording red.
- [x] 1.24 [GREEN] Make 1.22 pass with the smallest `outage.py` change, if any is needed (design claims generic rendering; verify, do not assume).

### Close-out

- [x] 1.25 Extend `tests/architecture/test_layer_boundaries.py`: `adapters/senamhi_relay.py` imports nothing from `entrypoints`; include a planted-violation case proving the scan can fail.
- [x] 1.26 PR-1 gate: `uv run pytest`; `uv run --directory agent pytest`; `uv run ruff check`; `uv run ruff format --check`; `uv run mypy`; `cd infra && npm test` (unchanged, must stay green).
- [x] 1.27 **Security review** (fresh context) on the PR-1 branch diff before opening the PR. Scope: no AWS account id, ARN or secret; `relay_detail` cannot leak page content into the operator notice beyond the VersionId and numbers; `tests/hygiene` passes after `git add`.

---

## Phase 2: `S3RelayReader`, wiring and the `on_read` record (PR 2, offline, inert until env var set)

Satisfies: `senamhi-relay` (reader contract, size cap, error mapping, never
direct scrape), `weather-sources` (relay selected by
`RAIN_ALERT_RELAY_BUCKET`). Design D39, D40 (record), D43.

### `FakeS3Client` (D44)

- [x] 2.1 [RED] `tests/unit/support/test_fake_s3.py` (or inside the reader tests) -- `FakeS3Client.get_object` returns `Body` (a `TrackingBody` recording `read`/`close`), `ContentLength`, `ContentType`, `LastModified`, `VersionId`, `Metadata`; programmed failures raise **real** `botocore.exceptions.ClientError` / `EndpointConnectionError` / `ReadTimeoutError`; `put_object` records kwargs. Expected failure: `ModuleNotFoundError`.
- [x] 2.2 [GREEN] Create `tests/support/fake_s3.py`. Verify: `uv run pytest tests/unit/support`.

### `S3RelayReader` -- one RED/GREEN pair per row of D39's table

- [x] 2.3 [RED] Bounded `Config` pinned: `connect_timeout=3`, `read_timeout=5`, `retries={"total_max_attempts": 2}` (copy the `s3_snapshot_publisher` test's `boto3.client` monkeypatch); client built lazily on first `read()`. Expected failure: `ModuleNotFoundError` (`adapters/s3_relay_reader.py`).
- [x] 2.4 [GREEN] Skeleton with the lazy client and the bounded `Config`.
- [x] 2.5 [RED] Happy path: returns `RelayObject` with text decoded UTF-8, `last_modified`, `version_id`, `claimed_fetched_at` from metadata; body closed in `finally`. Expected failure: `NotImplementedError`/missing `read`.
- [x] 2.6 [GREEN] Implement `read` with `GetObject` on `OBJECT_KEY` only (the key is **not** an env override, D37).
- [x] 2.7 [RED] `ClientError` `NoSuchKey` -> `FetchError(RELAY_MISSING)`; `AccessDenied`, `403`, an arbitrary code, `BotoCoreError` (`EndpointConnectionError`, `ReadTimeoutError`) -> `RELAY_UNREADABLE` with the code in the detail. Parametrized, asserting by `ClientError.response["Error"]["Code"]` (D39). Expected failure: exceptions propagate raw.
- [x] 2.8 [GREEN] Error mapping.
- [x] 2.9 [RED] Size cap: `ContentLength > MAX_OBJECT_BYTES` -> `RELAY_UNREADABLE` with `TrackingBody.read` **never called** and body closed; `ContentLength` lying (small) with a body over cap+1 bytes read -> `RELAY_UNREADABLE`. Expected failure: the oversize body is read. Depends on V.1, V.5.
- [x] 2.10 [GREEN] Check `ContentLength` before reading; read at most `cap + 1`.
- [x] 2.11 [RED] Integrity and charset: `Content-Type` charset not utf-8 -> UNREADABLE; invalid UTF-8 bytes -> UNREADABLE (strict decode); `sha256` metadata absent or mismatched -> UNREADABLE; empty body -> UNREADABLE. Expected failure: bad objects are returned as `RelayObject`.
- [x] 2.12 [GREEN] Charset check, strict decode, sha256 verification. Mutation proof: switch to `errors="replace"` and record the invalid-bytes row red; revert.

### Wiring (D43, design test 6)

- [x] 2.13 [RED] `tests/unit/entrypoints/test_wiring.py` -- `select_warning_provider({})` returns `SenamhiWarningScraper`; `{"RAIN_ALERT_RELAY_BUCKET": "b"}` returns `RelayWarningProvider`; `{"RAIN_ALERT_RELAY_BUCKET": ""}` returns a provider whose `fetch_current_warnings` yields `Unavailable(RELAY_UNREADABLE)` ("relay bucket not configured") and **no `HtmlFetcher` exists in the graph** (assert by walking the object graph or by a fetcher fake that fails the test if constructed). Expected failure: `ImportError` (`select_warning_provider` absent).
- [x] 2.14 [GREEN] Implement presence-not-truthiness selection; `build_cloud_deps(env: Mapping[str, str] | None = None)` defaulting to `os.environ`; the handler stays unchanged. `build_local_deps` untouched.
- [x] 2.15 [RED] `on_read` in cloud wiring prints exactly **one** compact line (`separators=(",", ":")`, no newline inside) with `"event":"senamhi_relay"` and `"degraded"`, via `capsys`. Expected failure: nothing printed. Shape confirmed by V.4.
- [x] 2.16 [GREEN] Cloud `on_read` writer.
- [x] 2.17 [RED] Characterisation: existing `test_build_cloud_deps_constructs_only_console_notifier` and the architecture boundary tests still pass; `lambda_handler` never imports `entrypoints.cli` (unchanged). Extend the architecture test: `adapters/s3_relay_reader.py` imports nothing from `entrypoints`, with a planted-violation case.

### Close-out

- [x] 2.17a [RED/GREEN] The structured `on_read` log writer JSON-encodes the whole record (never string-formats it) and sanitizes + caps `version_id` before encoding (log-injection guard). Test: a `version_id` containing a newline and a forged `{"event":...}` fragment still prints exactly one line, and the decoded `version_id` has no newline and is length-capped.
- [x] 2.17b [RED/GREEN] The S3 reader enforces `MAX_OBJECT_BYTES` before reading or decoding: check `ContentLength` first, then a bounded read of at most `cap + 1` bytes. Tests: an oversized `ContentLength` -> `RELAY_UNREADABLE` with `TrackingBody.read` never called; a lying small `ContentLength` with a body larger than the cap -> `RELAY_UNREADABLE` (extends 2.9/2.10).

- [x] 2.18 PR-2 gate: full project gate (as 1.26).
- [x] 2.19 **Security review** (fresh context) on the PR-2 branch diff. Scope: exceptions never carry object content into logs; the env-var-empty path cannot reach a direct scrape; no account id in fixtures. Note: review fixes applied (judgment-day round 1); security review still open.

---

## Phase 3: Producer `rain-alert-relay-push` and launchd template (PR 3, offline)

Satisfies: `senamhi-relay` (producer parse gate, no overwrite on failure,
UTF-8 body, headers, metadata, size cap). Design D37, D42. Needs V.6 and
V.7 recorded.

- [x] 3.1 [RED] `tests/unit/entrypoints/test_relay_push.py` -- `RAIN_ALERT_RELAY_BUCKET` absent or empty -> exit **1**, zero `put_object` calls. Expected failure: `ModuleNotFoundError`.
- [x] 3.2 [GREEN] `entrypoints/relay_push.py::main(argv=None, *, fetcher=None, client=None, now=None) -> int`; `__main__` is one call.
- [x] 3.3 [RED] Fetch failure (`FetchError` from a `FakeHtmlFetcher`) -> exit **2**, zero PUTs. Parse failure (`broken_structure.html`) -> exit **2**, zero PUTs (the gate). Expected failure: a PUT is attempted or the exception propagates.
- [x] 3.4 [GREEN] Steps 2 and 3 of D42 using `HttpxHtmlFetcher`, `warnings_url_for(REGION)`, `parse_warnings_page`.
- [x] 3.5 [RED] Body over `MAX_OBJECT_BYTES` -> exit **3**, zero PUTs. Success -> exactly **one** `put_object` with `Key == OBJECT_KEY`, `Body == html.encode("utf-8")`, `ContentType == "text/html; charset=utf-8"`, `ChecksumAlgorithm == "SHA256"`, `Metadata` carrying `fetched-at` (ISO-8601 UTC from the injected `now`), `sha256` (hex of the body), `producer`; exit **0**; one summary line (version id, bytes, rows_seen). Expected failure: the PUT headers are absent or wrong. The sha256 value is asserted against an independently computed digest, not the production helper.
- [x] 3.6 [GREEN] Steps 4-6 of D42.
- [x] 3.7 [RED] `put_object` raising `ClientError` or `BotoCoreError` -> exit **4**. Bounded `Config` pinned as in 2.3.
- [x] 3.8 [GREEN] Error exit and bounded client.
- [x] 3.9 [RED] Producer-reader round trip: bytes written by `main` through `FakeS3Client` are accepted by `S3RelayReader` over the same fake and parsed by `RelayWarningProvider` into the same `Available` data as the scraper. This is the test that pins "the Lambda parses byte-for-byte what the gate accepted" (D37 charset argument). Expected failure: reader rejects the producer's metadata or encoding if either side drifts.
- [x] 3.10 [GREEN] Fix any drift the round trip reveals (expected: none).
- [x] 3.11 Register `rain-alert-relay-push` in `pyproject.toml [project.scripts]`; create `ops/launchd/pe.ferrenafe.relay-push.plist` template (D47: `StartCalendarInterval` as the single dict `{Minute: 0}`, `RunAtLoad true`, `uv run --directory <repo> rain-alert-relay-push`, `AWS_PROFILE=ferrenafe-relay`, `AWS_REGION=us-east-2`, `RAIN_ALERT_RELAY_BUCKET=<bucket>`, logs under `~/Library/Logs/ferrenafe-relay/push.log`). **RED:** a hygiene-style test parses the plist with `plistlib` and asserts: `StartCalendarInterval == {"Minute": 0}` exactly (no `Hour`/`Day`/`Weekday`/`Month` keys, so it is hourly); **`StartInterval` is absent** (the man page says both are evaluated independently, so both would double-fire, and `StartInterval` misses runs during sleep); `RunAtLoad is True`; the `ProgramArguments`, `EnvironmentVariables` and log-path keys above; every placeholder is an angle-bracket token; no secret, account id or ARN. Expected failure: file absent. Mutation proof: swap in `StartInterval 3600` and record red; revert.
- [x] 3.12 PR-3 gate: full project gate (as 1.26), plus `uv run rain-alert-relay-push --help` exits 0 offline (entry point resolves).
- [x] 3.13 *(review fixes applied: gate-exception exit 2, in-flight cap and deadline, `--frozen`, optional ExpectedBucketOwner, design D42/D47 amended; awaiting confirmation)* **Security review** (fresh context) on the PR-3 branch diff. Scope: no credential, key or Keychain material in code, plist or fixtures; producer never logs the body; failed run provably leaves the old object untouched (tests 3.1-3.7).

---

## Phase 4: Infra: bucket, grants, producer identity, alarm, audit trail (PR 4, synth-only)

Satisfies: `senamhi-relay` (grants, bucket posture, no AccessKey resource,
alarm, CloudTrail requirement). Design D36, D40, D41, D45, D46. **No task
runs `cdk deploy`, `cdk bootstrap` or any live AWS call.** Every assertion
is against a synthesized `Template` in `infra/test/scheduled-cycle-stack.test.ts`.
Skills: `aws-cdk`, `awsiac` `cdk_best_practices`. Needs V.1, V.3, V.8,
V.10, V.11 recorded.

### Bucket and grants (D41, design test 9)

- [x] 4.1 [RED] `"relay bucket"` -- `BlockPublicAccess` all four true; versioning enabled; SSE `AES256`; `OwnershipControls` `BucketOwnerEnforced`; lifecycle `NoncurrentVersionExpiration` 90 days and `AbortIncompleteMultipartUpload` 1 day; `DeletionPolicy: Retain`/`UpdateReplacePolicy: Retain`; SSL-deny bucket policy statement; **no `BucketName`**. Expected failure: no relay bucket resource in the template.
- [x] 4.2 [GREEN] Add the bucket to `ScheduledCycleStack`.
- [x] 4.3 [RED] `"lambda relay grants"` -- the Lambda role's relay statements are exactly `s3:GetObject` on `<bucket>/senamhi/lambayeque/latest.html` and `s3:ListBucket` on the bucket ARN, and nothing else touches the relay bucket (no `s3:GetObjectVersion`, no `s3:*`, no Put). The object key is **read from `contracts/senamhi-relay.json`**, not retyped (the D32 prefix lesson). Expected failure: statements absent. Re-check V.8 headroom relation 2 unchanged.
- [x] 4.4 [GREEN] `bucket.grantRead`-style grant replaced by two explicit `PolicyStatement`s so the action set is exact.
- [x] 4.5 [RED] `"relay writer and producer user"` -- a `ManagedPolicy` `RelayWriter` with `s3:PutObject` only, on that one key; an `iam.User` `ferrenafe-relay-producer` with exactly one attached policy (`RelayWriter`) and no inline policy; `resourceCountIs('AWS::IAM::AccessKey', 0)`. Expected failure: no user or policy.
- [x] 4.6 [GREEN] Add policy and user. Mutation proof for the zero-AccessKey assertion: temporarily add `new iam.AccessKey(...)` and record the test red; revert.
- [x] 4.7 [RED] `"relay env and outputs"` -- `RAIN_ALERT_RELAY_BUCKET` equals the bucket ref; outputs `RelayBucketName`, `RelayProducerUserName`, `RelayObjectKey` exist; **no output or resource property contains an ARN** (account-id rule). Expected failure: env var and outputs absent.
- [x] 4.8 [GREEN] Env var and outputs.

### Alarm (D40, design test 9)

- [x] 4.9 [RED] `"relay filter"` -- metric filter on the Lambda log group with `FilterPattern` `{ $.event = "senamhi_relay" }`, metric namespace `FerrenafeRainAlert`, name `SenamhiRelayDegraded`, `MetricValue` `$.degraded`, **no `DefaultValue`**. Expected failure: no such filter.
- [x] 4.10 [GREEN] Add the filter.
- [x] 4.11 [RED] `"relay alarm"` -- `Statistic: Maximum`, `Period: 21600`, `EvaluationPeriods: 2`, `DatapointsToAlarm: 2`, `TreatMissingData: breaching`, `AlarmActions` includes the existing `AlarmsTopic`. Expected failure: no alarm. (D40, amended: 21600 s is a **sliding** window. The 2/2 plus breaching semantics do not depend on alignment, because any 12 h span holds at least one cycle in normal operation. Do not add a comment or ADR claim that windows align with the 0/6/12/18 America/Lima schedule.)
- [x] 4.12 [GREEN] Add the alarm. The code comment states the sliding-window argument from D40 in one or two lines, not an alignment claim.
- [x] 4.13 Mutation proofs: change `treatMissingData` to `notBreaching` and `datapointsToAlarm` to 1 in turn; record each assertion red; revert. Also assert the metric filter has **no** `DefaultValue` (a default of 0 would fill the missing windows and defeat `breaching`), with a mutation proof. State plainly in apply-progress that a template test proves configuration, not window alignment, evaluation cadence or a live transition (carried to L.9).

### `relayEnabled` rollback switch (D41, D45)

- [x] 4.14 [RED] `"relayEnabled=false"` -- with `-c relayEnabled=false` the Lambda has **no** `RAIN_ALERT_RELAY_BUCKET`, the relay filter's alarm is absent, while the bucket, user, policy **and (after 4.20) the trail** remain; default is enabled; parsing mirrors `scheduleEnabled` (the string `'false'` only). Expected failure: the env var is still present.
- [x] 4.15 [GREEN] Context parsing and conditional env var/alarm.
- [x] 4.16 Update `infra/bin/infra.ts` only if context plumbing needs it; extend `infra/test/app.test.ts` for `relayEnabled` passthrough if touched.
- [x] 4.17 PR-4a gate: `cd infra && npm test`; `uv run pytest tests/hygiene` after `git add`; full Python gate unchanged.

**PR-4a review fixes applied (2026-10-02):** (1) `relayEnabled` now ships `"false"` in `infra/cdk.json` and is parsed strictly (`parseRelayEnabled`: string or boolean accepted, absent disables, other values throw); (2) fixed `managedPolicyName` removed, `RelayWriterPolicyName` output added; (3) bucket policy denies TLS below 1.2; (4) D45 and task 5.1/L.0 updated for the flip procedure, creation-time ALARM and key revocation.

### Audit trail (D46, design test 10) -- may be PR 4b

- [x] 4.18 [RED] `"relay audit trail"` (D46, amended to classic selectors) -- exactly one `AWS::CloudTrail::Trail`; `IsMultiRegionTrail: false`; `IncludeGlobalServiceEvents: false` (both explicit, since the L2 defaults are `true`); `EnableLogFileValidation: true`; `EventSelectors` has **exactly one** entry equal to `{ReadWriteType: "WriteOnly", IncludeManagementEvents: false, DataResources: [{Type: "AWS::S3::Object", Values: [<relay bucket ARN> + "/"]}]}` (match the `Fn::Join`/`Fn::GetAtt` on the relay bucket's logical id, not a literal ARN); **no `AdvancedEventSelectors` key**; no management selector (no second `EventSelectors` entry); no `CloudWatchLogsLogGroupArn`/`CloudWatchLogsRoleArn`; no `KMSKeyId`. Expected failure: no trail.
- [x] 4.19 [GREEN] Add the trail with the L2: `new cloudtrail.Trail(this, ..., { bucket: logBucket, isMultiRegionTrail: false, includeGlobalServiceEvents: false, enableFileValidation: true, sendToCloudWatchLogs: false, managementEvents: cloudtrail.ReadWriteType.NONE })` and `trail.addS3EventSelector([{ bucket: relayBucket }], { readWriteType: cloudtrail.ReadWriteType.WRITE_ONLY, includeManagementEvents: false })`. **No `CfnTrail` escape hatch** (D46: no requirement needs advanced selectors, and CFN forbids mixing the two kinds).
- [x] 4.20 [RED] `"relay audit log bucket"` -- own bucket: BLOCK_ALL, SSL-deny, `AES256`, `BucketOwnerEnforced`, lifecycle expiration 400 days, `Retain`, no `BucketName`, CloudTrail bucket-policy statement present, **distinct** from the relay bucket. Expected failure: no second bucket.
- [x] 4.21 [GREEN] Add the log bucket.
- [x] 4.22 [RED] The trail and log bucket exist with `relayEnabled=false` (audit record outlives a rollback). Expected failure: they are missing if wrongly nested under the switch.
- [x] 4.23 [GREEN] Keep them outside the conditional. Mutation proofs: change `readWriteType` to `ALL` and record red (it would bill and log every Lambda read); change `managementEvents` to `ALL` (adds a management selector) and record red; replace the bucket selector with `logAllS3DataEvents` (account-wide `arn:aws:s3`) and record red; revert.
- [x] 4.24 PR-4 gate: `cd infra && npm test`; `uv run pytest`; `uv run --directory agent pytest`; `uv run ruff check`; `uv run ruff format --check`; `uv run mypy`; `uv run pytest tests/hygiene` after `git add`; `npx cdk synth ScheduledCycleStack -c ...` with fixture contexts offline succeeds (selects by construct id, as scheduled-cycle 3.25 recorded).
- [x] 4.25 **Security review** (fresh context) on the PR-4 (and 4a/4b) diff. Scope: every grant resource-scoped, no wildcard; no `AWS::IAM::AccessKey`; no account id or ARN in outputs, fixtures or context; trail selector is write-only and bucket-scoped; both buckets private.

---

## Phase 5: Runbook, ADR, architecture docs (PR 5)

- [x] 5.1 `docs/runbooks/senamhi-relay.md` -- follows `docs/runbooks/README.md` conventions and the shape of `scheduled-cycle-deploy.md`. Contents: deploy order (D45 steps 1-5); create the IAM access key and store it in the Keychain as the `credential_process` JSON with `-T /usr/bin/security`; `~/.aws/config` profile `ferrenafe-relay`; LaunchAgent (not Daemon) install with `launchctl bootstrap gui/$UID` and `bootout`; **schedule and sleep behaviour (D47)**: the push runs hourly at `:00` via `StartCalendarInterval`; after sleep, missed firings coalesce into one push on wake; if that push fails because the network is not up yet (exit 2, old object kept), the next `:00` push recovers, so expect fresh within 1 h of wake; a Mac off, logged out or asleep for more than about 3 h makes cycles read `STALE_RELAY`, and two such cycles raise the alarm (expected, not a fault of the relay; the R4 always-on producer is the remedy); how to read `push.log` to confirm the wake run; manual push; rotation every 90 days (D36 steps); revocation on suspected leak (Inactive, `list-object-versions`, CloudTrail events, `copy-object` restore); retirement on Roles Anywhere migration; rollback list (D45), including that rollback does NOT revoke the producer's access key, so deactivate/delete it; the `infra/cdk.json` `relayEnabled` flip procedure (start producer, confirm a fresh object, commit `"true"`, deploy, manually invoke the cycle Lambda once); the EXPECTED creation-time ALARM (`TreatMissingData=BREACHING` on a new alarm, cleared by the manual invoke, repeats on every re-enable) and that a producer absent for about 12 h or more after enabling alarms legitimately; reading CloudTrail write events; `<account>` and `<bucket>` placeholders only.
- [x] 5.2 State in the runbook that an always-on producer authenticating with IAM Roles Anywhere is required before 2026-12-01 (R4), with the dated link to the ADR.
- [x] 5.3 `docs/decisions/<next-number>-senamhi-s3-relay.md` -- follows `docs/decisions/README.md`: the choice, evidence (link `docs/evidence/2026-10-02-senamhi-unreachable-from-aws.md`), consequences, accepted risk R5, the S3 and CloudTrail cost figures from V.9/V.10. Does not reopen ADR 0001.
- [x] 5.4 `docs/architecture/adapters.md` -- document `RelayWarningProvider`, `S3RelayReader`, the producer entrypoint and the single-line `on_read` record; update the entrypoints doc for `select_warning_provider`.
- [x] 5.5 Correct stale docs found by `git grep` for "SENAMHI" reachability claims in `docs/` and `README` only where the evidence file contradicts them (edit narrowly, cite the evidence).
- [x] 5.6 PR-5 gate: `uv run pytest tests/hygiene` (account id regex, docstring citations) after `git add`; proofread that every runbook command matches `contracts/senamhi-relay.json` and the CDK outputs exactly.
- [x] 5.7 **Security review** (fresh context) on the PR-5 diff. Scope: no account id, ARN, access key id, secret, email or bucket name; Keychain commands show placeholders only. Note: review fixes applied.

---

## Phase L: Live deploy and first live verification (operator-run, not automated by sdd-apply)

These touch the real `ferrenafe` account and the producer Mac.
**sdd-apply MUST stop before this phase.** The owner runs and confirms each
step; evidence (command plus real output, account ids redacted to
`<account>`) is written to `docs/evidence/` per that directory's README,
one file per session, dated. Requires PR 1-5 merged. Use the `ferrenafe`
profile; prefer `mcp__aws-mcp__run_script` over raw CLI.

- [ ] L.0 [Owner, Mac] Producer ordering (D45): the relay ships OFF in `infra/cdk.json`. After L.1 to L.4 (producer confirmed pushing a fresh object), commit `"relayEnabled": "true"`, deploy, and manually invoke the cycle Lambda once so the alarm has a datapoint. Expect a creation-time `ALARM` email within minutes if the invoke is skipped; it repeats on every re-enable. Rollback is commit `"false"` + deploy, and also deactivate/delete the producer's access key.
- [ ] L.1 [Owner, AWS] `cdk diff ScheduledCycleStack` with the existing `-c` flags (`snapshotWriterPolicyArn`, `agentRuntimeArn`, `alarmEmail`); read the diff: bucket, user, policy, alarm, trail, log bucket, env var only. Then `cdk deploy ScheduledCycleStack ...`. Record `RelayBucketName` and `RelayProducerUserName` outputs.
- [ ] L.2 [Owner, AWS] Create the access key for `ferrenafe-relay-producer`; write the `credential_process` JSON into the Keychain with `-T /usr/bin/security`; add the `ferrenafe-relay` profile to `~/.aws/config`. The secret is never pasted into a chat or committed.
- [ ] L.3 [Owner, Mac] Run `rain-alert-relay-push` by hand: confirm exit 0, a real `VersionId` and `LastModified`, and the Keychain read **without a dialog**. Run it again from a LaunchAgent context (`launchctl kickstart`) and confirm the same unattended. Closes V.2.
- [ ] L.4 [Owner, Mac] `launchctl bootstrap gui/$UID` the agent; confirm `launchctl print gui/$UID/pe.ferrenafe.relay-push` shows the calendar trigger; put the Mac to sleep across at least two `:00` firings; on wake, observe exactly **one** coalesced push in `push.log` (and a new `VersionId`), or a logged fetch failure followed by a recovering push at the next `:00`. Confirms D47 live.
- [ ] L.5 [Owner, AWS] `aws lambda invoke` by hand; confirm one log line with `"event":"senamhi_relay","degraded":0`, the cycle's `senamhi_status: available`, and that the metric filter produced a datapoint. Closes V.4 against a real event.
- [ ] L.6 [Owner, AWS] Delete or rename nothing: instead, temporarily invoke with `RAIN_ALERT_RELAY_BUCKET` pointing at an empty scratch bucket the role cannot read, and a second time with a key-less state if feasible, to observe `RELAY_UNREADABLE`; observe a genuine `RELAY_MISSING` (`NoSuchKey`, not `AccessDenied`) before the first push or against a scratch prefix under the granted `ListBucket`. Closes V.1 live. Restore afterwards with a normal deploy, never a console edit of the env var (D34 trap).
- [ ] L.7 [Owner, AWS] CloudTrail: find the real `PutObject` data event from L.3; record principal, access key id (redacted), source IP, user agent, and whether `versionId` appears. Confirm Lambda reads are **not** recorded. Optionally attempt one denied write with another identity and confirm it is logged. Closes V.10 live.
- [ ] L.7a [Owner, AWS, read-only] Within about an hour of the first log delivery after the first deploy, run `aws cloudtrail get-trail-status --name <trail>` and confirm `LatestDeliveryError` and `LatestDigestDeliveryError` are empty and `LatestDeliveryTime` is set. This is the live proof that the log-bucket `Deny` on `aws:SourceAccount` (StringNotEquals) does not block CloudTrail delivery: AWS documents CloudTrail bucket policies conditioned on the source keys (https://docs.aws.amazon.com/awscloudtrail/latest/userguide/create-s3-bucket-policy-for-cloudtrail.html), but not explicitly for digest-file writes. If either error is set, remove the Deny and redeploy. Record the output in `docs/evidence/`.
- [ ] L.8 [Owner, AWS] Observe two consecutive scheduled cycles at full freshness (`degraded=0`), then the flow end to end into a real resident-facing run in `console` channel only (no real send beyond the existing active channel).
- [ ] L.9 [Owner, AWS] **Stale -> alarm**: stop the producer (`launchctl bootout`), wait for the object to pass 3 h, observe a cycle reporting `STALE_RELAY` and one operator notice carrying the VersionId; observe a second consecutive degraded cycle drive the `SenamhiRelayDegraded` alarm to `ALARM` and the email arrive. Restart the producer and observe recovery. Record the timestamps of the two degraded log events, the alarm's `StateUpdatedTimestamp`, and the `StateReason` text: they show the real window placement and evaluation cadence of a 21600 s alarm, which D40 states as unproven. Also confirm that no ALARM fired during L.8's healthy cycles. Closes V.3 live.
- [ ] L.10 [Owner, AWS] Run `cdk drift`/`cdk diff` and confirm no console drift; confirm one real rollback rehearsal with `-c relayEnabled=false` followed by re-enable, to prove D45 works on the live stack.
- [ ] L.11 Write `docs/evidence/2026-MM-DD-senamhi-relay-live.md` from the real output of L.1-L.10, with the V.1-V.10 answers that needed a live check; add a findings entry under `docs/blog/` for anything that surprised us, with the command that revealed it.
- [ ] L.12 Security review (fresh context) on the evidence-PR diff: no account id, ARN, key id or email.

---

## Notes on Rules Applied

- Strict TDD: every behavioural task is RED before GREEN with the expected failure named; tests whose teeth are not obvious carry a mutation-proof step.
- The default suite stays offline: only hand-written fakes (`FakeRelayReader`, `FakeS3Client`) and the existing dummy-credentials and socket-guard fixtures are used. No `unittest.mock`, no `moto` needed for this change (S3 reader behaviour is a fake over real botocore exception types).
- Hygiene runs after `git add`, never before (`git grep` does not see untracked files).
- Verification gate for every PR: `uv run pytest`; `uv run --directory agent pytest`; `uv run ruff check`; `uv run ruff format --check`; `uv run mypy`; `cd infra && npm test` (the last is mandatory from PR 4, run as a no-regression check earlier).
- A security review runs on each PR's branch diff before the PR is opened.
- No `Co-Authored-By` or AI attribution in any commit or PR.
