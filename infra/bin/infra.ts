#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib/core';
import { PublicSnapshotStack } from '../lib/public-snapshot-stack';
import { ScheduledCycleStack } from '../lib/scheduled-cycle-stack';

/**
 * Decides whether — and how — to synthesize `ScheduledCycleStack` (design D25, task 3.24).
 *
 * `snapshotBucketName` gates the *attempt*: it is committed to `cdk.json`'s `context` block (fix round 1,
 * SHOULD 4 — moved off `cdk.context.json`, which is CDK's own auto-written cache and not meant to be
 * hand-authored config), so it is present for every real `cdk synth`/`cdk deploy` but absent by default for
 * a plain `new cdk.App()` (e.g. under `npm test`, which never goes through the `cdk` CLI and therefore
 * never reads `cdk.json`'s context or any `-c` flag). Its absence means "this app instance was never given
 * the committed context" and the function returns `undefined` rather than attempting construction — the
 * existing `PublicSnapshotStack` tests in this file must keep working with no context supplied at all.
 *
 * **Fix round 2, MUST 1 — nothing below throws.** `snapshotWriterPolicyArn`, `agentRuntimeArn` and
 * `alarmEmail` are never committed (the two ARNs embed the twelve-digit account id, and an email address is
 * personal data), so once the attempt is underway (`snapshotBucketName` present) a missing one of these
 * three — or a `snapshotBucketName` that is still the committed placeholder — used to `throw` here, before
 * `ScheduledCycleStack` was even constructed. That killed the ENTIRE app-construction process that
 * `PublicSnapshotStack` shares: verified live, `cdk synth PublicSnapshotStack` failed with this stack's own
 * error, which also breaks `docs/runbooks/public-page-deploy.md`'s documented `cdk destroy` emergency
 * teardown for infrastructure that is deployed and serving residents right now. Every one of those checks
 * now lives **inside `ScheduledCycleStack`'s own constructor**, as an `Annotations.of(this).addError(...)`
 * — CDK's mechanism for a synthesis-time problem that fails only the ONE stack that carries the annotation.
 * This function's job is now only to read context and always attempt construction once `snapshotBucketName`
 * is present at all, falling back to an empty string for anything else missing — `ScheduledCycleStack`
 * itself decides whether that is acceptable.
 *
 * The CLI selects a stack by its **construct id** (`ScheduledCycleStack`, the second argument below), not
 * by the CloudFormation `stackName` property (`ferrenafe-scheduled-cycle`) — `cdk deploy
 * ferrenafe-scheduled-cycle` fails with "No stacks match the name(s)" (verified live, fix round 1, SHOULD
 * 3). Every error message and docstring here uses the construct id.
 *
 * Exported so `test/app.test.ts` can exercise every outcome (not attempted at all, constructed with error
 * annotations, constructed clean) with hand-built `App({ context })` instances instead of the one
 * module-level `app` below.
 */
export function buildScheduledCycleStack(app: cdk.App): ScheduledCycleStack | undefined {
  const snapshotBucketName = app.node.tryGetContext('snapshotBucketName') as string | undefined;
  if (snapshotBucketName === undefined) {
    return undefined;
  }

  // Falls back to `''` rather than `undefined` for anything missing: `ScheduledCycleStack`'s constructor
  // treats an empty string the same as an absent context value (both are falsy) and raises its own
  // `Annotations` error, but every downstream construct call in this stack accepts a plain string without
  // throwing synchronously (confirmed live, including `EmailSubscription`) — so synthesis always completes
  // and every misconfiguration is reported at once, not just the first one CDK happens to hit.
  const snapshotWriterPolicyArn = (app.node.tryGetContext('snapshotWriterPolicyArn') as string | undefined) ?? '';
  const agentRuntimeArn = (app.node.tryGetContext('agentRuntimeArn') as string | undefined) ?? '';
  const alarmEmail = (app.node.tryGetContext('alarmEmail') as string | undefined) ?? '';

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
