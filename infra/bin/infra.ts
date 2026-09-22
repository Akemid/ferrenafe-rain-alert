#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib/core';
import { PublicSnapshotStack } from '../lib/public-snapshot-stack';

const app = new cdk.App();
new PublicSnapshotStack(app, 'PublicSnapshotStack', {
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
