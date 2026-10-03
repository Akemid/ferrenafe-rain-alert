# Proposal: SENAMHI S3 Relay

Exploration: `openspec/changes/senamhi-s3-relay/explore.md`. Evidence: `docs/evidence/2026-10-02-senamhi-unreachable-from-aws.md`. Related: ADR 0001 (HTML scraping stays; this change does not reopen it).

## Intent

The deployed cycle cannot see official warnings. SENAMHI silently drops TCP from AWS: `www` and `idesep` both time out from `us-east-2` and `sa-east-1` (TCP `TimeoutError`, ~10 s), while Open-Meteo answers in ~0.6 s from the same function and SENAMHI answers `200` in ~1.6 s from the developer's machine. Every scheduled cycle since 2026-10-02T06:18Z runs forecast-only. The rainy season starts around December; an alert system blind to official warnings then is the failure this project exists to prevent.

The fix: a producer outside AWS fetches the page, validates it with the existing parser, and drops the validated page text (UTF-8) in a private S3 bucket; the cycle reads it, checks freshness, and parses it again with the same tested parser.

## Business rules (confirmed by the owner)

| # | Rule |
|---|------|
| R1 | **Freshness: 3 hours**, measured from S3 `LastModified` (server clock). The producer timestamp is informational; skew > ~15 min is flagged in the operator notice and log. Stale -> `Unavailable(STALE_RELAY)`, never `Available`. |
| R2 | **Monitoring**: CloudWatch alarm -> email to the operator after **2 consecutive cycles** degraded by the relay (`STALE_RELAY`, `RELAY_MISSING` and `RELAY_UNREADABLE` counted together, any mix; ~12 h), reusing the stack's `alarmEmail` topic. Resolved by the owner, 2026-10-02. |
| R3 | **Resident message unchanged.** The existing "SENAMHI no estuvo disponible..." template text stays byte-identical. The relay-vs-SENAMHI distinction appears only in the operator notice. |
| R4 | **One producer** (developer's Mac, launchd) in this version. **An always-on producer is required before 2026-12-01, and it MUST authenticate with IAM Roles Anywhere** (no long-lived access key). The reader is producer-agnostic. Roles Anywhere constraint resolved by the owner, 2026-10-02. |
| R5 | **Integrity is an accepted risk**: credential-only, bounded by a single-key write policy, versioning, a size cap, a producer parse gate (upload only after a successful parse; a failed fetch never overwrites the last good object), the object **VersionId in the operator notice and cycle log**, and **CloudTrail data events (write-only) on the relay bucket** as the forensic record of who wrote each version and from where. A forged calm page is worse than an outage (it routes the evaluator to the less sensitive forecast-only branch as `Available`). Revisit when a second producer exists. |
| R6 | **The schedule is the heartbeat.** The cycle stays EventBridge-driven so a dead relay surfaces as degraded mode, never silence. |
| R7 | A configured relay that cannot be used is `Unavailable`, with one of three reasons: object age >= 3 h -> `STALE_RELAY`; object does not exist (`NoSuchKey`/404) -> `RELAY_MISSING`; access denied, throttling or any other S3/client error -> `RELAY_UNREADABLE`. The cycle never falls back to direct scraping. Resolved by the owner, 2026-10-02. |
| R8 | **Producer credential (Mac phase)**: a dedicated IAM user access key scoped to `PutObject` on the single relay key, stored in the macOS Keychain and read through `credential_process`. Rotation and revocation are in the runbook. Resolved by the owner, 2026-10-02 (design D36). |
| R9 | **Stored content**: the producer stores the page text it validated with its local parse gate, encoded as UTF-8 (`Content-Type: text/html; charset=utf-8`), not SENAMHI's original bytes. It is unparsed HTML text, not parsed data; the Lambda parses it with `parse_warnings_page`. Archiving the original bytes is out of scope. Resolved by the owner, 2026-10-02. |

## Scope

### In Scope
- `UnavailableReason.STALE_RELAY`, `RELAY_MISSING`, `RELAY_UNREADABLE` (R7).
- `RelayWarningProvider` behind `ports.WarningProvider` (reader port + freshness + `parse_warnings_page`), `Available.fetched_at` = relay time.
- S3 relay reader adapter (bounded boto3 `Config`, size cap, hand-written fake).
- `select_warning_provider(env)` in `build_cloud_deps` keyed on `RAIN_ALERT_RELAY_BUCKET`; `build_local_deps` unchanged.
- Producer entrypoint `rain-alert-relay-push` + launchd plist + runbook (including key rotation and revocation, R8).
- CDK: private bucket (block all public access, SSL, versioning, no account id in name), Lambda `GetObject` on one key plus `ListBucket` on the bucket (required so a missing object is `NoSuchKey`, not `AccessDenied`), `RelayWriter` managed policy (`PutObject` on one key), producer IAM user without an access key resource, metric filter + 2-period alarm.
- CDK: CloudTrail data events, write-only, scoped to the relay bucket, with a dedicated trail and log bucket (R5, design D46).

### Out of Scope
- Local dashboard UI; S3-event trigger (future optimisation only); second/always-on producer and its Roles Anywhere setup (tracked by R4); SENAMHI allowlisting request; GeoServer complement (ADR 0001); archiving SENAMHI's original bytes (R9); S3 server access logs (best-effort delivery, replaced by CloudTrail data events).

## Capabilities

### New Capabilities
- `senamhi-relay`: producer contract (parse gate, no overwrite on failure, UTF-8 text, metadata), reader contract (freshness, size cap, three unavailable reasons), credential scope, write audit, alarm.

### Modified Capabilities
- `weather-sources`: SENAMHI acquisition may come from the relay; new unavailable reasons; stale is never a calm.
- `source-outage-notices`: operator notice names the relay cause, object VersionId and skew; resident text unchanged.

## Approach

Exploration approach 2. Producer uploads the gate-validated page as unparsed HTML text encoded UTF-8 (R9); parsing stays in `parse_warnings_page`. The domain stays free of S3 concerns: the provider sees only `(html, last_modified, version_id, metadata)` through a port.

## Affected Areas

| Area | Impact |
|------|--------|
| `src/rain_alert/domain/values.py` | Modified — three reasons |
| `src/rain_alert/adapters/` | New — relay provider, S3 reader |
| `src/rain_alert/entrypoints/wiring.py` | Modified — provider selection |
| `src/rain_alert/entrypoints/` + `pyproject.toml` | New — `rain-alert-relay-push` |
| `infra/lib/scheduled-cycle-stack.ts`, `infra/test/` | Modified — bucket, grants, alarm, CloudTrail data-event trail |
| `docs/runbooks/`, `docs/decisions/` | New — producer runbook, relay ADR |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Mac sleeps; cycles run degraded | High | R1/R2 make it visible; R4 deadline |
| Forged/garbage page accepted as calm | Low | R5 controls; parser rejects non-table pages; CloudTrail write events identify the writer after the fact |
| Long-lived producer key leaks | Low–Med | Single-key `PutObject`, Keychain, rotation and revocation in runbook; Roles Anywhere required for the always-on producer (R4) |
| Charset drift between producer and reader | Low | Store the gate-validated text as UTF-8 (R9); reader decodes strictly; verify the live charset once |
| Forensic gap if CloudTrail is misconfigured | Low | CDK assertion test on the trail's selectors; one live event confirmed at apply |

## Rollback Plan

Deploy with `-c relayEnabled=false` (design D45): the stack drops `RAIN_ALERT_RELAY_BUCKET` and the relay alarm, and the cycle returns to the direct scraper (today's degraded mode). Do not edit the env var in the console; the next deploy reverts it. Stop the launchd job and deactivate the access key. The bucket, trail and policy stay as the audit record; no resident-facing state depends on them.

## Success Criteria

- [ ] A cycle with an object < 3 h old reports SENAMHI `Available`, `fetched_at` = `LastModified`.
- [ ] An object >= 3 h old yields `STALE_RELAY` and one operator notice containing the VersionId.
- [ ] A missing object yields `RELAY_MISSING`; access denied or any other S3 error yields `RELAY_UNREADABLE`; neither attempts a direct scrape.
- [ ] The alarm fires after 2 consecutive degraded cycles in any mix of the three reasons (CDK template test + one live observation).
- [ ] Resident template text byte-identical to `main`.
- [ ] Producer does not upload when fetch or parse fails (test), and stores UTF-8 text with `Content-Type: text/html; charset=utf-8`.
- [ ] Bucket blocks all public access; Lambda role has `GetObject` on exactly one key and `ListBucket` on the bucket only; no account id in the repo.
- [ ] CloudTrail records write-only data events for the relay bucket (CDK assertion test + one live `PutObject` event observed).
- [ ] `uv run pytest` stays offline and green.

## Open questions

All resolved. Answers live in `design.md`: credential D36 (owner, 2026-10-02), key and metadata D37, size cap 4 MiB D37, alarm counts all three reasons D40 (owner, 2026-10-02), stack placement D41, audit trail D46 (owner, 2026-10-02). `snapshot.py` exposes no unavailable reason to residents (design D38).

Strict TDD applies in apply. A security review runs on the branch diff before every pull request.
