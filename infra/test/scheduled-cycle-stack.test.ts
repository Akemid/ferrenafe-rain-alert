import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { App } from 'aws-cdk-lib/core';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { ScheduledCycleStack, ScheduledCycleStackProps } from '../lib/scheduled-cycle-stack';

/** The golden contract this suite reads independently of the stack under test (design D31/D32) — the same
 * file `tests/unit/adapters/test_agentcore_invoker.py::TestScheduledCycleContract` asserts against on the
 * Python side. */
const CONTRACT = JSON.parse(
  readFileSync(join(__dirname, '..', '..', 'contracts', 'scheduled-cycle.json'), 'utf-8'),
) as { agent_read_timeout_seconds: number; cycle_headroom_seconds: number; ssm_path_prefix: string };

/** Fixture ARN, never a plausible twelve-digit account id (design D25's own trap, `tests/hygiene`). */
const SNAPSHOT_WRITER_POLICY_ARN = 'arn:aws:iam::ACCOUNT:policy/SnapshotWriter';
const AGENT_RUNTIME_ARN = 'arn:aws:bedrock-agentcore:us-east-2:ACCOUNT:runtime/example-runtime-abc123';
const SNAPSHOT_BUCKET_NAME = 'ferrenafe-public-snapshot-example';

function synthesizeTemplate(overrides: Partial<ScheduledCycleStackProps> = {}): Template {
  const app = new App();
  const stack = new ScheduledCycleStack(app, 'TestStack', {
    snapshotBucketName: SNAPSHOT_BUCKET_NAME,
    snapshotWriterPolicyArn: SNAPSHOT_WRITER_POLICY_ARN,
    agentRuntimeArn: AGENT_RUNTIME_ARN,
    ...overrides,
  });
  return Template.fromStack(stack);
}

/** Every IAM statement in the synthesized template, following `public-snapshot-stack.test.ts`'s own
 * convention: a walk rather than a list of known resource types, so a statement added by a construct
 * nobody remembered is still seen. */
function everyStatement(template: Template): Array<Record<string, unknown>> {
  const statements: Array<Record<string, unknown>> = [];
  const visit = (node: unknown): void => {
    if (Array.isArray(node)) {
      node.forEach(visit);
      return;
    }
    if (node === null || typeof node !== 'object') return;
    for (const [key, value] of Object.entries(node as Record<string, unknown>)) {
      if (key === 'Statement' && Array.isArray(value)) {
        statements.push(...(value as Array<Record<string, unknown>>));
      }
      visit(value);
    }
  };
  visit(template.toJSON());
  return statements;
}

function actionsOf(statement: Record<string, unknown>): string[] {
  const action = statement.Action;
  if (typeof action === 'string') return [action];
  if (Array.isArray(action)) return action.filter((entry): entry is string => typeof entry === 'string');
  return [];
}

/**
 * The Lambda function's own execution role's inline policy statements — not the Scheduler's separate
 * invocation role (`LambdaInvoke` creates one to call the function, distinct from the function's own
 * permissions to call DynamoDB/SSM/AgentCore), found by following the function's `Role` GetAtt reference
 * rather than assuming there is exactly one role in the template.
 */
function findFunctionRoleStatements(template: Template): Array<Record<string, unknown>> {
  const functions = template.findResources('AWS::Lambda::Function');
  const [fn] = Object.values(functions);
  const roleLogicalId = (fn.Properties.Role as { 'Fn::GetAtt': [string, string] })['Fn::GetAtt'][0];

  const policies = template.findResources('AWS::IAM::Policy');
  const matching = Object.values(policies).filter((policy) => {
    const roles = policy.Properties.Roles as Array<{ Ref: string }>;
    return roles.some((role) => role.Ref === roleLogicalId);
  });
  expect(matching).toHaveLength(1);
  return matching[0].Properties.PolicyDocument.Statement as Array<Record<string, unknown>>;
}

describe('ScheduledCycleStack — the timeout contract (D31)', () => {
  test('env timeout at least the contract read timeout', () => {
    const template = synthesizeTemplate();

    template.hasResourceProperties('AWS::Lambda::Function', {
      Environment: {
        Variables: Match.objectLike({
          RAIN_ALERT_AGENT_TIMEOUT_S: String(CONTRACT.agent_read_timeout_seconds),
        }),
      },
    });
    expect(Number(CONTRACT.agent_read_timeout_seconds)).toBeGreaterThanOrEqual(CONTRACT.agent_read_timeout_seconds);
  });

  test('function timeout exceeds the agent deadline plus headroom', () => {
    const template = synthesizeTemplate();
    const functions = template.findResources('AWS::Lambda::Function');
    const [fn] = Object.values(functions);
    const envTimeout = Number(fn.Properties.Environment.Variables.RAIN_ALERT_AGENT_TIMEOUT_S);
    const functionTimeout = Number(fn.Properties.Timeout);

    expect(functionTimeout).toBeGreaterThan(envTimeout + CONTRACT.cycle_headroom_seconds);
    expect(functionTimeout).toBeLessThanOrEqual(900);
  });
});

describe('ScheduledCycleStack — Lambda role and the imported SnapshotWriter policy (D25)', () => {
  test('lambda role attaches SnapshotWriter and defines no s3:PutObject', () => {
    const template = synthesizeTemplate();

    template.hasResourceProperties('AWS::IAM::Role', {
      ManagedPolicyArns: Match.arrayWith([SNAPSHOT_WRITER_POLICY_ARN]),
    });

    const withPutObject = everyStatement(template).filter((statement) =>
      actionsOf(statement).some((action) => action === 's3:PutObject' || action === 's3:*'),
    );
    expect(withPutObject).toEqual([]);
  });
});

describe('ScheduledCycleStack — DynamoDB table (D26, D27, D34)', () => {
  test('table', () => {
    const template = synthesizeTemplate();

    template.hasResourceProperties('AWS::DynamoDB::Table', {
      TableName: 'ferrenafe-alerts-sent',
      BillingMode: 'PAY_PER_REQUEST',
      TimeToLiveSpecification: { AttributeName: 'expires_at', Enabled: true },
      KeySchema: [
        { AttributeName: 'pk', KeyType: 'HASH' },
        { AttributeName: 'sk', KeyType: 'RANGE' },
      ],
    });
    template.hasResource('AWS::DynamoDB::Table', { DeletionPolicy: 'Retain', UpdateReplacePolicy: 'Retain' });

    const tables = template.findResources('AWS::DynamoDB::Table');
    expect(Object.keys(tables)).toHaveLength(1);
    // No GSI: `GlobalSecondaryIndexes` must be absent from the one table's properties.
    const [tableProps] = Object.values(tables).map((resource) => resource.Properties);
    expect(tableProps.GlobalSecondaryIndexes).toBeUndefined();

    // The Lambda role's grant names this one table, never `*`.
    const statements = findFunctionRoleStatements(template);
    const tableStatements = statements.filter((statement) =>
      actionsOf(statement).some((action) => action.startsWith('dynamodb:')),
    );
    expect(tableStatements.length).toBeGreaterThan(0);
    const wildcardResourceStatements = tableStatements.filter((statement) => statement.Resource === '*');
    expect(wildcardResourceStatements).toEqual([]);
  });
});

describe('ScheduledCycleStack — SSM grant, no parameters created (D32)', () => {
  test('SSM grant', () => {
    const template = synthesizeTemplate();

    const statements = findFunctionRoleStatements(template);
    const ssmStatements = statements.filter((statement) =>
      actionsOf(statement).some((action) => action === 'ssm:GetParameters'),
    );
    expect(ssmStatements).toHaveLength(1);
    const [ssmStatement] = ssmStatements;
    const resources = Array.isArray(ssmStatement.Resource) ? ssmStatement.Resource : [ssmStatement.Resource];
    expect(resources).toHaveLength(7);
    for (const resource of resources) {
      // Each resource is an `Fn::Join` building `arn:...:parameter/ferrenafe/rain-alert/<name>`; assert the
      // literal suffix segment of the join starts with the contract's prefix once the partition/region/
      // account tokens are stripped away — i.e. the joined literal array's last static fragment carries it.
      // The static tail of the `Fn::Join`, e.g. `:parameter/ferrenafe/rain-alert/location`, after the
      // dynamic partition/region/account tokens. Asserts it starts with `contract.ssm_path_prefix`
      // (design.md D32, amended fix round 1) rather than merely containing it, so a name that happens to
      // embed the prefix mid-string would not pass.
      const joined = (resource as { 'Fn::Join': [string, unknown[]] })['Fn::Join'][1] as string[];
      const staticSuffix = joined[joined.length - 1];
      expect(staticSuffix.startsWith(`:parameter${CONTRACT.ssm_path_prefix}`)).toBe(true);
    }

    // The synthesized template creates zero `AWS::SSM::Parameter` resources — CDK owning these would
    // silently restore stale values on an unrelated deploy (D32).
    const parameters = template.findResources('AWS::SSM::Parameter');
    expect(Object.keys(parameters)).toHaveLength(0);
  });
});

describe('ScheduledCycleStack — AgentCore grant (no Bedrock model permission)', () => {
  test('agentcore grant', () => {
    const template = synthesizeTemplate();

    const statements = findFunctionRoleStatements(template);
    const agentcoreStatements = statements.filter((statement) =>
      actionsOf(statement).some((action) => action === 'bedrock-agentcore:InvokeAgentRuntime'),
    );
    expect(agentcoreStatements).toHaveLength(1);
    expect(agentcoreStatements[0].Resource).toBe(AGENT_RUNTIME_ARN);

    const modelStatements = everyStatement(template).filter((statement) =>
      actionsOf(statement).some((action) => action === 'bedrock:InvokeModel'),
    );
    expect(modelStatements).toEqual([]);
  });
});

describe('ScheduledCycleStack — EventBridge Scheduler (D35)', () => {
  test('schedule', () => {
    const template = synthesizeTemplate();

    template.hasResourceProperties('AWS::Scheduler::Schedule', {
      ScheduleExpression: 'cron(0 0,6,12,18 * * ? *)',
      ScheduleExpressionTimezone: 'America/Lima',
      FlexibleTimeWindow: { Mode: 'OFF' },
      State: 'ENABLED',
      Target: Match.objectLike({
        RetryPolicy: {
          MaximumEventAgeInSeconds: 3600,
          MaximumRetryAttempts: 2,
        },
      }),
    });

    template.hasResourceProperties('AWS::Lambda::Function', {
      ReservedConcurrentExecutions: 1,
    });
  });

  test('scheduleEnabled context defaults to ENABLED and produces DISABLED when explicitly disabled', () => {
    const enabledTemplate = synthesizeTemplate({ scheduleEnabled: true });
    enabledTemplate.hasResourceProperties('AWS::Scheduler::Schedule', { State: 'ENABLED' });

    const disabledTemplate = synthesizeTemplate({ scheduleEnabled: false });
    disabledTemplate.hasResourceProperties('AWS::Scheduler::Schedule', { State: 'DISABLED' });

    // No override at all -- the default.
    const defaultTemplate = synthesizeTemplate();
    defaultTemplate.hasResourceProperties('AWS::Scheduler::Schedule', { State: 'ENABLED' });
  });
});

describe('ScheduledCycleStack — Observability (D33)', () => {
  test('alarms and topic', () => {
    const template = synthesizeTemplate();

    const topics = template.findResources('AWS::SNS::Topic');
    const topicLogicalIds = Object.keys(topics);
    expect(topicLogicalIds).toHaveLength(1);
    const [topicLogicalId] = topicLogicalIds;
    const topicArnRef = { Ref: topicLogicalId };

    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      Namespace: 'AWS/Lambda',
      MetricName: 'Errors',
      Statistic: 'Sum',
      Threshold: 1,
      ComparisonOperator: 'GreaterThanOrEqualToThreshold',
      TreatMissingData: 'notBreaching',
      AlarmActions: Match.arrayWith([topicArnRef]),
    });

    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      Namespace: 'AWS/Lambda',
      MetricName: 'Invocations',
      Statistic: 'Sum',
      Threshold: 1,
      ComparisonOperator: 'LessThanThreshold',
      TreatMissingData: 'breaching',
      AlarmActions: Match.arrayWith([topicArnRef]),
    });

    template.hasResourceProperties('AWS::Logs::MetricFilter', {
      FilterPattern: '"[SNAPSHOT NOT PUBLISHED]"',
      MetricTransformations: Match.arrayWith([
        Match.objectLike({ MetricName: 'SnapshotPublishFailures', MetricNamespace: 'FerrenafeRainAlert' }),
      ]),
    });

    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      Namespace: 'FerrenafeRainAlert',
      MetricName: 'SnapshotPublishFailures',
      Threshold: 1,
      ComparisonOperator: 'GreaterThanOrEqualToThreshold',
      AlarmActions: Match.arrayWith([topicArnRef]),
    });

    const alarms = template.findResources('AWS::CloudWatch::Alarm');
    expect(Object.keys(alarms)).toHaveLength(3);

    // No subscription is created — an email address is personal data (`openspec/config.yaml → rules.archive`).
    const subscriptions = template.findResources('AWS::SNS::Subscription');
    expect(Object.keys(subscriptions)).toHaveLength(0);
  });
});
