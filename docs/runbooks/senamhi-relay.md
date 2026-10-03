# Running the SENAMHI relay

SENAMHI silently drops TCP from AWS, so the deployed cycle cannot fetch the
warnings page itself ([evidence](../evidence/2026-10-02-senamhi-unreachable-from-aws.md)).
The relay fixes that with a producer outside AWS: a LaunchAgent on the
operator's Mac runs `rain-alert-relay-push` every hour, proves the page parses,
and replaces one object in a private S3 bucket. The scheduled cycle reads that
object, refuses it if it is 3 hours old or more, and parses it with the same
parser as before. Why this design and what it accepts: [ADR 0003](../decisions/0003-senamhi-s3-relay.md).

Documentation only. Nothing here is automated, and every command is run by
hand by the owner. The shape follows [`public-page-deploy.md`](public-page-deploy.md).

> **Required before 2026-12-01: an always-on producer that authenticates with
> IAM Roles Anywhere.** This runbook describes the Mac phase: one laptop and
> one long-lived IAM access key. A laptop that sleeps, is switched off or is
> logged out lets the relay go stale, and the rainy season starts around
> December. The always-on producer (and the retirement of the key below) is a
> separate change, tracked as proposal rule R4 and recorded in
> [ADR 0003](../decisions/0003-senamhi-s3-relay.md#the-deadline-the-always-on-producer-before-2026-12-01).
> The date stands.

## Quick path

| # | Step | Where | Section |
|---|---|---|---|
| 1 | Deploy the stack with the relay still OFF | operator, AWS | [Step 1](#step-1--deploy-the-stack-with-the-relay-off) |
| 2 | Create the producer's access key, store it in the Keychain, add the profile | operator, Mac | [Step 2](#step-2--create-the-producers-key-and-keychain-item) |
| 3 | Push by hand and confirm a fresh object | Mac | [Step 3](#step-3--push-by-hand) |
| 4 | Install the LaunchAgent | Mac | [Step 4](#step-4--install-the-launchagent) |
| 5 | Confirm the trail delivers | operator, AWS | [Step 5](#step-5--confirm-the-audit-trail-delivers) |
| 6 | Flip `relayEnabled` to `"true"`, deploy, invoke the cycle once | operator | [Step 6](#step-6--turn-the-relay-on) |

The order matters. The relay ships OFF in `infra/cdk.json`, so the first
deploy creates the bucket, the producer user, the policy and the audit trail
but changes nothing the cycle does. You turn it on only after the producer is
proven to push.

## Fixed names

Everything below is copied from the code, not chosen here. If one of these
changes in code, this table is wrong until it is updated.

| Thing | Value | Source |
|---|---|---|
| Region | `us-east-2` | `infra/bin/infra.ts` |
| Stack (construct id, used with `cdk`) | `ScheduledCycleStack` | `infra/bin/infra.ts` |
| Stack (CloudFormation name) | `ferrenafe-scheduled-cycle` | `infra/bin/infra.ts` |
| Relay object key | `senamhi/lambayeque/latest.html` | `contracts/senamhi-relay.json` |
| Maximum age before `stale_relay` | 10800 s (3 h) | `contracts/senamhi-relay.json` |
| Maximum object size | 4194304 bytes (4 MiB) | `contracts/senamhi-relay.json` |
| Producer IAM user | `ferrenafe-relay-producer` | `infra/lib/scheduled-cycle-stack.ts` |
| Stack outputs | `RelayBucketName`, `RelayWriterPolicyName`, `RelayProducerUserName`, `RelayObjectKey` | `infra/lib/scheduled-cycle-stack.ts` |
| Cycle Lambda | `ferrenafe-rain-alert-cycle` | `infra/lib/scheduled-cycle-stack.ts` |
| Lambda env var (the switch) | `RAIN_ALERT_RELAY_BUCKET` | `infra/lib/scheduled-cycle-stack.ts` |
| CDK context switch | `relayEnabled` in `infra/cdk.json` | `infra/cdk.json` |
| AWS profile, producer | `ferrenafe-relay` | `ops/launchd/pe.ferrenafe.relay-push.plist` |
| Keychain service | `ferrenafe-relay-producer` | below |
| LaunchAgent label | `pe.ferrenafe.relay-push` | `ops/launchd/pe.ferrenafe.relay-push.plist` |
| Producer log | `<home>/Library/Logs/ferrenafe-relay/push.log` | `ops/launchd/pe.ferrenafe.relay-push.plist` |
| Producer command | `rain-alert-relay-push` | `pyproject.toml` |

Two AWS profiles, never mixed up: **`ferrenafe`** (from `aws login`) for every
operator action in this runbook, and **`ferrenafe-relay`** only for the
producer, which can do `s3:PutObject` on the one key and nothing else.

The trail and the audit log bucket have **no stack output and no fixed name**
(no name in the repository carries an account id). Step 5 shows how to find them.

Placeholders used below: `<account>`, `<bucket>` (the `RelayBucketName` output),
`<log-bucket>`, `<trail-name>`, `<access-key-id>`, `<version-id>`, `<home>`,
`<repo>`, `<uv>`. Never replace one with a real value in a file you commit.

## Prerequisites

- The `ferrenafe` profile is signed in (`aws login`, per `CLAUDE.md`).
- `infra/` dependencies are installed (`cd infra && npm ci`) and the account is
  bootstrapped for CDK (see [`public-page-deploy.md`](public-page-deploy.md)).
- The three deploy-time context values of `ScheduledCycleStack`, never
  committed: `snapshotWriterPolicyArn` (the `SnapshotWriterPolicyArn` output of
  `PublicSnapshotStack`), `agentRuntimeArn` (from
  [`agentcore-deploy.md`](agentcore-deploy.md)) and `alarmEmail`.
- On the producer Mac: `uv`, `jq`, and this repository checked out at `<repo>`.

## Step 1 — Deploy the stack with the relay OFF

Run `cdk` from `infra/` through its own binary. `npx cdk` is read by npm,
which swallows `-c` (a finding from the infra work for this change), so the
context values would silently not arrive.

```bash
cd infra
export AWS_PROFILE=ferrenafe AWS_REGION=us-east-2

./node_modules/.bin/cdk diff ScheduledCycleStack \
  -c snapshotWriterPolicyArn=<snapshot-writer-policy-arn> \
  -c agentRuntimeArn=<agent-runtime-arn> \
  -c alarmEmail=<alarm-email>
```

Read the diff before deploying. With `relayEnabled` still `"false"` it should
add only: the relay bucket, the writer policy, the producer user, the audit
trail and its log bucket, plus the Lambda role's two read statements. It must
**not** add `RAIN_ALERT_RELAY_BUCKET` to the Lambda, and it must not add the
relay alarm. It must add no `AWS::IAM::AccessKey`: CDK never creates the key.

```bash
./node_modules/.bin/cdk deploy ScheduledCycleStack \
  -c snapshotWriterPolicyArn=<snapshot-writer-policy-arn> \
  -c agentRuntimeArn=<agent-runtime-arn> \
  -c alarmEmail=<alarm-email>
```

Record the `RelayBucketName` output. That value is `<bucket>` from here on.

`relayEnabled` is parsed strictly (`parseRelayEnabled` in `infra/bin/infra.ts`):
the string or boolean `true`/`false` is accepted, an absent value means
disabled, and **anything else fails the synth** rather than guessing. A typo
such as `"yes"` stops the deploy, which is the intended behaviour.

## Step 2 — Create the producer's key and Keychain item

The key is created by hand, because an `AWS::IAM::AccessKey` resource would
put the secret into CloudFormation. The secret is never pasted into a chat,
a file in the repository, `~/.aws/credentials`, or the plist. The Keychain
item's password is the JSON document that `credential_process` expects.

```bash
# Create the key and put the credential_process JSON on the clipboard.
# The secret goes through a pipe only: it is not written to disk and not printed.
# pipefail makes a failure in ANY stage (for example jq missing) visible.
set -o pipefail
trap 'pbcopy < /dev/null' EXIT      # clear the clipboard even if a step fails

aws iam create-access-key --profile ferrenafe --region us-east-2 \
  --user-name ferrenafe-relay-producer --query AccessKey --output json \
  | jq -c '{Version: 1, AccessKeyId: .AccessKeyId, SecretAccessKey: .SecretAccessKey}' \
  | pbcopy
echo "capture exit=$?"

# Store it. `-w` is the LAST option on purpose: security then prompts for the
# password, and you paste the clipboard there (it asks twice). `-T` lets
# /usr/bin/security read the item without a dialog; without it the first
# unattended read raises an "allow" prompt and the job hangs.
security add-generic-password -a "$USER" -s ferrenafe-relay-producer \
  -T /usr/bin/security -U -w

# Clear the clipboard now (the trap above also does it on any failure).
pbcopy < /dev/null
```

If the capture failed after `create-access-key` succeeded (for example `jq` is
missing), an access key now exists that no Keychain item holds. Delete that
orphan at once: find its id with `aws iam list-access-keys --profile ferrenafe
--user-name ferrenafe-relay-producer`, then `aws iam delete-access-key
--profile ferrenafe --user-name ferrenafe-relay-producer --access-key-id
<orphan-access-key-id>`. Universal Clipboard and clipboard managers may retain
the copied value despite the clear; if either is active, turn it off for this
step or treat the key as exposed and rotate it afterwards.

Tradeoff: `-T /usr/bin/security` lets any process running as the same user read
the item without a prompt. That is inherent to `credential_process` with an
unattended job, not something this setup can avoid.

Then add the profile to `~/.aws/config` (operator machine only, never
committed):

```ini
[profile ferrenafe-relay]
region = us-east-2
credential_process = /usr/bin/security find-generic-password -s ferrenafe-relay-producer -w
```

Check the item holds the right shape without printing the secret:

```bash
security find-generic-password -s ferrenafe-relay-producer -w | jq 'keys'
# Expected: ["AccessKeyId", "SecretAccessKey", "Version"]
```

The LaunchAgent runs as a **LaunchAgent**, not a LaunchDaemon, on purpose: the
login keychain only exists inside your user session.

## Step 3 — Push by hand

```bash
cd <repo>
AWS_PROFILE=ferrenafe-relay AWS_REGION=us-east-2 RAIN_ALERT_RELAY_BUCKET=<bucket> \
  uv run --frozen rain-alert-relay-push
echo "exit=$?"
```

Expected: exit `0` and one line like
`relay push: ok version=<version-id> bytes=<n> rows_seen=<n>`. `--profile` and
`--region` flags exist too, for a run outside launchd.

Then confirm the object from the operator side:

```bash
aws s3api head-object --profile ferrenafe --region us-east-2 \
  --bucket <bucket> --key senamhi/lambayeque/latest.html
```

Look for a `VersionId` equal to the one printed, a `LastModified` of a moment
ago, `ContentType` `text/html; charset=utf-8`, and `Metadata` with `sha256`,
`fetched-at` and `producer`.

### What the producer does and its exit codes

It fetches the page, runs the same parser the Lambda will run, and only if that
succeeds uploads exactly the text it parsed. Any failure before the upload
leaves the last good object in place. It never prints page content, a
credential or a raw exception message.

| Exit | Meaning | stderr line (`push.log`) | First thing to check |
|---|---|---|---|
| 0 | Pushed | `relay push: ok version=<id> bytes=<n> rows_seen=<n>` (stdout) | nothing |
| 1 | Not configured or bad usage | `relay push: RAIN_ALERT_RELAY_BUCKET is not set`, or `relay push: RAIN_ALERT_RELAY_BUCKET_OWNER is not a 12-digit account id`, or `relay push: bad usage` | the plist `EnvironmentVariables`: an empty or missing `RAIN_ALERT_RELAY_BUCKET` is exit 1, while a placeholder left in place is not: a leftover literal `<bucket>` is exit 4 (see below), so also check the bucket name |
| 2 | Fetch or parse gate failed, nothing written | `relay push: refused to push, <reason>` where `<reason>` is a value such as `timeout`, `transport_error` or `structure_unrecognized`, or `refused to push, gate crashed: <ExceptionType>` | is the network up; did SENAMHI change its page (`tests/integration/test_senamhi_canary.py` is the early warning) |
| 3 | Page over the size cap, nothing written | `relay push: refused to push, page is over the 4194304-byte cap` | has the page grown, see the ADR on the 4 MiB cap |
| 4 | S3 client or write failed | `relay push: S3 write failed: <ErrorCode or ExceptionType>` | `CredentialRetrievalError` or `ProfileNotFound` means the profile or Keychain item; `AccessDenied` means the policy or the key; `S3 write failed: ParamValidationError` means a leftover placeholder bucket such as the literal `<bucket>` (client-side validation inside the guarded block) |

Exit 2 after a sleep is normal and recovers by itself, see
[Schedule and sleep](#schedule-and-sleep).

Optional hardening, off by default: set `RAIN_ALERT_RELAY_BUCKET_OWNER` to the
12-digit account id and every PUT carries `ExpectedBucketOwner`. A value that
is not 12 digits exits 1 before any fetch. Put the real id in the plist on the
Mac only, never in the repository.

## Step 4 — Install the LaunchAgent

```bash
mkdir -p <home>/Library/LaunchAgents
mkdir -p <home>/Library/Logs/ferrenafe-relay
chmod 700 <home>/Library/Logs/ferrenafe-relay      # the log records the bucket name and versions

cp <repo>/ops/launchd/pe.ferrenafe.relay-push.plist <home>/Library/LaunchAgents/
```

Edit the copy and replace every placeholder: `<uv>` (absolute path, from
`which uv`, because launchd has a minimal PATH), `<repo>`, `<bucket>` and
`<home>` (launchd does not expand `~`). In the plist body these appear
XML-escaped (`&lt;uv&gt;`, `&lt;repo&gt;`, `&lt;bucket&gt;`, `&lt;home&gt;`);
only the header comment shows them unescaped. Replace each escaped token,
including its `&lt;` and `&gt;`, with the plain absolute path or value, with no
angle brackets left. Then:

```bash
plutil -lint <home>/Library/LaunchAgents/pe.ferrenafe.relay-push.plist
launchctl bootstrap gui/$UID <home>/Library/LaunchAgents/pe.ferrenafe.relay-push.plist
launchctl print gui/$UID/pe.ferrenafe.relay-push
```

`launchctl print` shows `state`, `runs` and `last exit code`, and lists the
calendar trigger. `RunAtLoad` is true, so `bootstrap` pushes at once. To run it
again without waiting:

```bash
launchctl kickstart -k gui/$UID/pe.ferrenafe.relay-push
```

Remove it with `launchctl bootout gui/$UID/pe.ferrenafe.relay-push`.

### Schedule and sleep

- The job runs **hourly at `:00`** through `StartCalendarInterval {Minute: 0}`.
  The plist deliberately has **no `StartInterval`**: `man launchd.plist` says
  an interval that falls during sleep is missed, while calendar runs are
  "coalesced into one event upon wake from sleep". The two keys are evaluated
  independently, so carrying both would double-fire.
- After the Mac wakes there is **one push within seconds**. If the network is
  not up yet, that push fails at the fetch step (exit 2, the old object stays)
  and the next `:00` push recovers. Expect a fresh object **within 1 hour of
  wake** at the latest.
- The freshness limit is 3 hours and the push is hourly, so two missed pushes
  are tolerated.
- A Mac that is **off, logged out, or asleep for more than about 3 hours**
  makes the next cycles read `stale_relay`. Two consecutive degraded cycles
  raise the alarm. That is the designed fail-safe direction and not a fault of
  the relay. The remedy is the always-on producer, see the banner at the top.
- The wake behaviour has not yet been observed live on this Mac (task L.4);
  until it has, treat the description above as what `man launchd.plist`
  promises, not as measured.

### Reading `push.log`

```bash
tail -n 20 <home>/Library/Logs/ferrenafe-relay/push.log
```

stdout and stderr of the job both go to that one file. **The lines carry no
timestamp.** To place a line in time, use the file's modification time, or
match the `version=` on an `ok` line against the version's `LastModified`:

```bash
aws s3api list-object-versions --profile ferrenafe --region us-east-2 \
  --bucket <bucket> --prefix senamhi/lambayeque/latest.html \
  --query 'Versions[].{VersionId:VersionId,LastModified:LastModified,IsLatest:IsLatest}'
```

To confirm a push after wake: a new `ok version=…` line, a new `VersionId` at
the top of that list, and `launchctl print` showing `runs` has increased with
`last exit code = 0`. A failed push (`refused to push, …`) followed by an `ok`
line an hour later is also correct.

## Step 5 — Confirm the audit trail delivers

The trail records **write** data events on the relay bucket (`PutObject` and
`CopyObject`) and nothing else: no reads, no management events. It exists
whether or not the relay is on, and it has no output, so find it:

```bash
aws cloudformation describe-stack-resources --profile ferrenafe --region us-east-2 \
  --stack-name ferrenafe-scheduled-cycle \
  --query "StackResources[?ResourceType=='AWS::CloudTrail::Trail'].PhysicalResourceId"

aws cloudtrail describe-trails --profile ferrenafe --region us-east-2 \
  --query 'trailList[].{Name:Name,LogBucket:S3BucketName}'
```

The first command gives the stack's trail, `<trail-name>`. The second gives its
`LogBucket`, `<log-bucket>`. Some minutes after the first push, and within
about an hour of the first log delivery:

```bash
aws cloudtrail get-trail-status --profile ferrenafe --region us-east-2 --name <trail-name>
```

`LatestDeliveryTime` must be set and `LatestDeliveryError` and
`LatestDigestDeliveryError` must be empty. This is the live check that the log
bucket's `Deny` on `aws:SourceAccount` does not block CloudTrail itself:
AWS documents bucket policies conditioned on the source keys, but not
explicitly for digest-file writes. **If either error is set, first correct the
log bucket's `Deny` condition in `infra/lib/scheduled-cycle-stack.ts` (for
example scope it with `aws:SourceArn` to this trail) and redeploy. Remove the
confused-deputy `Deny` only as a last resort, and record that decision in an
ADR; do not simply delete it.** Record the output in `docs/evidence/` (task L.7a).

### Reading CloudTrail write events

Data events are **not** returned by `aws cloudtrail lookup-events` (that is
management events only). They are gzipped JSON files in the log bucket, under
the trail's `AWSLogs/` prefix:

```bash
aws s3 ls --profile ferrenafe --region us-east-2 s3://<log-bucket>/AWSLogs/ --recursive | tail

aws s3 cp --profile ferrenafe --region us-east-2 s3://<log-bucket>/<log-object-key> - \
  | gunzip \
  | jq '.Records[] | select(.eventName == "PutObject" or .eventName == "CopyObject")
        | {eventTime, eventName, sourceIPAddress, userAgent, userIdentity, requestParameters, responseElements}'
```

Read who (`userIdentity`: the producer user and access key id), from where
(`sourceIPAddress`, `userAgent`) and when (`eventTime`). Whether a
`PutObject` event carries the object's version id, and whether a denied write
is logged at all, are **not yet confirmed live** (tasks L.7); if the event has
no version id, join a version to its writer by `eventTime` against the version's
`LastModified`. `userIdentity` contains the account id, `accessKeyId` and `principalId`, and
the events carry `sourceIPAddress`: before pasting anything into
`docs/evidence/`, redact the account id, and replace `accessKeyId`,
`principalId` and `sourceIPAddress` with `<access-key-id>`, `<principal-id>`
and `<operator-ip>`.

## Step 6 — Turn the relay on

Do this only after Step 3 produced a fresh object and Step 4 shows the agent
pushing.

1. Confirm an object exists and is fresh (`head-object` above).
2. Edit `infra/cdk.json` and change `"relayEnabled": "false"` to `"true"`.
   Commit that one-line change: it is the reviewed record of the flip.
3. Deploy `ScheduledCycleStack` exactly as in Step 1.
4. **Invoke the cycle once by hand**, so the alarm has a datapoint.

   This is a **real cycle**, not a dry run. It reads the live sources,
   evaluates risk, writes deduplication and outage state to DynamoDB, publishes
   the public status snapshot to the public bucket, and may invoke the message
   composer. It uses the same SSM configuration and deduplication table as the
   scheduled one. Today the only notifier in the code is `ConsoleNotifier`
   (`entrypoints/wiring.py`): no resident delivery channel exists, so no
   message reaches residents. **Revisit this paragraph when a real notifier
   ships.**

   ```bash
   aws lambda invoke --profile ferrenafe --region us-east-2 \
     --function-name ferrenafe-rain-alert-cycle --cli-read-timeout 150 \
     "$TMPDIR/relay-invoke.json" && cat "$TMPDIR/relay-invoke.json"
   ```

   It returns `{"sent": ..., "level": ..., "degraded": ...}`.

5. Confirm the relay record in the Lambda's log. The log group is created by
   the stack, so ask the function for its name:

   ```bash
   aws lambda get-function-configuration --profile ferrenafe --region us-east-2 \
     --function-name ferrenafe-rain-alert-cycle --query LoggingConfig.LogGroup --output text

   aws logs filter-log-events --profile ferrenafe --region us-east-2 \
     --log-group-name <log-group> --filter-pattern '{ $.event = "senamhi_relay" }' \
     --max-items 3
   ```

   A healthy record is one compact JSON line:
   `{"event":"senamhi_relay","degraded":0,"reason":null,"version_id":"…","last_modified":"…","age_seconds":…,"skew_seconds":…}`.
   `degraded` is `1` for every relay failure, including a fresh object that
   no longer parses. Each of those yields an Unavailable source, so the
   cycle's own JSON shows `senamhi_status` as `"unavailable"` and `degraded`
   as true; a degraded record never coexists with `available`.

### The creation-time ALARM, expected

The relay alarm treats missing data as breaching. A brand-new alarm has empty
windows and can go to `ALARM`, with an email, **within minutes of the deploy**,
before the first record lands. The manual invoke in step 4 is what prevents or
clears it. The same happens **on every re-enable**. If you see that email right
after a flip, check the invoke ran before assuming a fault.

The alarm only has an alarm action, so recovery sends **no email**. Look at
its state directly:

```bash
aws cloudwatch describe-alarms --profile ferrenafe --region us-east-2 \
  --query "MetricAlarms[?MetricName=='SenamhiRelayDegraded'].{State:StateValue,Reason:StateReason,Updated:StateUpdatedTimestamp}"
```

How long a 21600 s alarm takes to change state is not yet observed live
(task L.9); do not rely on a number.

### The alarm that is legitimate

The alarm is `Maximum` of `SenamhiRelayDegraded`, period 21600 s (a **sliding**
window), 2 of 2 periods, missing data breaching, threshold 1. In practice it
fires after **about 12 hours** without a healthy record. That includes: two
consecutive degraded cycles in any mix of `stale_relay`, `relay_missing`,
`relay_unreadable` and the parser reasons below (any record with `degraded=1`
counts), and a cycle that never ran. If the producer is absent for
about 12 hours or more after you enabled the relay, the alarm is telling the
truth. It is separate from, and deliberately duplicates, the no-invocation
alarm.

### What each reason means

| Reason (operator notice and log) | What happened | Look at |
|---|---|---|
| `stale_relay` | The object exists but is 3 h old or more; it is not parsed, because a stale "nothing in force" would be a false calm | the producer: Mac awake, logged in, `push.log` |
| `relay_missing` | `NoSuchKey`: the producer never pushed | Step 3, a first push |
| `relay_unreadable` | Access denied, any other S3 error, an object over the size cap, a bad charset, a missing or wrong `sha256`, a `LastModified` more than 60 s ahead of the cycle clock, or the bucket variable present but empty | the notice detail names the code; the grant, the bucket name, or a producer/Lambda version drift |
| the parser's own reason, for example `structure_unrecognized` or `no_rows_extracted` | A fresh object was read but its parse failed; the record carries the parser's reason, not `relay_unreadable`. `degraded` is still `1`, so it counts toward the alarm | the object's content: did SENAMHI change its page, or was a bad object pushed (see the leak response) |
| `transport_error` | The parser crashed with an unexpected exception (not a fetch error) on a fresh object; `degraded` is `1` | the notice detail names the exception type; a parser bug or producer/Lambda version drift |

A relay failure never falls back to scraping SENAMHI from AWS. The resident
message is the same SENAMHI-unavailable text as for any outage; only the
operator notice names the relay, with the `version=` of the object.

## Rotate the key (every 90 days)

Also rotate whenever `aws iam get-access-key-last-used` shows use you cannot
account for.

```bash
aws iam list-access-keys --profile ferrenafe --user-name ferrenafe-relay-producer
aws iam get-access-key-last-used --profile ferrenafe --access-key-id <old-access-key-id>
```

1. IAM allows two keys per user. Record the old key's id
   (`<old-access-key-id>`, from `list-access-keys` above) **before** replacing
   anything. Create the new key (`<new-access-key-id>`) and replace the
   Keychain item with the same commands as Step 2 (`-U` updates the existing
   item).
2. Run one manual push (Step 3). Confirm a new `VersionId` and a fresh
   `LastModified`.
3. Set the old key to `Inactive`:

   ```bash
   aws iam update-access-key --profile ferrenafe --user-name ferrenafe-relay-producer \
     --access-key-id <old-access-key-id> --status Inactive
   ```

4. Wait one cycle (the next hourly push must succeed), then delete it:

   ```bash
   aws iam delete-access-key --profile ferrenafe --user-name ferrenafe-relay-producer \
     --access-key-id <old-access-key-id>
   ```

## Revoke on a suspected leak

Speed first, then forensics. The risk being managed is a forged "calm" page:
a forged page that parses is read as a real, empty warning list.

1. **Deactivate the key now.**

   ```bash
   aws iam update-access-key --profile ferrenafe --user-name ferrenafe-relay-producer \
     --access-key-id <access-key-id> --status Inactive
   launchctl bootout gui/$UID/pe.ferrenafe.relay-push
   ```

2. **List the versions in the exposure window.**

   ```bash
   aws s3api list-object-versions --profile ferrenafe --region us-east-2 \
     --bucket <bucket> --prefix senamhi/lambayeque/latest.html \
     --query 'Versions[].{VersionId:VersionId,LastModified:LastModified,Size:Size,IsLatest:IsLatest}'
   ```

3. **Find who wrote each one** from the CloudTrail write events
   ([above](#reading-cloudtrail-write-events)): any `PutObject` from an
   unexpected principal or source address marks a version you must not trust.
4. **Promote a known-good version.** A copy makes a new current version, so it
   gets a fresh `LastModified`:

   ```bash
   aws s3api copy-object --profile ferrenafe --region us-east-2 \
     --bucket <bucket> --key senamhi/lambayeque/latest.html \
     --copy-source "<bucket>/senamhi/lambayeque/latest.html?versionId=<version-id>"
   ```

   The copy keeps the object's metadata, so the reader still validates its
   `sha256`. Choose a **recent** known-good version: a restored old page is
   read as fresh for up to 3 hours, so an old one would be a stale page
   passing for current. The copy keeps the original `fetched-at`, so a healthy
   cycle may add a skew note to the operator view. That is informational.
5. A bad object stays "fresh" until 3 hours after its own `LastModified`, and
   only then turns into `stale_relay`. That is why step 4 matters; the staleness
   limit alone is not a substitute for it.
6. **Close out after forensics.** Delete the compromised key
   (`aws iam delete-access-key --profile ferrenafe --user-name
   ferrenafe-relay-producer --access-key-id <compromised-access-key-id>`),
   create a new key and replace the Keychain item (Step 2), then restart the
   producer with `launchctl kickstart -k gui/$UID/pe.ferrenafe.relay-push`
   (use `launchctl bootstrap` first if you ran `bootout`). Until a new push
   succeeds the relay goes stale after about 3 hours and the cycles degrade.

## Retire the key (Roles Anywhere migration)

When the always-on producer goes live on IAM Roles Anywhere (the R4 deadline),
in the same change: deactivate and delete this key, remove the IAM user and its
policy from the stack, delete the Keychain item
(`security delete-generic-password -s ferrenafe-relay-producer`), remove the
`ferrenafe-relay` profile from `~/.aws/config`, and `bootout` the agent.

## Roll back

Fastest first. The relay is a switch; the bucket and trail stay.

1. **Stop the producer:** `launchctl bootout gui/$UID/pe.ferrenafe.relay-push`.
2. **Commit `"relayEnabled": "false"` in `infra/cdk.json` and deploy**
   (Step 1's command). In an emergency, `cdk deploy ... -c relayEnabled=false`
   works, but then commit the same value so the next deploy does not revert it.
   This removes `RAIN_ALERT_RELAY_BUCKET` and the relay alarm. The bucket,
   producer user, policy and the trail **stay**, so the audit record outlives
   the rollback.
3. **Deactivate and delete the producer's access key.** Rollback does **not**
   revoke it: CDK never created the key, so removing the switch leaves a working
   credential on the Mac. Use the commands in [Rotate](#rotate-the-key-every-90-days)
   (step 3 and step 4) or [Revoke](#revoke-on-a-suspected-leak).
4. Revert the code if needed.

What a rollback gives you is **today's degraded mode, not working SENAMHI
data**: with no `RAIN_ALERT_RELAY_BUCKET` the Lambda goes back to scraping
SENAMHI directly, which times out from AWS (the evidence file). Cycles then run
forecast-only. Never edit the Lambda's environment in the console: the next
deploy reverts it.

## What this runbook does not cover

- The always-on producer and IAM Roles Anywhere. Required before 2026-12-01.
- Asking SENAMHI to allowlist AWS egress, which would make the relay
  unnecessary ([ADR 0003](../decisions/0003-senamhi-s3-relay.md#when-to-reopen-this)).
- The live verification pass (tasks L.1 to L.12), which writes its own file
  under `docs/evidence/` from real output.
