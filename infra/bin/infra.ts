#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib/core';
import { PublicSnapshotStack } from '../lib/public-snapshot-stack';

const app = new cdk.App();
new PublicSnapshotStack(app, 'PublicSnapshotStack', {
  // Deliberate, explicit stack name so the deploy runbook can reference it
  // unambiguously in `cdk deploy <name>` and in the AWS console, instead of
  // relying on the CDK construct id's default CloudFormation stack naming.
  stackName: 'ferrenafe-public-snapshot',

  /* If you don't specify 'env', this stack will be environment-agnostic.
   * Account/Region-dependent features and context lookups will not work,
   * but a single synthesized template can be deployed anywhere. */

  /* Uncomment the next line to specialize this stack for the AWS Account
   * and Region that are implied by the current CLI configuration. */
  // env: { account: process.env.CDK_DEFAULT_ACCOUNT, region: process.env.CDK_DEFAULT_REGION },

  /* Uncomment the next line if you know exactly what Account and Region you
   * want to deploy the stack to. */
  // env: { account: '<account>', region: 'us-east-2' },

  /* For more information, see https://docs.aws.amazon.com/cdk/latest/guide/environments.html */
});
