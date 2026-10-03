# ADR 0003 — Fetch SENAMHI outside AWS and relay it through S3

- **Status**: Accepted
- **Date**: 2026-10-02
- **Scope**: `adapters/senamhi_relay.py`, `adapters/s3_relay_reader.py`, `entrypoints/relay_push.py`, `entrypoints/wiring.py` (`select_warning_provider`), `infra/lib/scheduled-cycle-stack.ts`
- **Supersedes**: nothing. It does not reopen [ADR 0001](0001-senamhi-source-strategy.md): the HTML page is still the source and `senamhi_scraper.py` still parses it. Only *where the page is fetched from* changes.
- **Revisit trigger**: see [When to reopen this](#when-to-reopen-this)

## Context

The deployed scheduled cycle has run forecast-only since 2026-10-02: every
SENAMHI fetch from the Lambda ends in `ConnectTimeout: [Errno 110]`. The
rainy season starts around December, and an alert system that cannot see the
official warnings then is the failure this project exists to prevent.

The cause was measured, not assumed
([evidence](../evidence/2026-10-02-senamhi-unreachable-from-aws.md)):

| From | `www.senamhi.gob.pe` (the page) | `idesep.senamhi.gob.pe` (the OGC API) |
|---|---|---|
| Lambda, `us-east-2` | TCP timeout | TCP timeout |
| Lambda, `sa-east-1` | TCP timeout | TCP timeout |
| Developer's machine | HTTP 200 in 1.6 s | HTTP 200 in 1.9 s |

Open-Meteo answered from the same Lambda in 0.6 s, so the function has working
egress. The packets to SENAMHI are dropped silently at the network layer: no
reset and no 403. Both hosts sit in the same `/24`. The evidence does not show
whether the block covers AWS only, all cloud providers or every non-Peruvian
address, nor whether it is deliberate.

## Options considered

| Option | Verdict | Why |
|---|---|---|
| **Use the official OGC/GeoServer API instead of the page** | Rejected | It is behind the same wall. Measured: it times out from both regions exactly like the page. It also still lacks the warning text (ADR 0001). |
| **Move the Lambda to another AWS region** | Rejected | Measured: `sa-east-1`, with a different egress range, fails the same way. Moving would also split the application across regions for no gain. |
| **Route through a residential or commercial proxy** | Rejected | It adds a paid third party and a new secret to the path of life-safety data, and it works by evading a block whose intent we do not know. If SENAMHI blocks cloud ranges on purpose, evading it is the wrong answer. |
| **Run the whole cycle on the Mac** | Rejected | Two writers for the dedup table and the outage state, or a split of that state between a laptop and the cloud. A laptop that sleeps would also stop alerts altogether instead of degrading them. |
| **Trigger the cycle from the S3 write** | Rejected | A dead producer writes nothing, so nothing would fire, and the failure would be silent. The schedule has to stay the heartbeat. |
| **Producer outside AWS, S3 relay, schedule unchanged** | **Chosen** | See below. |

## Decision

**A producer outside AWS fetches the page and drops it in a private S3 bucket.
The existing scheduled cycle reads that object, checks it is fresh, and parses
it with the unchanged parser.**

How it works:

- **Producer.** `rain-alert-relay-push`, run hourly by a macOS LaunchAgent
  (`StartCalendarInterval`, not `StartInterval`, because calendar runs coalesce
  after sleep and interval runs are missed). It fetches the page, runs
  `parse_warnings_page` as a gate, and uploads the exact text it parsed,
  encoded as UTF-8. **No parse, no upload**: a failure leaves the last good
  object to age out.
- **Reader.** `RelayWarningProvider` over `S3RelayReader`. Age is measured from
  the S3 `LastModified` (the server clock, which the credential holder cannot
  set), never from the producer's own claim. An object 3 hours old or more is
  `Unavailable(STALE_RELAY)` and is **not parsed**: a stale "nothing in force"
  is a false calm.
- **Never a fallback.** A configured relay that cannot be used is `Unavailable`
  with one of three reasons (`stale_relay`, `relay_missing`,
  `relay_unreadable`). The code never falls back to scraping from AWS.
- **One contract file** (`contracts/senamhi-relay.json`) carries the object key,
  the age limit and the size cap, asserted equal to the Python constants and
  read by the CDK stack for both IAM grants, so a one-character drift cannot
  turn into silent `AccessDenied`.
- **Resident message unchanged.** The existing "SENAMHI no estuvo disponible"
  text is byte-identical. Only the operator notice names the relay, with the
  object's `VersionId`.
- **Credential, Mac phase.** A dedicated IAM user whose only policy is
  `s3:PutObject` on the one key, with its access key in the macOS Keychain read
  through `credential_process`. CDK creates the user and never the key.
- **Audit.** A dedicated CloudTrail trail records write-only data events on the
  relay bucket, so each version can be tied to a principal and a source address.
- **Switch.** `relayEnabled` in `infra/cdk.json`, shipped `"false"`. Presence of
  `RAIN_ALERT_RELAY_BUCKET` selects relay mode.

Operating procedures: [`docs/runbooks/senamhi-relay.md`](../runbooks/senamhi-relay.md).

## Consequences

- The cycle sees official warnings again, as long as the producer is running.
  When it is not, cycles degrade to forecast-only and say why. **That is the
  same mode as today**, now with a named cause and an alarm.
- **The relay adds a laptop to the critical path.** A Mac that is off, logged
  out or asleep for more than about 3 hours makes cycles read `stale_relay`.
  Two consecutive degraded cycles (about 12 hours) raise a CloudWatch alarm to
  the existing alarm email. Expected, not a fault of the relay.
- **Rollback is a one-line switch, but it restores the broken state, not
  working data.** With the relay off the Lambda scrapes SENAMHI directly and
  times out. Rollback also does **not** revoke the producer's access key; it
  has to be deactivated separately.
- The bucket, the producer user, the policy and the trail are retained across a
  rollback, so the audit record outlives it.
- A second writer to the relay in future means a second credential and a second
  principal in the trail.

## The deadline: the always-on producer before 2026-12-01

**An always-on producer that authenticates with IAM Roles Anywhere is required
before 2026-12-01.** It is a constraint on the next change, not a
recommendation. The Mac phase is the way to get data flowing now; it is not
the end state, because a laptop is the weakest link in a flood-warning path
and a long-lived access key on it is the weakest credential. AWS recommends
Roles Anywhere over long-lived access keys for workloads outside AWS. The
reader is producer-agnostic, so the new producer needs no change on the AWS
side beyond a role. When it goes live, the IAM user and its key are removed in
the same change (runbook: *Retire the key*).

## Accepted risks

**R5 — a forged calm page.** Integrity here is the secrecy of one credential.
Whoever holds the producer key can write a page, and a forged page that parses
as "nothing in force" reaches the evaluator as an `Available` empty result,
which routes it to the less sensitive forecast-only branch. That is worse than
an outage, which is why it is listed first. It is bounded, not removed:

- the key can only `PutObject` on one key;
- the bucket is versioned, so every earlier page can be inspected and restored;
- the reader enforces a size cap and a `sha256`, the producer will not upload a
  page that does not parse, and the object's `VersionId` is in the operator
  notice and the cycle log;
- write-only CloudTrail data events record who wrote each version and from where.

**A frozen but valid SENAMHI page.** The parse gate checks structure, not
authenticity or whether SENAMHI itself is up to date. If SENAMHI's page freezes
and stays valid, the producer stamps it fresh every hour. An "unchanged
content" alarm was rejected: warning pages legitimately stay unchanged for many
hours, so it would cry wolf. TLS to the hardcoded SENAMHI host is the integrity
control for the fetch.

Both are revisited when a second producer exists and the two can be cross-checked.

## Cost

Figures from the verification pass before any code (tasks V.9 and V.10).

| Item | Figure |
|---|---|
| S3 Standard, 90 days of hourly versions (about 2,200 objects, about 1.1 GB noncurrent) | about $0.025 a month |
| CloudTrail data events | $0.10 per 100,000 events |
| Events recorded | about 28 a day (24 hourly pushes plus manual pushes and restores), roughly 850 a month, well under one cent a month |
| Audit log bucket | negligible; 400-day expiry |

Write-only is what keeps the second figure small: the Lambda's reads are not
recorded.

## When to reopen this

- **A second producer exists.** That is when cross-checking two sources, and
  re-weighing R5 and the frozen-page risk, becomes possible. It is also where
  the Roles Anywhere requirement lands.
- **SENAMHI allowlists AWS egress, or the block lifts.** Re-run the probe in the
  evidence file. If the Lambda can reach the page again, the relay is
  unnecessary and the direct scraper returns. This also reopens the question
  ADR 0001 deferred, which was the API as a complement.
- **The page outgrows the 4 MiB cap.** At about 524 KB today it is around 12 % of
  the cap; the cap fails loudly (`relay_unreadable`, exit 3 on the producer),
  not silently.

## Evidence

- [`docs/evidence/2026-10-02-senamhi-unreachable-from-aws.md`](../evidence/2026-10-02-senamhi-unreachable-from-aws.md):
  the probe from both regions and from outside AWS, its method, its cleanup,
  and the live page's charset and size (section 9).
- [`docs/blog/2026-10-02-the-api-was-behind-the-same-wall.md`](../blog/2026-10-02-the-api-was-behind-the-same-wall.md):
  why the first two fixes would not have worked.
- The planning record for this change: `openspec/changes/senamhi-s3-relay/`.

## Related

- [ADR 0001](0001-senamhi-source-strategy.md): the HTML page stays the source.
- [ADR 0002](0002-senamhi-attribution-rule.md): the relay stores unparsed page
  text and never alters it, so the attribution rule is unaffected.
