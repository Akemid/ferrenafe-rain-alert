#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib/core';
import { PublicSnapshotStack } from '../lib/public-snapshot-stack';
import { ScheduledCycleStack } from '../lib/scheduled-cycle-stack';

/**
 * The committed placeholder for `snapshotBucketName` (see `cdk.json`'s `context` block). Never a real
 * bucket name — `buildScheduledCycleStack` refuses to synthesize against it (fix round 1, SHOULD 5), rather
 * than letting it reach `RAIN_ALERT_SNAPSHOT_BUCKET` truthily and fail every publish with `AccessDenied`
 * while the alert itself still goes out and reports success.
 */
const PLACEHOLDER_SNAPSHOT_BUCKET_NAME = 'ferrenafe-public-snapshot-example';

/**
 * Decides whether — and how — to synthesize `ScheduledCycleStack` (design D25, task 3.24).
 *
 * `snapshotBucketName` gates the *attempt*: it is committed to `cdk.json`'s `context` block (fix round 1,
 * SHOULD 4 — moved off `cdk.context.json`, which is CDK's own auto-written cache and not meant to be
 * hand-authored config), so it is present for every real `cdk synth`/`cdk deploy` but absent by default for
 * a plain `new cdk.App()` (e.g. under `npm test`, which never goes through the `cdk` CLI and therefore
 * never reads `cdk.json`'s context or any `-c` flag). Its absence means "this app instance was never given
 * the committed context" and the function returns `undefined` rather than throwing — the existing
 * `PublicSnapshotStack` tests in this file must keep working with no context supplied at all. Its presence
 * as the still-unreplaced placeholder literal is a separate, louder failure (see below).
 *
 * `snapshotWriterPolicyArn`, `agentRuntimeArn` and `alarmEmail` are never committed — the two ARNs embed
 * the twelve-digit account id (`tests/hygiene/test_repo_hygiene.py` refuses that in the repository), and an
 * email address is personal data (`openspec/config.yaml → rules.archive`). Once the attempt is underway
 * (`snapshotBucketName` present and not the placeholder), a missing one of these three is a required-prop
 * failure, not a default: `cdk deploy ScheduledCycleStack` with a flag forgotten fails synth loudly, naming
 * exactly which context key is missing, rather than silently deploying with the wrong value or none at all.
 *
 * The CLI selects a stack by its **construct id** (`ScheduledCycleStack`, the second argument below), not
 * by the CloudFormation `stackName` property (`ferrenafe-scheduled-cycle`) — `cdk deploy
 * ferrenafe-scheduled-cycle` fails with "No stacks match the name(s)" (verified live, fix round 1, SHOULD
 * 3). Every error message and docstring here uses the construct id.
 *
 * Exported so `test/app.test.ts` can exercise every outcome (absent, throws for each missing/placeholder
 * value, synthesizes) with hand-built `App({ context })` instances instead of the one module-level `app`
 * below.
 */
export function buildScheduledCycleStack(app: cdk.App): ScheduledCycleStack | undefined {
  const snapshotBucketName = app.node.tryGetContext('snapshotBucketName') as string | undefined;
  if (snapshotBucketName === undefined) {
    return undefined;
  }
  if (snapshotBucketName === PLACEHOLDER_SNAPSHOT_BUCKET_NAME) {
    throw new Error(
      "snapshotBucketName in cdk.json's context still holds the placeholder value " +
        `'${PLACEHOLDER_SNAPSHOT_BUCKET_NAME}'. Replace it with the real deployed PublicSnapshotStack ` +
        'bucket name before deploying ScheduledCycleStack — the placeholder would otherwise reach ' +
        'RAIN_ALERT_SNAPSHOT_BUCKET truthily and fail every publish with AccessDenied while the alert ' +
        'itself still goes out and reports success.',
    );
  }

  const snapshotWriterPolicyArn = app.node.tryGetContext('snapshotWriterPolicyArn') as string | undefined;
  if (!snapshotWriterPolicyArn) {
    throw new Error(
      "snapshotWriterPolicyArn context value is required to deploy 'ScheduledCycleStack' and is " +
        'never defaulted (design.md D25). Supply it at deploy time — it must never be committed to ' +
        "cdk.json/cdk.context.json, because the ARN embeds the account id: cdk deploy ScheduledCycleStack " +
        '-c snapshotWriterPolicyArn=<arn>',
    );
  }

  const agentRuntimeArn = app.node.tryGetContext('agentRuntimeArn') as string | undefined;
  if (!agentRuntimeArn) {
    throw new Error(
      "agentRuntimeArn context value is required to deploy 'ScheduledCycleStack' and is never " +
        'defaulted, for the same reason as snapshotWriterPolicyArn: the ARN embeds the account id. ' +
        'Supply it at deploy time: cdk deploy ScheduledCycleStack -c agentRuntimeArn=<arn>',
    );
  }

  const alarmEmail = app.node.tryGetContext('alarmEmail') as string | undefined;
  if (!alarmEmail) {
    throw new Error(
      "alarmEmail context value is required to deploy 'ScheduledCycleStack' and is never defaulted — an " +
        'unsubscribed Alarm 2 is decorative for this design\'s defining silent failure (the cycle stops ' +
        'running). An email address is personal data and must never be committed. Supply it at deploy ' +
        'time: cdk deploy ScheduledCycleStack -c alarmEmail=<address>',
    );
  }

  // `-c scheduleEnabled=false` is the durable disable route (design D34); any other value, or the flag's
  // absence, means enabled. Never a boolean context value — CDK context values from the CLI are strings.
  const scheduleEnabledContext = app.node.tryGetContext('scheduleEnabled') as string | undefined;

  return new ScheduledCycleStack(app, 'ScheduledCycleStack', {
    stackName: 'ferrenafe-scheduled-cycle',
    // Same region as `PublicSnapshotStack` and the AgentCore runtime (design D25).
    env: { region: 'us-east-2' },
    snapshotBucketName,
    snapshotWriterPolicyArn,
    agentRuntimeArn,
    alarmEmail,
    scheduleEnabled: scheduleEnabledContext !== 'false',
  });
}

const app = new cdk.App();

/**
 * Exported so `test/app.test.ts` can assert the deploy target THIS file
 * configures. The stack tests build their own `PublicSnapshotStack` with no
 * `env`, which is right for asserting the template but means every decision
 * below — the region pin above all — was covered by nothing at all.
 * Constructing an app is pure: no credentials, no network, no synth.
 */
export const stack = new PublicSnapshotStack(app, 'PublicSnapshotStack', {
  // Deliberate, explicit stack name so the deploy runbook can reference it
  // unambiguously in `cdk deploy <name>` and in the AWS console, instead of
  // relying on the CDK construct id's default CloudFormation stack naming.
  stackName: 'ferrenafe-public-snapshot',

  // Pinned, not ambient. Left environment-agnostic (the CDK scaffold's
  // default), `cdk deploy` targets whatever AWS_REGION or AWS_PROFILE the
  // shell happens to carry. For a bucket that is public by design, a
  // wrong-region deploy leaves a second world-readable bucket somewhere
  // nobody is looking, and the deploy that was meant to happen appears to
  // have succeeded.
  //
  // us-east-2 is where the AgentCore composer runtime already lives, so the
  // whole application stays in one region. CLAUDE.md still names us-east-1
  // as this project's target; that line predates the AgentCore deploy and
  // `docs/runbooks/public-page-deploy.md` explains the divergence.
  //
  // The account is deliberately left unpinned: writing it here would put a
  // twelve-digit account id in a public repository, which
  // `tests/hygiene/test_repo_hygiene.py` refuses. The deploy runbook uses
  // the `ferrenafe` profile, and `cdk deploy` refuses a region mismatch.
  env: { region: 'us-east-2' },
});

/**
 * `undefined` under `npm test`/`cdk synth` with no context supplied — see `buildScheduledCycleStack`'s own
 * docstring. Populated for a real `cdk deploy ScheduledCycleStack -c snapshotWriterPolicyArn=<arn>
 * -c agentRuntimeArn=<arn> -c alarmEmail=<address>`, which is the only way those three values ever reach
 * this process — never committed.
 */
export const scheduledCycleStack = buildScheduledCycleStack(app);
