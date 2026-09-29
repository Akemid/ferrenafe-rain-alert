#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib/core';
import { PublicSnapshotStack } from '../lib/public-snapshot-stack';
import { ScheduledCycleStack } from '../lib/scheduled-cycle-stack';

/**
 * Decides whether — and how — to synthesize `ScheduledCycleStack` (design D25, task 3.24).
 *
 * `snapshotBucketName` gates the *attempt*: it is committed to `cdk.context.json` (see that file), so it
 * is present for every real `cdk synth`/`cdk deploy` but absent by default for a plain `new cdk.App()`
 * (e.g. under `npm test`, which never goes through the `cdk` CLI and therefore never reads
 * `cdk.context.json` or any `-c` flag). Its absence means "this app instance was never given the committed
 * context" and the function returns `undefined` rather than throwing — the existing `PublicSnapshotStack`
 * tests in this file must keep working with no context supplied at all.
 *
 * `snapshotWriterPolicyArn` and `agentRuntimeArn` are never committed — both ARNs embed the twelve-digit
 * account id (`tests/hygiene/test_repo_hygiene.py` refuses that in the repository). Once the attempt is
 * underway (`snapshotBucketName` present), a missing one of these is a required-prop failure, not a
 * default: `cdk deploy ferrenafe-scheduled-cycle` with the flag forgotten fails synth loudly, naming
 * exactly which context key is missing, rather than silently deploying with the wrong ARN or none at all.
 *
 * Exported so `test/app.test.ts` can exercise all three outcomes (absent, throws, synthesizes) with
 * hand-built `App({ context })` instances instead of the one module-level `app` below.
 */
export function buildScheduledCycleStack(app: cdk.App): ScheduledCycleStack | undefined {
  const snapshotBucketName = app.node.tryGetContext('snapshotBucketName') as string | undefined;
  if (snapshotBucketName === undefined) {
    return undefined;
  }

  const snapshotWriterPolicyArn = app.node.tryGetContext('snapshotWriterPolicyArn') as string | undefined;
  if (!snapshotWriterPolicyArn) {
    throw new Error(
      "snapshotWriterPolicyArn context value is required to deploy 'ferrenafe-scheduled-cycle' and is " +
        'never defaulted (design.md D25). Supply it at deploy time — it must never be committed to ' +
        "cdk.context.json, because the ARN embeds the account id: cdk deploy ferrenafe-scheduled-cycle " +
        '-c snapshotWriterPolicyArn=<arn>',
    );
  }

  const agentRuntimeArn = app.node.tryGetContext('agentRuntimeArn') as string | undefined;
  if (!agentRuntimeArn) {
    throw new Error(
      "agentRuntimeArn context value is required to deploy 'ferrenafe-scheduled-cycle' and is never " +
        'defaulted, for the same reason as snapshotWriterPolicyArn: the ARN embeds the account id. ' +
        'Supply it at deploy time: cdk deploy ferrenafe-scheduled-cycle -c agentRuntimeArn=<arn>',
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
 * docstring. Populated for a real `cdk deploy ferrenafe-scheduled-cycle -c snapshotWriterPolicyArn=<arn>
 * -c agentRuntimeArn=<arn>`, which is the only way `snapshotWriterPolicyArn`/`agentRuntimeArn` ever reach
 * this process — never committed.
 */
export const scheduledCycleStack = buildScheduledCycleStack(app);
