# 2026-10-03 — The SENAMHI relay, deployed and verified live (Phase L)

Region: `us-east-2`. Profiles: `ferrenafe` (operator), `ferrenafe-relay`
(producer). Change: `openspec/changes/senamhi-s3-relay/`. Procedure:
[`docs/runbooks/senamhi-relay.md`](../runbooks/senamhi-relay.md).

This record covers runbook steps 1 to 5. During those steps the relay stays
off in production, so the cycle Lambda does not read the relay object yet.
Step 6 turns it on, and is recorded below once it has run.

## 1. Deploy with the relay and the schedule off (runbook step 1)

`cdk diff` with only the three documented context values showed one change
that neither the runbook nor the design expected:

```
[~] AWS::Scheduler::Schedule Schedule Schedule83A77FD1
 └─ [~] State
     ├─ [-] DISABLED
     └─ [+] ENABLED
```

The schedule had been disabled on 2026-10-02 at 03:29 UTC with
`-c scheduleEnabled=false`. The reason is documented in
`docs/submission/builder-center-post.md`: with SENAMHI unreachable from AWS,
every scheduled cycle would overwrite a correct page with a degraded one.
`scheduleEnabled` is read from the CLI context of each deploy, not from
`infra/cdk.json`. A deploy without that flag would therefore have re-enabled
the cycle as a side effect. The deploy was rerun with
`-c scheduleEnabled=false`. The re-diff showed only the expected relay
resources, and the deploy completed in 84 s.

State read back from the account after the deploy:

```
schedule state:                 DISABLED
lambda env keys:                RAIN_ALERT_AGENT_RUNTIME_ARN, RAIN_ALERT_AGENT_TIMEOUT_S, RAIN_ALERT_SNAPSHOT_BUCKET
RAIN_ALERT_RELAY_BUCKET:        absent (relay off)
relay bucket public access:     BlockPublicAcls, IgnorePublicAcls, BlockPublicPolicy, RestrictPublicBuckets = true
relay bucket versioning:        Enabled
relay bucket objects:           0
producer user access keys:      0
producer user policies:         ferrenafe-scheduled-cycle-RelayWriter4BA429F7-<suffix>
trail:                          single region, log file validation on, logging
trail event selectors:          [{ReadWriteType: WriteOnly, IncludeManagementEvents: false,
                                  DataResources: [{Type: AWS::S3::Object, Values: [<relay bucket ARN>/]}]}]
relay alarm:                    none (created only when the relay is on)
```

## 2. Producer key and Keychain item (runbook step 2)

Two attempts produced orphan access keys. In the first, the operator copied
the second command from the chat after the capture. That replaced the
clipboard, so the Keychain item stored an 11-character fragment of the copied
text. In the second, a single block was used, but its store step did not run.
In both cases the secret was unrecoverable, because AWS shows it once. Each
orphan key was deleted, and the user was back to zero keys each time. The
third attempt avoided the clipboard entirely: the JSON was held in a shell
variable and passed straight to `security add-generic-password -w`. The
runbook still uses the clipboard and should be changed to this method.

Verification, without printing the secret:

```
keychain item:                  valid JSON, keys [AccessKeyId, SecretAccessKey, Version], Version 1
producer access keys in IAM:    1, Active, id suffix matches the Keychain item
aws sts get-caller-identity --profile ferrenafe-relay:
                                arn:aws:iam::<account>:user/ferrenafe-relay-producer
```

## 3. Push by hand (runbook step 3)

```
$ AWS_PROFILE=ferrenafe-relay RAIN_ALERT_RELAY_BUCKET=<bucket> uv run --frozen rain-alert-relay-push --region us-east-2
relay push: ok version=Oti2y3JJNWFmdhjCjx57zjpDjGxQCs3O bytes=523653 rows_seen=808
exit=0
```

`HeadObject` on `senamhi/lambayeque/latest.html`:

```
content type:   text/html; charset=utf-8
length:         523653
metadata:       fetched-at=2026-10-03T04:25:21+00:00, producer=rain-alert-relay-push, sha256=<64 hex>
last modified:  2026-10-03T04:25:23+00:00   (2 s after fetched-at; skew tolerance is 15 min)
encryption:     AES256
```

## 4. LaunchAgent (runbook step 4)

The template's placeholders were filled with absolute paths. The installed
plist passes `--region us-east-2` and sets `AWS_DEFAULT_REGION` (#36), and
`plutil -lint` reports OK. After
`launchctl bootstrap gui/$UID ~/Library/LaunchAgents/pe.ferrenafe.relay-push.plist`,
`RunAtLoad` pushed at once:

```
launchctl print: state = not running, runs = 1, last exit code = 0, StartCalendarInterval Minute => 0
push.log:        relay push: ok version=OyC_3wmPu1.Budf2SN8.fFQQOmX2ijAC bytes=523653 rows_seen=808
```

This closes **L.3 live**. Under launchd, `credential_process` reads the
Keychain item without a prompt, and the push succeeds.

## 5. The audit trail delivers (runbook step 5, L.7a and L.7)

`GetTrailStatus`:

```
IsLogging:                  True
LatestDeliveryTime:         2026-10-03T04:30:23Z
LatestDeliveryError:        None
LatestDigestDeliveryTime:   2026-10-03T04:01:09Z
LatestDigestDeliveryError:  None
```

The log bucket's `Deny` on `aws:SourceAccount` (`StringNotEquals`) does not
block delivery of log files or digest files. This closes **L.7a**.

The delivered log file holds both pushes. It was read with `aws s3 cp`,
`gunzip` and `jq`, because the `aws-mcp` sandbox blocks `gzip`. Access key id,
principal id and source IP are omitted:

```
PutObject  2026-10-03T04:25:23Z  ferrenafe-relay-producer  senamhi/lambayeque/latest.html  versionId=Oti2y3JJNWFmdhjCjx57zjpDjGxQCs3O  error=null  Boto3/1.43.98
PutObject  2026-10-03T04:26:12Z  ferrenafe-relay-producer  senamhi/lambayeque/latest.html  versionId=OyC_3wmPu1.Budf2SN8.fFQQOmX2ijAC  error=null  Boto3/1.43.98
```

The data event carries the object's `versionId`, so every version the cycle
reads can be traced to the identity that wrote it. This closes **L.7**. The
optional denied-write check was not run.
