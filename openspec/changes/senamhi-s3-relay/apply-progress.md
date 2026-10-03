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

---

# Phase 2 / PR 2 (branch `feat/senamhi-relay-2-reader`, stacked on `feat/senamhi-relay-1-domain`)

Tasks 2.1-2.18 done (including 2.17a and 2.17b). 2.19 (security review) open. Strict TDD, RED confirmed per cycle.

## Work units

1. `FakeS3Client` + `TrackingBody` + `client_error` (real `ClientError`). RED: `ModuleNotFoundError tests.support.fake_s3` (test_fake_s3.py:12).
2. `S3RelayReader` bounded `Config` (3/5/2), lazy client, fixed key, error mapping (`NoSuchKey` -> MISSING; everything else, incl. BotoCoreError -> UNREADABLE, code sanitized and capped). RED: `ImportError s3_relay_reader` (test file line 12).
3. Size cap (`ContentLength` before read, bounded read of cap+1), charset, sha256, strict decode, empty body, mid-stream body read failure. RED: `AttributeError MAX_OBJECT_BYTES`, `DID NOT RAISE FetchError`, `UnicodeDecodeError`, raw `ReadTimeoutError`/`OSError`. Mutation proof: `errors="replace"` turned `test_invalid_utf8_is_unreadable` red; reverted.
4. Wiring: `select_warning_provider` by presence of `RAIN_ALERT_RELAY_BUCKET`; empty value -> `UnconfiguredRelayReader` (RELAY_UNREADABLE, "relay bucket not configured"), no `HtmlFetcher` built; `build_cloud_deps(env=None)`; `build_local_deps` unchanged. Architecture boundary test extended to `s3_relay_reader.py` (characterisation, green on arrival). RED: `ImportError RELAY_BUCKET_ENV_VAR`.
5. `log_relay_read` (2.15-2.17a): one `json.dumps(separators=(",",":"))` line, `version_id` sanitized and capped at 100. RED: `AttributeError log_relay_read`, nothing printed.

## Deviations

- `UnconfiguredRelayReader` lives in `adapters/s3_relay_reader.py`; `log_relay_read` lives in `entrypoints/wiring.py` (it is cloud wiring, and adapters may not import entrypoints).
- Reader requires a present `charset=utf-8` (absent charset is UNREADABLE), per design "requires charset=utf-8".
- Reader catches `BotoCoreError`/`OSError` on the body stream read, not only on `get_object`.
- `version_id` is not sanitized in the reader; the provider and the log writer sanitize at their sinks.

## Gate (PR 2)

`uv run pytest` 1282 passed, 15 deselected. `uv run --directory agent pytest` 32 passed. `ruff check` clean. `ruff format --check` 124 files formatted. `mypy` no issues in 49 files.

## Review fixes (judgment-day round 1, PR 2)

1. Whitespace bucket: `select_warning_provider` strips the value, so blank goes to `UnconfiguredRelayReader`. RED: `'relay bucket not configured' in 'S3 GetObject failed: ParamValidationError'` (test_wiring.py:306). Commit 04e9394.
2. `_parse_claim` catches `(ValueError, OverflowError)` around parse and `astimezone`. RED: `OverflowError: date value out of range` (s3_relay_reader.py:66). Commit 5e95ac9.
3. Body read maps any `Exception` to RELAY_UNREADABLE with the sanitized type name only; module docstring aligned. RED: raw `_TransportBoom` escaped. Commit 9d04829.
4. `client_error` adds `ResponseMetadata.HTTPStatusCode`. RED: `KeyError: 'ResponseMetadata'`. Commit 1246d00.
5. Doc: reader keeps `Error.Code == "NoSuchKey"` only; design D39 note and spec wording updated; `NoSuchBucket` -> RELAY_UNREADABLE test added (parametrized with fix 4).

Task 2.19 annotated "review fixes applied"; the security-review checkbox is left open.


---

# Phase 3 / PR 3 (branch feat/senamhi-relay-3-producer, stacked on feat/senamhi-relay-2-reader)

Tasks 3.1-3.12 done. 3.13 (security review) open. Strict TDD, RED confirmed per cycle. Commits: cb1f74e (producer + fakes), 9161a2e (script registration + plist).

## Work units

1. `relay_push.main(argv, *, fetcher, client, now)`. RED: `ImportError cannot import name 'relay_push'` (test_relay_push.py:13); then `TypeError main() got an unexpected keyword argument 'now'` (gate tests), then 7 failing on absent PUT / `PRODUCER_ID`, then `AttributeError ... no attribute 'boto3'` + `DID NOT RAISE SystemExit` (client/usage tests).
2. Gate: fetch failure and parse failure exit 2, oversize (measured in encoded bytes, exactly-cap passes) exit 3, S3 failure exit 4 (ClientError code only, other exceptions by type name only), no bucket exit 1; none PUT. Success: one PUT, `Content-Type: text/html; charset=utf-8`, `ChecksumAlgorithm SHA256`, Metadata `sha256`/`fetched-at` (UTC ISO-8601)/`producer`. sha256 asserted against `hashlib` independently.
3. Client: `boto3.Session(profile_name, region_name).client("s3", Config(3 s connect, 10 s read, 2 attempts))`, built only after the gate passes; `--profile`/`--region` args, otherwise the default chain. Session/profile errors map to exit 4.
4. `FakeS3Client` extended: `put_error`, and a successful PUT replaces the stored object (so get returns it), a failed PUT leaves it. RED: `TypeError put_error`, `b'' == b'<p>hi</p>'`.
5. Round trip (3.9/3.10): producer -> FakeS3Client -> `S3RelayReader` -> `RelayWarningProvider` equals `SenamhiWarningScraper` on the coast fixture (one in-force warning, non-empty). No drift found. Mutation proof: renaming the `sha256` metadata key turned 4 tests red; reverted.
6. 3.11: console script registered; `ops/launchd/pe.ferrenafe.relay-push.plist`, tested with `plistlib` (`StartCalendarInterval == {"Minute": 0}`, `StartInterval` absent, `RunAtLoad`, env, log paths, placeholders, no secrets). RED: `FileNotFoundError`. Mutation proof: injecting `StartInterval 3600` turned `test_start_interval_is_absent...` red; reverted.

## Deviations

- Plist `ProgramArguments[0]` is the placeholder `<uv>` (launchd's minimal PATH cannot resolve a bare `uv`), and the log path is `<home>/Library/Logs/ferrenafe-relay/push.log` (launchd documents no tilde expansion; `man launchd.plist` StandardOutPath). The operator also creates the log directory first. Both noted in the plist comment.
- `argparse` usage errors exit 1, not argparse's 2, because 2 means "gate refused the page".
- Summary line is `relay push: ok version=<id> bytes=<n> rows_seen=<n>` with the S3-supplied version id sanitized and capped.
- `READ_TIMEOUT` is 10 s for the producer (a 4 MiB upload), versus 5 s for the reader.

## Gate (PR 3)

`uv run pytest` 1335 passed, 15 deselected. `uv run --directory agent pytest` 32 passed. `ruff check` clean. `ruff format --check` 127 files formatted. `mypy` no issues in 50 files. `uv run rain-alert-relay-push --help` exits 0 offline; with no bucket variable it exits 1.

## PR-3 review fixes (post security review of 3.13)

Commits, each RED first:
- c6d0103 any gate exception exits 2 naming only the type. RED: `_PageDerivedError` propagated out of `main` (tests/support/fakes.py:351 raise).
- 2dcacc2 in-flight cap and overall deadline. RED: `TypeError ... unexpected keyword argument 'max_bytes'` / `'deadline_seconds'` (test_http.py), then `AttributeError ... no attribute 'FETCH_DEADLINE_SECONDS'` (test_relay_push.py). `HttpxHtmlFetcher` streams only when a bound is set; default path unchanged. Cap breach is TRANSPORT_ERROR, deadline is TIMEOUT.
- 9a42986 `uv run --frozen`, optional `RAIN_ALERT_RELAY_BUCKET_OWNER` -> `ExpectedBucketOwner`, plist comments. RED: `AttributeError ... no attribute 'BUCKET_OWNER_ENV_VAR'` plus plist assertions. Tests build the 12-digit id at runtime because the hygiene scan rejects literals.
- b069d05 unused argparse `message` deleted deliberately (`del message`).
- design.md D42/D47 amended (RunAtLoad intentional, --frozen, ExpectedBucketOwner, accepted structure-not-authenticity risk).

Gate: pytest 1349 passed, 15 deselected; agent 32 passed; ruff check clean; ruff format 127 files formatted; mypy no issues in 50 files.

---

# Phase 4a / PR 4a (feat/senamhi-relay-4a-infra, stacked on relay-3-producer): tasks 4.1-4.17 [x]

4.18+ (CloudTrail, PR 4b), 4.24 and the 4.25 security review remain open. Synth-only: no deploy, no AWS call.

## Work units (strict TDD, tests in infra/test/scheduled-cycle-stack.test.ts)

1. Bucket (4.1/4.2). RED: `Expected 1 resources of type AWS::S3::Bucket but found 0`. Private, versioned, S3-managed SSE, BucketOwnerEnforced, enforceSSL, two lifecycle rules (noncurrent 90 d; abort MPU 1 d), RETAIN, no `bucketName`.
2. Lambda grants (4.3/4.4). RED: expected 2 relay statements, found 0. Two explicit statements: `s3:GetObject` on `arnForObjects(contract.object_key)` and `s3:ListBucket` on the bucket ARN. The key is read from `contracts/senamhi-relay.json` in both the stack and the test (independent reads). No GetObjectVersion, Put or `s3:*`.
3. RelayWriter + producer user (4.5/4.6). RED: no RelayWriter managed policy found. `ManagedPolicy` (PutObject on the key only), `iam.User ferrenafe-relay-producer` with that one managed policy, `AWS::IAM::AccessKey` count 0. Mutation proof: adding `new iam.AccessKey` turned the test red; reverted.
4. Env and outputs (4.7/4.8). RED: `RAIN_ALERT_RELAY_BUCKET` undefined. Env is `{Ref: bucket}`; outputs RelayBucketName, RelayProducerUserName (Ref of the user), RelayObjectKey; no ARN in outputs.
5. Alarm (4.9-4.13). RED: no filter, no alarm, alarm count 3 vs 4. Filter `{ $.event = "senamhi_relay" }`, value `$.degraded` (field names match `wiring.py::log_relay_read`; `degraded` is 1 for stale_relay, relay_missing, relay_unreadable and a fresh-but-unparseable object), no DefaultValue. Alarm Maximum/21600/2 of 2/breaching -> existing AlarmsTopic. Mutation proofs, each red then reverted: `NOT_BREACHING`, `datapointsToAlarm: 1`, `defaultValue: 0`, plus the AccessKey one above. A template test proves configuration only, not window alignment, evaluation cadence or a live transition (carried to L.9).
6. `relayEnabled` (4.14-4.16). RED: env var present with `relayEnabled: false`. Default true; only the string 'false' disables (`bin/infra.ts`, same parsing as `scheduleEnabled`). Disabled omits env var and relay alarm; bucket, policy and user stay. `test/app.test.ts` passthrough test: red when the `relayEnabled` plumbing line was removed; reverted.

## Deviations / notes
- The existing "no s3:PutObject" test now scopes to the Lambda role statements, because RelayWriter legitimately carries PutObject. The alarm-count assertion went 3 -> 4.
- Metric filter and alarm sit inside the `relayEnabled` conditional (no alarm that would breach forever after rollback).

## PR-4a review fixes (2026-10-02)

Each RED first, `cd infra && npm test`:
- ec55786 `relayEnabled` ships `"false"` in `infra/cdk.json`; `parseRelayEnabled` in `bin/infra.ts` (string or boolean; absent disables; other values throw). RED: `Expected path: "relayEnabled"` (real cdk.json lacked the key), `Received: {"Ref": "RelayBucketA541D426"}` for absent context, and `Received function did not throw` for "yes"/1/""/null. Tests in `test/app.test.ts` ("relayEnabled context parsing (D45)").
- 88353f3 fixed `managedPolicyName` removed; `RelayWriterPolicyName` output added. RED: `Received: "RelayWriter"`. The policy is now found by its `s3:PutObject` statement.
- 7a2169b bucket policy denies `s3:TlsVersion` below `'1.2'`. RED: TLS floor test found no such statement.
- Docs: design.md D41/D45 (cdk.json switch, flip procedure, expected creation-time ALARM, 12 h legitimate alarm, rollback does not revoke the key); tasks.md 5.1, L.0 and review note (unticked).
