# `infra/` — the public snapshot bucket

The CDK app for one stack, `PublicSnapshotStack` (CloudFormation stack name
`ferrenafe-public-snapshot`). It creates the S3 bucket that holds the single
object the public status page reads.

**Nothing here has been deployed.** The procedure of record is
[`docs/runbooks/public-page-deploy.md`](../docs/runbooks/public-page-deploy.md),
and it is run by hand, once, by the owner. Read it before running any command
in this directory that talks to AWS.

## What the stack creates

| Resource | Why |
|---|---|
| An S3 bucket, versioned | Holds `status.json`, one object, rewritten by each rain-alert cycle. Versioning is what makes a bad snapshot recoverable. |
| A bucket policy | Anonymous `s3:GetObject`, scoped to the key `status.json` and to nothing else. |
| A public-access-block configuration | ACLs blocked; public *policies* allowed, which is what makes the line above take effect. |
| An IAM managed policy | `s3:PutObject` on `status.json`. The write grant, defined here rather than invented at the console later. |

Three outputs: `SnapshotBucketName`, `SnapshotObjectUrl` and
`SnapshotWriterPolicyArn`.

## The public-read decision

Amplify's reverse proxy fetches the object **server-side and unsigned**. The
rewrite hides the bucket from the browser; it does not authenticate anything.
So the object has to be readable by anyone who knows its URL, and the bucket's
Block Public Access has to be relaxed enough to let a bucket policy say so.

That is acceptable here for one reason and one reason only: this bucket holds
exactly one file, and that file's entire contents are what the page already
displays to the public. Nothing else ever goes in it.

Two consequences follow, and both are load-bearing:

- **The grant is per key, never per bucket.** `s3:GetObject` on
  `<bucket>/status.json`. Not the bucket, not `/*`. The deploy runbook's
  Step 4 verifies that a bucket *listing* returns 403.
- **Nothing in this stack may hold `s3:PutBucketPolicy`.** With
  `blockPublicPolicy: false`, S3 accepts whatever policy such a principal
  writes — including one granting `s3:PutObject` to `*`, which would hand an
  attacker control of what every resident reads. `autoDeleteObjects: true`
  granted exactly that to a CDK-managed Lambda and was removed; emptying the
  bucket before `cdk destroy` is a manual step in the runbook instead.

`infra/test/public-snapshot-stack.test.ts` asserts all three properties
against the synthesized template. It runs in-process — no credentials, no
network, no deploy.

## The region is pinned

`infra/bin/infra.ts` pins `region: 'us-east-2'`. Left environment-agnostic,
`cdk deploy` targets whatever `AWS_REGION` or `AWS_PROFILE` the shell happens
to carry, and a wrong-region deploy of a deliberately world-readable bucket
leaves one somewhere nobody is watching while appearing to have succeeded.

`us-east-2` is where the AgentCore composer runtime already lives. `CLAUDE.md`
still names `us-east-1`; that line predates the AgentCore deploy, and the
runbook explains the divergence.

The **account** is deliberately not pinned: a twelve-digit account id in a
public repository is what `tests/hygiene/test_repo_hygiene.py` exists to
refuse.

## Commands

All run from `infra/`.

| Command | What it does |
|---|---|
| `npm ci` | Install the exact tree in `package-lock.json`, which is committed. |
| `npm test` | Jest over the synthesized template. No AWS access. |
| `npx tsc --noEmit` | Type-check. Part of the verification gate. |
| `npx cdk synth` | Emit the CloudFormation template to `cdk.out/`. Local; no AWS access. |
| `npx cdk diff` | Compare the deployed stack with this source. **Talks to AWS.** |
| `npx cdk deploy` | Create or update the stack. **Talks to AWS.** Follow the runbook, not this table. |
| `npx cdk destroy` | Delete the stack. The bucket must be emptied by hand first — see the runbook's rollback section. |
