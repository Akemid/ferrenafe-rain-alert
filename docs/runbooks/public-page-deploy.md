# Deploying the public snapshot bucket

Documentation only — nothing here is automated. This deploys the `infra/`
CDK app (task 7 of the `public-status-page` change): one S3 bucket holding
one public object, `status.json`, produced by each rain-alert cycle and read
by the public status page through Amplify's reverse-proxy rewrite.

The bucket, its public-access-block configuration, its versioning, its bucket
policy and the managed policy that grants write access are all defined in
`infra/lib/public-snapshot-stack.ts`. That file's comments carry the security
reasoning (Amplify's proxy fetches unsigned, so the object must be publicly
readable; both policies are scoped to the single `status.json` key, never the
bucket; nothing in the stack may rewrite the bucket policy) — this runbook is
the deploy procedure, not a restatement of that reasoning.

## Region: `us-east-2`, not this project's usual `us-east-1`

`CLAUDE.md` names `us-east-1` as this project's deployment target, but the
AgentCore composer runtime (`docs/runbooks/agentcore-deploy.md`) is actually
deployed in `us-east-2`, and this stack follows it there so the whole
application stays in one region. Use `us-east-2` for every command below.

## Scope

This deploys exactly one thing: the `infra/` CDK app (the `PublicSnapshotStack`,
CloudFormation stack name `ferrenafe-public-snapshot`). It does not deploy the
application code that writes to the bucket (`S3SnapshotPublisher`, wired in
`src/rain_alert/entrypoints/wiring.py`) or the Amplify app that reads from it
— both are configured separately, against this stack's outputs.

## Prerequisites

- AWS credentials for the `ferrenafe` profile (`aws login`, per `CLAUDE.md`),
  with permission to create S3 buckets, bucket policies, and IAM managed
  policies.
- The CDK environment bootstrapped once for this account and region
  (`npx cdk bootstrap aws://<account>/us-east-2` from `infra/`), if not
  already done for another stack in this account.
- Node.js and `npm`, matching `infra/package.json`.

## Step 1 — Install dependencies (already done for this commit)

```bash
cd infra
npm install
```

`infra/package-lock.json` is committed, so this installs the exact tree that
was reviewed. Use `npm ci` rather than `npm install` if you want the install
to fail on a lockfile that has drifted from `package.json` instead of quietly
rewriting it.

## Step 2 — Synthesize and review before deploying

```bash
cd infra
AWS_PROFILE=ferrenafe AWS_REGION=us-east-2 npx cdk synth PublicSnapshotStack
```

**The stack name is required as of the `scheduled-cycle` change**: `infra/`
now synthesizes a second stack (`ScheduledCycleStack`), and a bare `cdk
synth`/`cdk deploy` with no stack name acts on every stack the app
constructs — including one whose required deploy-time context
(`snapshotWriterPolicyArn`, `agentRuntimeArn`, `alarmEmail`) this runbook has
no reason to supply. Naming `PublicSnapshotStack` explicitly keeps this
runbook's commands scoped to the one stack it deploys.

Read the generated template (`cdk.out/PublicSnapshotStack.template.json`)
before deploying anything from it, especially after touching
`public-snapshot-stack.ts`. Confirm in particular that the `s3:GetObject`
statement's `Resource` is still the single `.../status.json` key, not the
whole bucket — a change here is exactly the kind of defect that looks
correct in the source and is not.

## Step 3 — Deploy

```bash
cd infra
AWS_PROFILE=ferrenafe AWS_REGION=us-east-2 npx cdk deploy PublicSnapshotStack
```

The region is pinned in `infra/bin/infra.ts`, so `cdk deploy` cannot be
aimed at another one by an ambient `AWS_REGION`; the `AWS_REGION` above is
kept for the credential resolution and is now redundant with the stack's own
`env`.

Confirm the changeset when prompted. On success, the CLI prints three outputs:

- `SnapshotBucketName` — the bucket name, for
  `RAIN_ALERT_SNAPSHOT_BUCKET` (see Step 5).
- `SnapshotObjectUrl` — the full URL to `status.json`, used in Step 4.
- `SnapshotWriterPolicyArn` — the managed policy that grants `s3:PutObject`
  on `status.json` and nothing else. Attach it to whichever principal runs
  the cycle. **Do not write a write policy by hand**: the reason this output
  exists is that a policy invented at the console is broad, and this bucket
  is world-readable by design, so a writer that can also rewrite the bucket
  policy decides what every resident reads.

  ```bash
  aws iam attach-role-policy --profile ferrenafe \
    --role-name <the role that runs the cycle> \
    --policy-arn "<SnapshotWriterPolicyArn>"
  ```

## Step 4 — Verify the object is readable and the bucket is not listable

Before anything has been published, the object does not exist yet:

```bash
curl -s -o /dev/null -w "object: %{http_code}\n" "<SnapshotObjectUrl>"
# Expected: 403 before a cycle has published, 200 once one has.
```

The bucket listing must always be `403`, regardless of whether an object has
been published:

```bash
curl -s -o /dev/null -w "listing: %{http_code}\n" \
  "https://<bucket>.s3.us-east-2.amazonaws.com/"
# Expected: 403.
```

If the listing check returns `200`, the bucket policy is broader than
intended — stop, do not wire the application to this bucket, and fix
`public-snapshot-stack.ts` before redeploying.

## Step 5 — Wire the application to the deployed bucket

Two environment variables, read by
`src/rain_alert/entrypoints/wiring.py::select_snapshot_publisher`:

| Variable | Required | Meaning |
|---|---|---|
| `RAIN_ALERT_SNAPSHOT_BUCKET` | Yes, to publish to S3 | The `SnapshotBucketName` output from Step 3. Absent by default — a developer running the cycle locally must not write to a public bucket by accident. |
| `RAIN_ALERT_SNAPSHOT_KEY` | No — defaults to `status.json` | The object key. Must match the key the bucket policy in `public-snapshot-stack.ts` scopes `s3:GetObject` to; changing one without the other breaks public readability. |

`RAIN_ALERT_SNAPSHOT_PATH` (the local-file adapter, for development) is
ignored once `RAIN_ALERT_SNAPSHOT_BUCKET` is set — the bucket wins by design
(`select_snapshot_publisher`'s docstring).

## Rollback

Two levels, cheapest first:

1. **Unset `RAIN_ALERT_SNAPSHOT_BUCKET`.** The next cycle stops publishing to
   S3 (and, per the wiring precedence, falls back to the local-file adapter
   only if `RAIN_ALERT_SNAPSHOT_PATH` is also set — otherwise it publishes
   nowhere). No infrastructure change needed.
2. **Empty the bucket by hand, then `cdk destroy`.** The bucket carries
   `removalPolicy: DESTROY` but deliberately **not** `autoDeleteObjects`, so
   CloudFormation will refuse to delete a bucket that still holds objects.
   That refusal is the price of not provisioning a Lambda whose role holds
   `s3:PutBucketPolicy` on a bucket whose Block Public Access is relaxed —
   `infra/lib/public-snapshot-stack.ts` carries the reasoning.

   Versioning is enabled, so deleting the current object is not enough: every
   version and every delete marker has to go.

   ```bash
   cd infra
   export AWS_PROFILE=ferrenafe AWS_REGION=us-east-2

   # Confirm what is about to be deleted. Expect status.json and its versions
   # and nothing else — if anything else is listed, stop and find out why.
   aws s3api list-object-versions --bucket "<SnapshotBucketName>"

   # Delete every version and delete marker, then the bucket itself.
   aws s3 rm "s3://<SnapshotBucketName>" --recursive
   aws s3api list-object-versions --bucket "<SnapshotBucketName>" \
     --query '{Objects: Versions[].{Key:Key,VersionId:VersionId}}' \
     --output json > /tmp/versions.json
   aws s3api delete-objects --bucket "<SnapshotBucketName>" \
     --delete file:///tmp/versions.json

   npx cdk destroy PublicSnapshotStack
   ```

   The stack name is required for the same reason as Steps 2 and 3: a bare
   `cdk destroy` with no stack name acts on every stack `infra/` synthesizes,
   including `ScheduledCycleStack`.

   If `cdk destroy` reports `The bucket you tried to delete is not empty`,
   a delete marker is left; repeat the `list-object-versions` /
   `delete-objects` pair with `DeleteMarkers[]` in place of `Versions[]`.

## What this runbook does not cover

- The Amplify app's reverse-proxy rewrite that fetches `status.json`
  server-side — that configuration lives with the Amplify app, not this
  stack.
- Automating any of the steps above. Every command here is run by hand, once
  per environment, by the owner.
