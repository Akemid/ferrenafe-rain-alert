# Deploying the public snapshot bucket

Documentation only — nothing here is automated. This deploys the `infra/`
CDK app (task 7 of the `public-status-page` change): one S3 bucket holding
one public object, `status.json`, produced by each rain-alert cycle and read
by the public status page through Amplify's reverse-proxy rewrite.

The bucket, its public-access-block configuration, and its bucket policy are
defined in `infra/lib/public-snapshot-stack.ts`. That file's comments carry
the security reasoning (Amplify's proxy fetches unsigned, so the object must
be publicly readable; the policy is scoped to the single `status.json` key,
never the bucket) — this runbook is the deploy procedure, not a restatement
of that reasoning.

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
  with permission to create S3 buckets, bucket policies, and the
  CDK-managed IAM role and Lambda function that back `autoDeleteObjects`.
- The CDK environment bootstrapped once for this account and region
  (`npx cdk bootstrap aws://<account>/us-east-2` from `infra/`), if not
  already done for another stack in this account.
- Node.js and `npm`, matching `infra/package.json`.

## Step 1 — Install dependencies (already done for this commit)

```bash
cd infra
npm install
```

`infra/package-lock.json` is gitignored (it carries a third-party email
address in a deprecation warning string, same reason as
`agentcore/cdk/package-lock.json`) — `npm install` regenerates it locally.

## Step 2 — Synthesize and review before deploying

```bash
cd infra
AWS_PROFILE=ferrenafe AWS_REGION=us-east-2 npx cdk synth
```

Read the generated template (`cdk.out/PublicSnapshotStack.template.json`)
before deploying anything from it, especially after touching
`public-snapshot-stack.ts`. Confirm in particular that the `s3:GetObject`
statement's `Resource` is still the single `.../status.json` key, not the
whole bucket — a change here is exactly the kind of defect that looks
correct in the source and is not.

## Step 3 — Deploy

```bash
cd infra
AWS_PROFILE=ferrenafe AWS_REGION=us-east-2 npx cdk deploy
```

Confirm the changeset when prompted. On success, the CLI prints two outputs:

- `SnapshotBucketName` — the bucket name, for
  `RAIN_ALERT_SNAPSHOT_BUCKET` (see Step 5).
- `SnapshotObjectUrl` — the full URL to `status.json`, used in Step 4.

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
2. **`cdk destroy`.** `removalPolicy: DESTROY` and `autoDeleteObjects: true`
   on the bucket mean this also deletes `status.json` and the bucket itself,
   with no manual emptying step first:

   ```bash
   cd infra
   AWS_PROFILE=ferrenafe AWS_REGION=us-east-2 npx cdk destroy
   ```

## What this runbook does not cover

- The Amplify app's reverse-proxy rewrite that fetches `status.json`
  server-side — that configuration lives with the Amplify app, not this
  stack.
- Automating any of the steps above. Every command here is run by hand, once
  per environment, by the owner.
