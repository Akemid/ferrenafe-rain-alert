# Design: SENAMHI S3 Relay

Inputs: `proposal.md` (business rules R1 to R7), `explore.md`, `specs/` (written in parallel), evidence `docs/evidence/2026-10-02-senamhi-unreachable-from-aws.md`. Decisions continue the single series: `scheduled-cycle` ended at D35, so this document starts at **D36**.

This design exceeds the 800-word SDD budget on purpose, as `scheduled-cycle/design.md` did. Nine decisions were handed here explicitly, and three of them (credential, missing-vs-denied, alarm semantics) fail silently if they are wrong.

**Verification status, stated up front.** The AWS documentation tools (`aws-mcp`) were not reachable from this phase. Every AWS behaviour below that the design depends on is cited by URL and listed under *To verify at apply*, with the check that has to run. Nothing here is load-bearing on memory without being flagged.

**Phase 0 amendment, 2026-10-02.** Phase 0 checked the To-verify list (Engram observation 2706, topic `sdd/senamhi-s3-relay/phase0-verification`; tasks.md Phase 0 has the verdicts). Three results contradicted this design, and the design now reflects them: D40 (a 6 h alarm uses a sliding window, not a wall-clock-aligned one), D42/D47 (`StartInterval` misses runs while the Mac sleeps, so the producer now uses `StartCalendarInterval`), and D46 (the CDK `Trail` L2 emits classic event selectors only). Section 5 records each item's verdict.

---

## 1. Technical Approach

The cycle gets a second `WarningProvider`. It reads one S3 object, decides whether the object is fresh using S3's own clock, and hands the text to the **unchanged** `parse_warnings_page`. Everything new is one of three things: a pure function tested with no I/O, an S3 client injected at the seam (with a hand-written fake), or a relation asserted on the synthesized template. `RunAlertCycle`, `CycleDependencies`, `ports/` and the resident template are untouched.

```
Mac (launchd, hourly)                          AWS us-east-2
rain-alert-relay-push                          ScheduledCycleStack
  HttpxHtmlFetcher -> SENAMHI page               EventBridge cron (6 h, the heartbeat, R6)
  parse_warnings_page  (gate: no parse,             |
                        no PUT)                     v
  PutObject ---------------------------------> S3 relay bucket (private, versioned)
    key   senamhi/lambayeque/latest.html            |  GetObject (one key) + ListBucket
    meta  fetched-at, sha256, producer              v
    ContentType text/html; charset=utf-8     RelayWarningProvider
                                                S3RelayReader -> RelayObject
                                                freshness (LastModified, injected now)
                                                parse_warnings_page (unchanged)
                                                on_read -> one JSON line to stdout
                                                    |                        |
                                          SourceResult -> RunAlertCycle   metric filter
                                          (Unavailable reason/detail      -> RelayDegraded alarm
                                           -> operator notice)            -> existing SNS topic
```

---

## 2. Architecture Decisions

### D36: Producer credential is a dedicated IAM user key, kept in the macOS Keychain and read through `credential_process`

**Resolved by the owner, 2026-10-02.** Option (a) for the Mac phase. **IAM Roles Anywhere is REQUIRED for the always-on producer** (proposal R4), which is itself required before 2026-12-01. This is a constraint on the next change, not a recommendation. Evidence: AWS recommends Roles Anywhere for workloads outside AWS instead of long-lived access keys, https://repost.aws/knowledge-center/manage-iam-access-keys-securely .

| Option | Tradeoff | Decision |
|---|---|---|
| (a) IAM user `ferrenafe-relay-producer`, one access key, only `RelayWriter` attached | Long-lived secret on a laptop. Blast radius is exactly R5's accepted risk: rewrite one key. No new moving parts in an unattended job | **Chosen for the Mac phase** |
| (b) IAM Roles Anywhere (X.509 trust anchor, `aws_signing_helper` as `credential_process`) | No long-lived AWS secret, and this is AWS's own recommendation for workloads outside AWS. The cost: a CA (self-signed, or AWS Private CA at a monthly fee), trust anchor, profile, role, a helper binary on the Mac, and certificate renewal. The certificate's private key is still a long-lived secret on the same laptop, unless it sits in a non-exportable store | Deferred for the Mac, **required for the always-on producer** (R4, before 2026-12-01) |
| (c) Presigned PUT issued by an AWS-side service | The producer has to authenticate to the issuer, so the credential problem comes back one hop later, plus a new endpoint to defend | Rejected |
| (d) `aws login` session | Expires. Unattended runs stop silently | Rejected (proposal) |

**How the key is read at runtime.** The secret never lives in `~/.aws/credentials`, in the plist, or in the repository:

```ini
# ~/.aws/config  (operator machine only, not committed)
[profile ferrenafe-relay]
region = us-east-2
credential_process = /usr/bin/security find-generic-password -s ferrenafe-relay-producer -w
```

The Keychain item's password is the `credential_process` JSON document (`{"Version": 1, "AccessKeyId": ..., "SecretAccessKey": ...}`). The plist sets `AWS_PROFILE=ferrenafe-relay`, and boto3 resolves the profile through its normal chain. Gotchas the runbook has to carry:
- Create the item with `-T /usr/bin/security`. Otherwise the first unattended read raises a Keychain "allow" dialog and the job hangs.
- Use a **LaunchAgent** (user session), not a LaunchDaemon. The login keychain is only available inside the user session.

**CDK creates the user and the policy, never the key.** An `AWS::IAM::AccessKey` resource would put the secret into CloudFormation outputs or state. The CDK test asserts `resourceCountIs('AWS::IAM::AccessKey', 0)`.

**Rotation and revocation runbook outline** (`docs/runbooks/senamhi-relay.md`):
1. *Rotate every 90 days*, or whenever `aws iam get-access-key-last-used` shows use you cannot account for. Create a second key (IAM allows two per user), replace the Keychain item, run one manual push, confirm a new `VersionId` and a fresh `LastModified`, set the old key to `Inactive`, wait one cycle, then delete it.
2. *Revoke on suspected leak.* Run `update-access-key --status Inactive` immediately. Then `list-object-versions` on the key for the exposure window, and use the CloudTrail `PutObject` events (D46) to see which versions came from an unexpected principal or source address. Promote a known-good version with `copy-object` under the operator profile (same procedure as D34). The cycle keeps running and turns stale-to-degraded on its own within 3 h, which is the safe direction.
3. *Retire on migration.* When the always-on producer goes live on Roles Anywhere (R4), deactivate and delete this key and remove the IAM user in the same change.

Sources (owner-supplied, 2026-10-02): https://repost.aws/knowledge-center/manage-iam-access-keys-securely . Sources not fetched in this phase, see *To verify*: IAM best practices, temporary credentials and Roles Anywhere for external workloads, https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html. Roles Anywhere, https://docs.aws.amazon.com/rolesanywhere/latest/userguide/introduction.html and `.../credential-helper.html`. `credential_process` contract, https://docs.aws.amazon.com/sdkref/latest/guide/feature-process-credentials.html. Access key rotation, https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_access-keys.html.

### D37: One contract file carries the object key, the age limit and the size cap

New file `contracts/senamhi-relay.json`, following the D31 golden-file pattern:

```json
{ "schema_version": 1,
  "object_key": "senamhi/lambayeque/latest.html",
  "max_age_seconds": 10800,
  "max_object_bytes": 4194304,
  "skew_tolerance_seconds": 900 }
```

- The Python constants are asserted equal to the file. The CDK stack reads `object_key` for both grants. If the grant and the reader disagree by one character, every cycle gets `AccessDenied` and no test notices. That is the D32 prefix lesson.
- **The key is not configurable by env.** The grant names exactly one key, so an override could only ever produce a denial. Only the bucket comes from env.
- The `lambayeque` segment equals `region_slug(REGION)`. A test pins that, so a renamed region cannot keep writing to the old key.

**Object format.** The body is the gate-validated page text, encoded as UTF-8. The headers:

| Header / metadata | Value | Purpose |
|---|---|---|
| `Content-Type` | `text/html; charset=utf-8` | The declared charset of the stored bytes |
| `x-amz-meta-fetched-at` | ISO-8601 UTC, producer clock | Informational only (R1), used for the skew check |
| `x-amz-meta-sha256` | hex SHA-256 of the body | Catches truncated or partial writes. Reader rejects a mismatch or an absent value |
| `x-amz-meta-producer` | e.g. `mac-launchd` | Attribution once a second producer exists (R4) |
| `ChecksumAlgorithm=SHA256` on PUT | S3-verified integrity in transit | No extra IAM action |

**Charset, end to end.** `HttpxHtmlFetcher` returns `response.text`, which is httpx's decode of the live response. The gate parses that string. The producer stores *that string* as UTF-8. The reader requires `charset=utf-8` and decodes strictly. Net effect: the Lambda parses byte-for-byte the string the producer's gate accepted, whatever charset SENAMHI declares. The alternative was to store SENAMHI's raw bytes plus its declared charset and decode in the Lambda. That was rejected because it lets the gate and the Lambda decode differently, and the gate would then vouch for text the Lambda never sees. The classification regexes match title words, so a decode difference could silently change a level. **Resolved by the owner, 2026-10-02:** the stored body is the gate-validated text as UTF-8; specs and proposal (R9) now say so. Archiving SENAMHI's original bytes is out of scope. The live encoding still gets verified once (*To verify* 6).

**Size cap: 4 MiB.** The live page measured about 512 KB on 2026-09-04, carrying 788 history rows since 2024 (`tests/fixtures/senamhi/README.md`). That gives 8x headroom for history growth before a false rejection, while staying small enough that a hostile upload cannot pressure the 512 MB Lambda during the BeautifulSoup parse. The cap is enforced on both sides: the producer refuses to upload above it, and the reader checks `ContentLength` *before* reading, then reads at most `cap + 1` bytes in case `ContentLength` is wrong. IAM has no PutObject size condition, so the reader-side check is the real guard (*To verify* 5).

### D38: `RelayWarningProvider` composes a reader seam with the unchanged parser

```python
# adapters/senamhi_relay.py
@dataclass(frozen=True, slots=True)
class RelayObject:
    text: str
    last_modified: datetime          # S3 server clock, authoritative (R1)
    version_id: str | None
    claimed_fetched_at: datetime | None

class RelayObjectReader(Protocol):   # adapter-internal seam, like HtmlFetcher
    def read(self) -> RelayObject: ...   # raises FetchError(reason, detail)

class RelayWarningProvider:          # satisfies ports.WarningProvider structurally
    def __init__(self, reader: RelayObjectReader, *,
                 on_read: Callable[[RelayReadRecord], None] = lambda _: None,
                 max_age: timedelta = MAX_AGE) -> None: ...
```

`fetch_current_warnings(region, now)` runs these steps. No exception escapes, as in `SenamhiWarningScraper`:
1. `reader.read()`. A `FetchError` becomes `Unavailable(reason, detail)`, and any other exception becomes `Unavailable(RELAY_UNREADABLE)`.
2. `age = now - last_modified`, where `now` is the injected cycle clock (D7), never `datetime.now()`. **`age >= 3 h` returns `STALE_RELAY` and the object is not parsed.** A future `last_modified` clamps to `fetched_at = min(last_modified, now)`, because `default_now()` truncates to whole seconds and a just-written object can be up to 1 s ahead.
3. `parse_warnings_page(text, region, now)`. A `FetchError` passes through with the **same reason and detail the scraper produces** (spec), and any other exception becomes `TRANSPORT_ERROR`, mirroring `senamhi_scraper.py:391`.
4. `Available(data, fetched_at=min(last_modified, now), notes=parse_notes(outcome) + skew_note)`.

Pure helpers, each table-tested: `relay_age(last_modified, now)`, `is_stale(age, max_age)`, `skew(claimed, last_modified)`, `relay_detail(...)`.

**New `UnavailableReason` members: `STALE_RELAY`, `RELAY_MISSING`, `RELAY_UNREADABLE`.** Resolved by the owner, 2026-10-02 (proposal R7). The operator needs to tell "the producer never pushed" (`RELAY_MISSING`) apart from "the grant or S3 is broken" (`RELAY_UNREADABLE`). Adding members is safe: `fallback_reason_for` is total, `outage.py` renders `reason.value` generically, and nothing in `contracts/` or `web/` enumerates the reasons (checked).

**Detail inside the 160-char cap.** The VersionId goes first, so truncation drops the least important tail. Example: `version=3HL4kqtJlcpXroDTDmJ.rmSpXd3dIbrHY age=4h12m limit=3h modified=2026-10-02T05:00:00Z skew=+20m` (about 100 chars). `_failure_lines` already sanitizes and caps it, and the reason value (`stale_relay`) tells the operator it is the relay, not SENAMHI, that is down. The resident text is untouched: `snapshot.py` and the template render only `assessment.reasons` (verified, `snapshot.py:109`), never `Unavailable.reason` or `detail`.

### D39: `S3RelayReader` uses the publisher's bounds, maps errors explicitly, and the Lambda gets `ListBucket`

`adapters/s3_relay_reader.py` copies `s3_snapshot_publisher.py`'s structure: a lazy client, `Config(connect_timeout=3, read_timeout=5, retries={"total_max_attempts": 2})`, and the client injected as `Any`. A retry is safe because `GetObject` is idempotent. The worst case is about 16 s, roughly 5 s above the direct scrape it replaces. *To verify* 8 re-sums D31's 60 s headroom.

| S3 outcome | Reason |
|---|---|
| `ClientError` code `NoSuchKey` | `RELAY_MISSING` |
| `ClientError` code `AccessDenied` / `403`, any other code, `BotoCoreError` (connect/read timeout, endpoint) | `RELAY_UNREADABLE`, detail names the code |
| `ContentLength > cap`, body over cap, `Content-Type` charset not utf-8, strict decode fails, sha256 absent or mismatched | `RELAY_UNREADABLE`, body closed unread where possible |
| Bucket env present but empty | `RELAY_UNREADABLE` ("relay bucket not configured") |

**Missing means `Error.Code == "NoSuchKey"`, never a bare HTTP 404.** `NoSuchBucket` is also a 404, and treating it as `RELAY_MISSING` would present a misconfigured or deleted bucket as "the producer has not published yet". A real `GetObject` returns `NoSuchKey` only when the caller holds `s3:ListBucket`; otherwise it returns `403 AccessDenied`, which maps to `RELAY_UNREADABLE`. A whitespace-only bucket variable is treated like an empty one (stripped, then `UnconfiguredRelayReader`). The `fetched-at` metadata is informational: an invalid or out-of-range value is ignored, and any exception during the body read maps to `RELAY_UNREADABLE` with only the exception type name.

The mapping uses `ClientError.response["Error"]["Code"]`, not `client.exceptions.NoSuchKey`. The typed classes are subclasses of `ClientError`, so behaviour is identical, and the fake can raise a real `ClientError` without replicating botocore's exception factory. The body is always closed in a `finally` (aws-sdk-python skill, `references/s3.md`).

**`s3:ListBucket` is granted, deliberately.** Without it, S3 answers `403 AccessDenied` for a key that does not exist, so `RELAY_MISSING` could never occur in production and every missing object would read as a broken grant. The grant is unconditional on the bucket ARN. An `s3:prefix` condition was rejected because the implicit list check made during a `GetObject` almost certainly carries no prefix context, which would bring the 403 back (*To verify* 1). The bucket holds one key that the role already knows, so listing reveals nothing. **`s3:GetObjectVersion` is not granted**: an unversioned `GetObject` returns the current version and its `VersionId` header. Reading history stays an operator action (D34's principle). Sources: https://docs.aws.amazon.com/AmazonS3/latest/API/API_GetObject.html (permissions section), and owner-supplied evidence that without `ListBucket` a missing object returns Access Denied, https://repost.aws/knowledge-center/s3-static-website-endpoint-error .

### D40: The alarm is a metric filter on a dedicated single-line record, and missing data breaches

**Why not `as_json`.** The cycle log line is `json.dumps(..., indent=2)`, which is multi-line. The Python Lambda runtime is understood to split raw `print` output on newlines into separate log events, which would make a JSON metric filter on that line unreliable. It also does not carry `Unavailable.reason`. Changing it would change the CLI `--json` contract.

**Instead:** every `RelayWarningProvider` read calls `on_read` exactly once. In cloud wiring, `on_read` prints one compact line (`separators=(",", ":")`) to stdout:

```json
{"event":"senamhi_relay","degraded":1,"reason":"stale_relay","version_id":"…","last_modified":"…","age_seconds":15120,"skew_seconds":1200}
```

`degraded` is `1` for **any** `Unavailable` from the relay provider: the three relay reasons the owner resolved on 2026-10-02 (`STALE_RELAY`, `RELAY_MISSING`, `RELAY_UNREADABLE`), and also a parse failure of a fresh object, which only happens when the producer and Lambda parser versions drift. That last case is a superset of the spec's count, failing loud. This record is also the cycle-log home of the VersionId on healthy cycles (spec).

| Setting | Value | Why |
|---|---|---|
| Filter | `FilterPattern.stringValue('$.event', '=', 'senamhi_relay')`, `metricValue: '$.degraded'`, no `defaultValue` | Healthy cycles publish `0`. A cycle that never ran publishes **nothing** |
| Metric | `FerrenafeRainAlert/SenamhiRelayDegraded`, statistic **Maximum** | A Scheduler retry can put two records in one period. Maximum keeps "any degraded" and does not double-count |
| Period | 6 h (21600 s), **a sliding window** | One schedule interval per window. See *Window semantics* below. Amended after Phase 0: the earlier claim that windows line up with 6 h UTC boundaries was wrong |
| `evaluationPeriods` / `datapointsToAlarm` | 2 / 2 | "Two consecutive degraded cycles" (R2). Mixed reasons count, because the value is 1 either way |
| `treatMissingData` | **BREACHING** | A stopped Lambda, a disabled schedule or a missing env var must never read as OK. This duplicates `NoInvocationsAlarm` on purpose: the cost is a second email, and the alternative is silence |
| Action | existing `AlarmsTopic` | Reuses the `alarmEmail` subscription (R2) |

**Window semantics (amended after Phase 0, Engram 2706).** CloudWatch offers wall-clock-aligned alarm windows only for 60, 300, 3600, 86400 and 604800 s periods. A 21600 s alarm therefore evaluates a *sliding* 6 h window. Nothing in the documentation says when those windows start, so this design no longer assumes they line up with the 00/06/12/18 America/Lima schedule (05/11/17/23 UTC). The configuration still holds without that assumption. The argument:
- The schedule has `TimeWindow.off()`, `maxEventAge` 1 h and 2 retries (`scheduled-cycle-stack.ts`). Two consecutive healthy records are therefore between 5 h and 7 h apart.
- Two consecutive 6 h windows cover 12 h. When every cycle runs, any 12 h span holds at least one record, so both windows can never be empty or breaching at once. **Normal operation cannot alarm.** Ingestion delay on the metric filter does not change this, because a datapoint keeps its log-event timestamp.
- A single late or missing cycle empties one window at most, and 2/2 tolerates that.
- Two consecutive degraded records (value 1), or one degraded record next to one missing cycle, breach both windows. That gives the R2 meaning, with "never ran" counted as degraded. This is the same accepted behaviour as before the amendment.

**Unproven, carried to Phase L (L.9):** how often a 21600 s alarm is evaluated, and the observed transition delay. A template test proves configuration only.

Alternatives for the period, considered after Phase 0:
- *3600 s, wall-clock-aligned, larger M/N* (for example 2 of 12). Rejected. Five of every six hourly windows hold no cycle, so `treatMissingData: breaching` would keep the alarm permanently in ALARM. Switching to `notBreaching` or `ignore` would drop the "a cycle that never ran is degraded" signal, which this alarm duplicates from `NoInvocationsAlarm` on purpose.
- *86400 s, aligned.* Rejected. One window holds four cycles, so 2 consecutive periods would mean about 48 h before anyone hears about it.
- *21600 s sliding, 2/2, breaching.* **Kept**, for the reasons above.

Alternatives rejected: an EMF metric (needs the same stdout discipline plus the EMF envelope, with nothing gained over a filter on our own record); `put_metric_data` from the Lambda (a new IAM grant and a network call on the cycle path); `Sum >= 2` over one 12 h period (fires on one degraded cycle plus one missing period, and loses the "consecutive" meaning).

### D41: The bucket lives in `ScheduledCycleStack`, private, versioned and retained

| Option | Decision |
|---|---|
| New `RelayStack`, bucket name passed into `ScheduledCycleStack` as a prop (D25 pattern) | Rejected. A second deploy and a string-typed name that can drift from the grant |
| Reuse the `PublicSnapshotStack` bucket | Rejected (explore): relaxed public-access guards next to an untrusted-input path |
| **`ScheduledCycleStack`** | **Chosen.** The grants use the bucket construct directly, the alarm sits next to its log group and topic, and the change ships as one deploy |

Bucket: `BlockPublicAccess.BLOCK_ALL`, `enforceSSL: true`, `versioned: true`, `encryption: S3_MANAGED` (KMS rejected: public content, and it would add grants to both principals), `objectOwnership: BUCKET_OWNER_ENFORCED`, lifecycle `noncurrentVersionExpiration: 90 days` plus `abortIncompleteMultipartUploadAfter: 1 day`, `removalPolicy: RETAIN`, **no `bucketName`**. The CDK-generated name embeds no account id. Ninety days of hourly versions is about 2,200 objects and roughly 1.1 GB, which costs cents a month at S3 Standard (https://aws.amazon.com/s3/pricing/, to confirm at apply). It keeps the forensic window (R5) across the whole pre-season.

IAM:
- Lambda role: `s3:GetObject` on `bucket.arnForObjects(object_key)`, and `s3:ListBucket` on `bucket.bucketArn`. Nothing else on this bucket.
- `RelayWriter` managed policy: `s3:PutObject` on the one key. No Delete, List or ACL action.
- `iam.User` `ferrenafe-relay-producer` with only `RelayWriter` attached.

Env var: `RAIN_ALERT_RELAY_BUCKET = bucket.bucketName`. Outputs: `RelayBucketName`, `RelayProducerUserName`, `RelayObjectKey`. No ARNs, which carry the account id.

Context `relayEnabled` (default `true`, the string `'false'` disables, same parsing as `scheduleEnabled`). When `false`, the stack omits the env var **and** the relay alarm. The bucket, user and policy stay. That gives a declarative rollback (D45) without an alarm that would breach forever.

Write audit for this bucket: see D46.

### D42: Producer `rain-alert-relay-push`

`src/rain_alert/entrypoints/relay_push.py`, registered in `pyproject.toml [project.scripts]`. `main(argv=None, *, fetcher=None, client=None, now=None) -> int` keeps all the injection points, and `__main__` is a single call (aws-sdk-python skill).

1. Read `RAIN_ALERT_RELAY_BUCKET`. If it is absent or empty, **exit 1**.
2. `html = HttpxHtmlFetcher().fetch(warnings_url_for(REGION))`. `REGION` is taken from `adapters.local.static_config_repository`. On `FetchError`, **exit 2, no PUT**.
3. `parse_warnings_page(html, REGION, now)`. On `FetchError`, **exit 2, no PUT** (spec: the gate).
4. `body = html.encode("utf-8")`. If `len(body) > cap`, **exit 3, no PUT**.
5. `put_object(...)` per D37, with the same bounded `Config`. On `ClientError` or `BotoCoreError`, **exit 4**.
6. Print one summary line (version id, bytes, rows_seen) and **exit 0**.

The old object is only ever replaced by a page that parsed. A failing producer leaves the last good object to age out into `STALE_RELAY` (R5).

launchd: a template at `ops/launchd/pe.ferrenafe.relay-push.plist` with placeholders (repo path, bucket name, no secret). It contains `StartCalendarInterval` `{Minute: 0}` (hourly, on the hour; see D47, which replaced `StartInterval 3600` after Phase 0), `RunAtLoad true`, `ProgramArguments` `uv run --directory <repo> rain-alert-relay-push`, `EnvironmentVariables` `AWS_PROFILE=ferrenafe-relay`, `AWS_REGION=us-east-2`, `RAIN_ALERT_RELAY_BUCKET=<bucket>`, and stdout/stderr to `~/Library/Logs/ferrenafe-relay/push.log`. An hourly push against a 3 h limit tolerates two missed pushes. Sleep and wake behaviour: D47.

### D43: Wiring: presence of the variable selects relay mode, and there is never a fallback (R7)

```python
def select_warning_provider(env: Mapping[str, str], *, on_read=...) -> WarningProvider:
    if RELAY_BUCKET_ENV_VAR not in env:
        return SenamhiWarningScraper(HttpxHtmlFetcher())
    return RelayWarningProvider(S3RelayReader(env[RELAY_BUCKET_ENV_VAR], key=OBJECT_KEY), on_read=on_read)
```

- **Presence, not truthiness**, unlike `select_snapshot_publisher`, and on purpose. An empty value is a misconfigured relay, so it must become `RELAY_UNREADABLE`, never a direct scrape. Raising at wiring time was rejected because it aborts the cycle, which means no forecast-only alert at all, and that is worse than a degraded one.
- `build_cloud_deps(env: Mapping[str, str] | None = None)` defaults to `os.environ`. The handler stays unchanged. **`build_local_deps` is untouched** (spec).
- The relay branch constructs no `HtmlFetcher` at all, so there is structurally no direct-HTTP path to fall back to.

### D44: Test strategy, strict TDD, and which tests go red first

New fake `tests/support/fake_s3.py::FakeS3Client` (shared by the reader and producer tests). It is hand-written, with no `unittest.mock`. `get_object` returns `{"Body": TrackingBody, "ContentLength", "ContentType", "LastModified", "VersionId", "Metadata"}`. `TrackingBody` records `read` and `close` calls, so "body not read" can be asserted. `put_object` records its kwargs. Failures are programmed with **real** `botocore.exceptions.ClientError({"Error": {"Code": "NoSuchKey", ...}, "ResponseMetadata": {"HTTPStatusCode": 404}}, "GetObject")` and real `EndpointConnectionError`/`ReadTimeoutError` instances. `FakeRelayReader` (returns a `RelayObject` or raises) goes in `tests/support/fakes.py`.

Red-first order:

| # | Test | Layer |
|---|---|---|
| 1 | `is_stale`: 2h59m fresh, **exactly 3h stale**, 4h stale, future clamped | pure |
| 2 | `relay_detail`: VersionId first, at most 160 chars after `_failure_lines`, no VersionId when missing | pure |
| 3 | Contract file equals the Python constants; `region_slug(REGION)` equals the key segment | contract |
| 4 | `S3RelayReader` × every row of D39's table; bounded `Config` pinned (copy the publisher test's `boto3.client` monkeypatch) | adapter + fake |
| 5 | `RelayWarningProvider`: the fresh `warnings_table.html` result equals the scraper's except `fetched_at`; a 4 h calm page gives `STALE_RELAY`, not `Available(())`; `broken_structure.html` gives the scraper's exact reason and detail; one `on_read` record per call; nothing escapes | adapter |
| 6 | `select_warning_provider`: unset gives the scraper; set gives relay; empty gives `RELAY_UNREADABLE` with no fetcher in the graph | wiring |
| 7 | Outage notice names `stale_relay` plus the VersionId; resident message for `STALE_RELAY` is byte-identical to `TIMEOUT` | domain |
| 8 | `relay_push.main`: fetch fail, parse fail and oversized each give zero `put_object` calls and the right exit code; success gives exactly one PUT with the D37 headers and metadata | entrypoint |
| 9 | CDK: BLOCK_ALL, versioning, SSE, lifecycle 90 d, `DeletionPolicy: Retain`, SSL deny; Lambda role relay statements are exactly `GetObject` on `<bucket>/senamhi/lambayeque/latest.html` and `ListBucket` on the bucket; `RelayWriter` has `PutObject` only on that key; user has exactly one policy; `AWS::IAM::AccessKey` count 0; no `BucketName`; env var present; filter pattern and `metricValue`; alarm `Maximum`/21600/2/2/`breaching`/topic; `relayEnabled=false` removes the env var and the alarm | `infra/test` |
| 10 | CDK (D46, classic selectors): exactly one trail; `IsMultiRegionTrail: false`; `IncludeGlobalServiceEvents: false`; exactly one `EventSelectors` entry `{ReadWriteType: WriteOnly, IncludeManagementEvents: false, DataResources: [{Type: AWS::S3::Object, Values: [<relay bucket ARN>/]}]}`; no `AdvancedEventSelectors`; no CloudWatch Logs delivery; file validation on; log bucket BLOCK_ALL, SSL deny, expiry 400 d, `Retain`, no `BucketName`; trail still present with `relayEnabled=false` | `infra/test` |

The architecture test is extended so that `adapters/senamhi_relay.py` and `adapters/s3_relay_reader.py` import nothing from `entrypoints`.

**Not provable offline, recorded in `docs/evidence/` at apply:** a real push and a real Lambda read, one real `PutObject` data event in the trail with the producer's principal, the metric filter matching a real log event, `NoSuchKey` actually returned under the `ListBucket` grant, one observed alarm transition, and the Keychain read under launchd with no dialog.

### D45: Deploy order and rollback

Deploy:
1. Merge. All Python ships with the stack asset, and nothing reads the relay until the env var exists.
2. Run `cdk diff` and then `cdk deploy ScheduledCycleStack` (with the existing `-c` flags). The bucket, user, policy, alarm, trail (D46) and env var arrive together.
3. Within the same 6 h window: create the access key, store it in the Keychain, and run `rain-alert-relay-push` by hand. Confirm the object, `VersionId` and `LastModified`.
4. `aws lambda invoke` by hand. Confirm `"event":"senamhi_relay","degraded":0` in the log and `senamhi_status: available`.
5. `launchctl bootstrap gui/$UID` the agent, then watch two scheduled cycles.

Between steps 2 and 3, a cycle reads `RELAY_MISSING`. That is the same forecast-only mode as today, and one degraded period cannot alarm.

Rollback, fastest first:
1. Stop the producer with `launchctl bootout`.
2. Run `cdk deploy -c relayEnabled=false`. This is declarative and removes the env var and the relay alarm, so the cycle goes back to the direct scraper, which is today's degraded mode.
3. Deactivate the access key.
4. Revert the code.

The bucket is retained as the audit trail. A console edit of the env var is drift that the next deploy reverts. That is the D34 trap, named again here.

### D46: CloudTrail data events, write-only, on the relay bucket, through a dedicated trail

**Resolved by the owner, 2026-10-02.** Purpose: the forensic path for accepted risk R5. Versioning keeps *what* was written; this records *who* wrote each version and *from where* (principal, access key id, source IP, user agent, time).

| Option | Decision |
|---|---|
| S3 server access logs | Rejected. Delivery is best-effort, so a forged write could be the one record that is missing |
| Add data-event selectors to an existing account trail | Rejected. That trail is outside this stack, so the selector would be console or CLI drift (the D34 trap), invisible to `cdk diff` and untestable by a template assertion |
| **Dedicated trail in `ScheduledCycleStack`** | **Chosen.** Declarative, asserted in `infra/test`, removed or kept with the rest of the relay. If an account trail already exists, it keeps its management events and this trail adds nothing to it |

Trail settings (amended after Phase 0, Engram 2706: **classic event selectors, not advanced**):
- `cloudtrail.Trail` with `isMultiRegionTrail: false` and `includeGlobalServiceEvents: false`, both set explicitly because the L2 defaults are `true` (`cloudtrail.d.ts`). Also `enableFileValidation: true`, `sendToCloudWatchLogs: false`, and **management events off** (`managementEvents: ReadWriteType.NONE`), so it never pays for a second copy of management events. With `NONE`, the L2 adds no management selector, forces `includeManagementEvents` to `false` on data selectors, and fails validation unless at least one selector exists (`cloudtrail.js`).
- One classic selector: `trail.addS3EventSelector([{ bucket: relayBucket }], { readWriteType: ReadWriteType.WRITE_ONLY, includeManagementEvents: false })`. There is no `objectPrefix`, so the value is `<relay bucket ARN>/` and it covers the whole bucket. Every write to the bucket is recorded, including writes to a key other than the contract key. The synthesized CFN is `EventSelectors: [{ReadWriteType: WriteOnly, IncludeManagementEvents: false, DataResources: [{Type: AWS::S3::Object, Values: [<bucketArn>/]}]}]`, with no `AdvancedEventSelectors`. Write-only means `PutObject` and `CopyObject` (the D36 restore) are recorded, and the Lambda's hourly reads are not.
- **Why classic, not advanced.** The pinned `aws-cdk-lib` `Trail` L2 only ever pushes classic `{dataResources, includeManagementEvents, readWriteType}` selectors; advanced selectors exist only on `CfnTrail`/`CfnEventDataStore`. CloudFormation rejects a trail that mixes the two. Advanced selectors would add filtering by `eventName` or `resources.ARN` operators, and no requirement here needs that: write-only plus bucket scope is fully expressible in classic form. A `CfnTrail` escape hatch would mean hand-writing the bucket policy and losing the L2's validation, for no requirement gained. Rejected.
- Log bucket: its own `s3.Bucket`, `BLOCK_ALL`, `enforceSSL`, `S3_MANAGED`, `BUCKET_OWNER_ENFORCED`, lifecycle expiry 400 days (one full rainy season plus margin), `removalPolicy: RETAIN`, no `bucketName`. CDK adds the CloudTrail bucket policy. No CloudWatch Logs delivery (nothing alarms on it; it is read after an incident).
- No KMS key: same reasoning as D41.

**Cost.** About 28 write events a day (24 hourly pushes plus manual pushes and restores), roughly 850 a month. Data events are billed per 100,000 events, so this is well under one cent a month, plus negligible S3 storage (https://aws.amazon.com/cloudtrail/pricing/, to confirm at apply).

**Not affected by `relayEnabled=false`.** The trail stays with the bucket, because the audit record must outlive a rollback.

### D47: The producer runs on `StartCalendarInterval`, not `StartInterval` (Phase 0)

**Evidence.** From `man launchd.plist` on the producer Mac (`/usr/share/man/man5/launchd.plist.5`, lines 441-459, read 2026-10-02):

> StartInterval <integer> — This optional key causes the job to be started every N seconds. If the system is asleep during the time of the next scheduled interval firing, that interval will be missed due to shortcomings in kqueue(3). If the job is running during an interval firing, that interval firing will likewise be missed.

> StartCalendarInterval — Unlike cron which skips job invocations when the computer is asleep, launchd will start the job the next time the computer wakes up. If multiple intervals transpire before the computer is woken, those events will be coalesced into one event upon wake from sleep.

| Option | Behaviour after sleep | Decision |
|---|---|---|
| `StartInterval 3600` | Every interval that falls during sleep is **missed**. The next run waits for the next firing after wake, up to 1 h, plus whatever drift the interval timer carries | Rejected |
| **`StartCalendarInterval` `{Minute: 0}`** (hour and day wildcard, so hourly on the hour) | Missed firings coalesce into **one run on wake** | **Chosen** |

The plist key is a single dictionary, `<key>StartCalendarInterval</key><dict><key>Minute</key><integer>0</integer></dict>`. There is no `StartInterval` key. The man page says the two keys are evaluated independently, so carrying both would double-fire. `RunAtLoad true` stays, so a fresh `bootstrap` pushes at once.

**What the operator should expect.** After wake there is one push within seconds. If the network is not up yet, that push fails at the fetch step (exit 2, no PUT, the old object stays), and the next push comes at the next `:00`. The relay therefore goes fresh again within 1 h of wake at the latest. A Mac that sleeps longer than about 3 h makes cycles read `STALE_RELAY`, which is the designed fail-safe direction.

**Residual risk, not removed by this decision.** A Mac that is powered off, logged out (a LaunchAgent needs the user session, D36), or asleep for long still lets the relay go stale. Nothing on the Mac can fix that. R1 (freshness) and R2 (the alarm after 2 consecutive degraded cycles) make it visible, and R4's always-on producer before 2026-12-01 is the real remedy. The deadline stands.

**Still to observe live (L.4).** The coalesced run on wake, and the log line it writes.

---

## 3. File Changes

| Path | Action |
|---|---|
| `src/rain_alert/domain/values.py` | Modify: three `UnavailableReason` members |
| `src/rain_alert/adapters/senamhi_relay.py` | Create: `RelayObject`, `RelayObjectReader`, `RelayWarningProvider`, pure helpers, `RelayReadRecord` |
| `src/rain_alert/adapters/s3_relay_reader.py` | Create |
| `src/rain_alert/entrypoints/wiring.py` | Modify: `select_warning_provider`, `build_cloud_deps(env)` |
| `src/rain_alert/entrypoints/relay_push.py`, `pyproject.toml` | Create / Modify |
| `contracts/senamhi-relay.json` | Create |
| `tests/support/fake_s3.py`, `tests/support/fakes.py` | Create / Modify |
| `tests/unit/adapters/test_senamhi_relay.py`, `test_s3_relay_reader.py`, `tests/unit/entrypoints/test_relay_push.py`, `test_wiring.py`, `tests/unit/domain/test_outage.py`, `tests/architecture/test_layer_boundaries.py` | Create / Modify |
| `infra/lib/scheduled-cycle-stack.ts`, `infra/bin/infra.ts`, `infra/test/scheduled-cycle-stack.test.ts` | Modify (includes the D46 trail and log bucket) |
| `ops/launchd/pe.ferrenafe.relay-push.plist` | Create (template, placeholders) |
| `docs/runbooks/senamhi-relay.md`, `docs/decisions/<next>-senamhi-s3-relay.md`, `docs/architecture/adapters.md` | Create / Modify |

---

## 4. Resolved Decisions (owner, 2026-10-02)

| Question | Resolution |
|---|---|
| D36 credential | IAM user key in Keychain via `credential_process` for the Mac phase. Roles Anywhere **required** for the always-on producer (R4) |
| Third reason | `RELAY_UNREADABLE` adopted. Proposal R7 corrected: missing -> `RELAY_MISSING`, unreadable -> `RELAY_UNREADABLE` |
| Stored content | Gate-validated text as UTF-8 (R9). Specs no longer say "raw bytes" |
| Write audit | CloudTrail data events, write-only, dedicated trail (D46) |

No open questions remain.

## 5. To Verify at Apply

**Phase 0 verdicts (2026-10-02, Engram 2706; the per-item table is in tasks.md Phase 0):**
- Contradicted, then amended: #3 (the alarm window slides, D40), #7 (`StartInterval` misses runs during sleep, D47), #10a (the L2 emits classic selectors only, D46).
- Partial, with the rest carried to Phase L: #2 (`credential_process` under launchd, L.3), #4 (the single-line record shipped in PR 2; a real event is matched in L.5), #10e/f (denied `PutObject` logged, and `versionId` in the event, L.7).
- Confirmed: the rest.

The original list follows, unedited.

1. `GetObject` returns 403 rather than 404 for a missing key without `s3:ListBucket`, and whether an `s3:prefix` condition would satisfy it (API_GetObject.html).
2. The `credential_process` JSON shape and that boto3 honours it from `~/.aws/config`. Also the `security -T` pre-authorization under launchd.
3. CloudWatch alarm period alignment for a 6 h period, and `treatMissingData` behaviour with a metric-filter metric.
4. That the Python Lambda runtime splits multi-line `print` output into separate events, and that the single-line JSON record is matched by a JSON filter pattern under the stack's log format. Check against a real event.
5. That no IAM condition key bounds the PutObject size.
6. The live SENAMHI response charset (`response.encoding`) from the Mac.
7. launchd `StartInterval` behaviour across sleep (whether missed runs coalesce into one on wake).
8. D31's `cycle_headroom_seconds` (60) with the relay read (about 16 s worst case) replacing the scrape.
9. S3 Standard pricing for the 90-day version estimate.
10. D46: the CDK `Trail` API for management events off plus an advanced event selector (or the `addS3EventSelector` equivalent with `includeManagementEvents: false`); whether `describe-trails` shows an existing account trail (recorded, not reused); that a denied `PutObject` attempt also appears as a data event; whether the `PutObject` event carries the object `versionId`, so a version can be joined to its writer; CloudTrail data-event pricing.
