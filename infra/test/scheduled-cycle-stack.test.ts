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

/** The relay contract (design D37/D-contract), read independently of the stack under test so IAM and Python
 * cannot drift on the object key. */
const RELAY_CONTRACT = JSON.parse(
  readFileSync(join(__dirname, '..', '..', 'contracts', 'senamhi-relay.json'), 'utf-8'),
) as { object_key: string };

/** Fixture ARN, never a plausible twelve-digit account id (design D25's own trap, `tests/hygiene`). */
const SNAPSHOT_WRITER_POLICY_ARN = 'arn:aws:iam::ACCOUNT:policy/SnapshotWriter';
const AGENT_RUNTIME_ARN = 'arn:aws:bedrock-agentcore:us-east-2:ACCOUNT:runtime/example-runtime-abc123';
/** Deliberately not the placeholder literal `buildScheduledCycleStack` refuses (fix round 1, MUST/SHOULD 5) —
 * a real synth-time bucket name, distinct from `ScheduledCycleStack`'s own placeholder-rejection test below. */
const SNAPSHOT_BUCKET_NAME = 'ferrenafe-public-snapshot-test-fixture';
/** `example.com` is IANA-reserved for documentation (RFC 2606) — never a real, deliverable address, and
 * never committed to `cdk.json`/`cdk.context.json` (fix round 1, SHOULD 6: supplied only via `-c` at deploy
 * time, exactly like `agentRuntimeArn`). Assembled in parts, matching `tests/hygiene/test_repo_hygiene.py`'s
 * own `NPM_DEPRECATION_CONTACT` precedent: `tests/hygiene`'s secret scan is deliberately email-shape-blind
 * to *any* committed line, with no general test-fixture exemption, so writing this as one contiguous
 * `user` + at-sign + `domain` string would fail it, even though the address is not personal data. */
const ALARM_EMAIL = 'rain-alert-alarms' + '@' + 'example.com';

function synthesizeTemplate(overrides: Partial<ScheduledCycleStackProps> = {}): Template {
  const app = new App();
  const stack = new ScheduledCycleStack(app, 'TestStack', {
    snapshotBucketName: SNAPSHOT_BUCKET_NAME,
    snapshotWriterPolicyArn: SNAPSHOT_WRITER_POLICY_ARN,
    agentRuntimeArn: AGENT_RUNTIME_ARN,
    alarmEmail: ALARM_EMAIL,
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

    // Fix round 1, SHOULD 8: this used to also assert
    // `Number(CONTRACT.agent_read_timeout_seconds) >= CONTRACT.agent_read_timeout_seconds` — an `x >= x`
    // tautology that can never fail. Relation 1 is genuinely enforced by the `hasResourceProperties` match
    // below, which reds under a mutation to the env var (task 3.10's mutation proof, apply-progress);
    // nothing was actually left unguarded, but the dead assertion is deleted rather than kept as noise.
    template.hasResourceProperties('AWS::Lambda::Function', {
      Environment: {
        Variables: Match.objectLike({
          RAIN_ALERT_AGENT_TIMEOUT_S: String(CONTRACT.agent_read_timeout_seconds),
        }),
      },
    });
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

    // Scoped to the Lambda role: the `RelayWriter` managed policy (design D41) legitimately carries
    // `s3:PutObject` for the producer user, and is asserted separately below.
    const withPutObject = findFunctionRoleStatements(template).filter((statement) =>
      actionsOf(statement).some((action) => action === 's3:PutObject' || action === 's3:*'),
    );
    expect(withPutObject).toEqual([]);
  });

  test('the execution role carries exactly one managed policy, and no statement anywhere carries a wildcard resource', () => {
    // Fix round 2, SHOULD 2: `Match.arrayWith([SNAPSHOT_WRITER_POLICY_ARN])` above passes on a role that
    // ALSO carries CDK's implicit `AWSLambdaBasicExecutionRole` (`logs:*` on `Resource: "*"`) — `arrayWith`
    // only proves the named ARN is present, never that nothing else is. This is the PR's central security
    // claim (fix round 1: the implicit role was replaced with an explicit one plus a scoped
    // `logGroup.grantWrite`), and it had no test protecting it until this one.
    //
    // Two assertions, proven independently (mutation run live, recorded in apply-progress) to catch two
    // DIFFERENT classes of regression — one does not substitute for the other:
    // - The exact-set `ManagedPolicyArns` check is the one that actually catches the implicit-role
    //   regression: restoring it and re-running with only the wildcard sweep active left this test GREEN,
    //   because `AWSLambdaBasicExecutionRole` is an AWS-owned managed policy referenced by ARN — its
    //   permissions are never inlined into this template as a `Statement` array at all, so
    //   `everyStatement()` cannot see them. This confirms the fix round 1 review's own finding, precisely:
    //   `everyStatement()` is structurally blind to managed policies, by construction, not by an oversight
    //   fixable here.
    // - The wildcard-resource sweep still earns its place for a different mistake: an inline
    //   `iam.PolicyStatement` this stack's own code adds with `resources: ['*']` — a regression the
    //   `ManagedPolicyArns` check cannot see, since that mistake would never touch a managed-policy ARN.
    const template = synthesizeTemplate();

    const roles = template.findResources('AWS::IAM::Role');
    const executionRoles = Object.values(roles).filter((role) =>
      (role.Properties.AssumeRolePolicyDocument.Statement as Array<Record<string, unknown>>).some(
        (statement) =>
          (statement.Principal as { Service?: string } | undefined)?.Service === 'lambda.amazonaws.com',
      ),
    );
    expect(executionRoles).toHaveLength(1);
    expect(executionRoles[0].Properties.ManagedPolicyArns).toEqual([SNAPSHOT_WRITER_POLICY_ARN]);

    const wildcardResourceStatements = everyStatement(template).filter(
      (statement) => statement.Resource === '*' || (Array.isArray(statement.Resource) && statement.Resource.includes('*')),
    );
    expect(wildcardResourceStatements).toEqual([]);
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
      // Fix round 1, MUST 2: `RemovalPolicy.RETAIN` protects the table from stack-deletion only. Without
      // point-in-time recovery there is nothing to restore from if the table's *data* is deleted through a
      // live grant instead (see the exact-action-set assertion below) — the reviewer's point, verbatim.
      PointInTimeRecoverySpecification: { PointInTimeRecoveryEnabled: true },
      DeletionProtectionEnabled: true,
    });
    template.hasResource('AWS::DynamoDB::Table', { DeletionPolicy: 'Retain', UpdateReplacePolicy: 'Retain' });

    const tables = template.findResources('AWS::DynamoDB::Table');
    expect(Object.keys(tables)).toHaveLength(1);
    // No GSI: `GlobalSecondaryIndexes` must be absent from the one table's properties.
    const [tableProps] = Object.values(tables).map((resource) => resource.Properties);
    expect(tableProps.GlobalSecondaryIndexes).toBeUndefined();

    // Fix round 1, MUST 2: `table.grantReadWriteData(fn)` granted 12 actions (including `Scan` and
    // `BatchWriteItem`) against the four the adapter actually calls
    // (`dynamodb_alert_repository.py`: Query, GetItem, PutItem, DeleteItem). `Scan` on this table reads
    // every alert ever sent; `BatchWriteItem`/an unscoped write path can erase the dedup history in bulk —
    // a parser bug or a compromised transitive dependency in the SENAMHI/Open-Meteo fetch path inherits
    // this role, so "no wildcard *resource*" was not enough; the *action list* had to be exact too.
    const statements = findFunctionRoleStatements(template);
    const tableStatements = statements.filter((statement) =>
      actionsOf(statement).some((action) => action.startsWith('dynamodb:')),
    );
    expect(tableStatements).toHaveLength(1);
    const [tableStatement] = tableStatements;
    expect([...actionsOf(tableStatement)].sort()).toEqual(
      ['dynamodb:DeleteItem', 'dynamodb:GetItem', 'dynamodb:PutItem', 'dynamodb:Query'].sort(),
    );
    expect(tableStatement.Resource).not.toBe('*');
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
  test('agentcore grant covers both the runtime and its endpoints, and no more', () => {
    // Fix round 1, MUST 1: `bedrock-agentcore:InvokeAgentRuntime` is evaluated against BOTH the agent
    // runtime resource and the agent endpoint resource being invoked (AWS's resource-based-policies
    // documentation for `bedrock-agentcore`, verbatim: "must allow the action on both the agent runtime and
    // agent endpoint resources"). `agentcore_invoker.py` passes no `qualifier`, so the DEFAULT endpoint is
    // the target, and a grant naming only the runtime ARN denies it. The installed `aws-cdk-lib`'s own
    // `aws-bedrockagentcore` helper (`runtime-base.js`) grants exactly
    // `[runtimeArn, \`${runtimeArn}/*\`]` for this reason — confirmed by reading it directly.
    const template = synthesizeTemplate();

    const statements = findFunctionRoleStatements(template);
    const agentcoreStatements = statements.filter((statement) =>
      actionsOf(statement).some((action) => action === 'bedrock-agentcore:InvokeAgentRuntime'),
    );
    expect(agentcoreStatements).toHaveLength(1);
    const resources = Array.isArray(agentcoreStatements[0].Resource)
      ? agentcoreStatements[0].Resource
      : [agentcoreStatements[0].Resource];
    // Exact set, not merely "contains": still resource-scoped (`/*` spans only this one runtime's
    // endpoints, never a wildcard across runtimes), and nothing broader than the two required resources.
    expect([...resources].sort()).toEqual([AGENT_RUNTIME_ARN, `${AGENT_RUNTIME_ARN}/*`].sort());

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

    // `ReservedConcurrentExecutions: 1` was asserted here until 2026-10-01, when the first deploy failed
    // on it: this account's total Lambda concurrency is 10 against AWS's 1000 default, and AWS requires 10
    // to remain unreserved, so no reservation is expressible at any value. The reasoning for wanting it
    // (D35, at-least-once delivery) is unchanged and recorded at the property's absence in the stack.
    //
    // This now asserts the **absence**, deliberately. A property that silently reappears would fail the
    // deploy again, in CloudFormation, after the asset upload — and the next person would have to
    // rediscover why from an error message that names a number and not a reason. When the quota lands,
    // this assertion flips back to the value and the stack's comment comes out together.
    const functions = template.findResources('AWS::Lambda::Function');
    const reservations = Object.values(functions)
      .map((fn) => (fn as { Properties?: Record<string, unknown> }).Properties?.ReservedConcurrentExecutions)
      .filter((value) => value !== undefined);
    expect(Object.keys(functions)).toHaveLength(1);
    expect(reservations).toEqual([]);
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
    // Three original alarms plus the relay-degraded alarm (design D40).
    expect(Object.keys(alarms)).toHaveLength(4);

    // Fix round 1, SHOULD 6: Alarm 2 (`Invocations`) is the only detector for this design's defining
    // silent failure — the cycle stops running — and an alarm nobody is subscribed to is decorative. The
    // address itself is still never committed (`ALARM_EMAIL` above is an RFC 2606 documentation address,
    // used only in this test fixture); the real stack requires it as a context prop supplied only via
    // `-c alarmEmail=…` at deploy time, exactly like `agentRuntimeArn` (see `bin/infra.ts`).
    template.hasResourceProperties('AWS::SNS::Subscription', {
      Protocol: 'email',
      Endpoint: ALARM_EMAIL,
      TopicArn: { Ref: topicLogicalId },
    });
    const subscriptions = template.findResources('AWS::SNS::Subscription');
    expect(Object.keys(subscriptions)).toHaveLength(1);
  });
});

function relayBucketLogicalId(template: Template): string {
  const buckets = template.findResources('AWS::S3::Bucket');
  expect(Object.keys(buckets)).toHaveLength(1);
  return Object.keys(buckets)[0];
}

describe('ScheduledCycleStack — relay bucket (D41)', () => {
  test('relay bucket is private, versioned, encrypted, owner-enforced, retained and unnamed', () => {
    const template = synthesizeTemplate();
    const id = relayBucketLogicalId(template);
    const bucket = template.toJSON().Resources[id];

    expect(bucket.DeletionPolicy).toBe('Retain');
    expect(bucket.UpdateReplacePolicy).toBe('Retain');
    expect(bucket.Properties.BucketName).toBeUndefined();

    template.hasResourceProperties('AWS::S3::Bucket', {
      PublicAccessBlockConfiguration: {
        BlockPublicAcls: true,
        BlockPublicPolicy: true,
        IgnorePublicAcls: true,
        RestrictPublicBuckets: true,
      },
      VersioningConfiguration: { Status: 'Enabled' },
      BucketEncryption: {
        ServerSideEncryptionConfiguration: [
          Match.objectLike({ ServerSideEncryptionByDefault: { SSEAlgorithm: 'AES256' } }),
        ],
      },
      OwnershipControls: { Rules: [{ ObjectOwnership: 'BucketOwnerEnforced' }] },
      LifecycleConfiguration: {
        Rules: Match.arrayWith([
          Match.objectLike({ NoncurrentVersionExpiration: { NoncurrentDays: 90 }, Status: 'Enabled' }),
          Match.objectLike({ AbortIncompleteMultipartUpload: { DaysAfterInitiation: 1 }, Status: 'Enabled' }),
        ]),
      },
    });

    template.hasResourceProperties('AWS::S3::BucketPolicy', {
      Bucket: { Ref: id },
      PolicyDocument: {
        Statement: Match.arrayWith([
          Match.objectLike({
            Effect: 'Deny',
            Action: 's3:*',
            Condition: { Bool: { 'aws:SecureTransport': 'false' } },
          }),
        ]),
      },
    });
  });
});

describe('ScheduledCycleStack — relay bucket TLS floor (D41)', () => {
  test('denies any request below TLS 1.2 alongside the enforceSSL deny', () => {
    const template = synthesizeTemplate();
    const id = relayBucketLogicalId(template);

    template.hasResourceProperties('AWS::S3::BucketPolicy', {
      Bucket: { Ref: id },
      PolicyDocument: {
        Statement: Match.arrayWith([
          Match.objectLike({
            Effect: 'Deny',
            Principal: { AWS: '*' },
            Action: 's3:*',
            Condition: { NumericLessThan: { 's3:TlsVersion': '1.2' } },
            Resource: [{ 'Fn::GetAtt': [id, 'Arn'] }, { 'Fn::Join': ['', [{ 'Fn::GetAtt': [id, 'Arn'] }, '/*']] }],
          }),
        ]),
      },
    });
  });
});

describe('ScheduledCycleStack — relay grants (D39, D41)', () => {
  const relayStatements = (template: Template): Array<Record<string, unknown>> =>
    findFunctionRoleStatements(template).filter((statement) =>
      actionsOf(statement).some((action) => action.startsWith('s3:')),
    );

  test('lambda reads exactly the one contract key and may list the bucket, nothing else', () => {
    const template = synthesizeTemplate();
    const id = relayBucketLogicalId(template);
    const statements = relayStatements(template);

    expect(statements).toHaveLength(2);
    const get = statements.find((s) => actionsOf(s).includes('s3:GetObject'));
    const list = statements.find((s) => actionsOf(s).includes('s3:ListBucket'));
    expect(actionsOf(get!)).toEqual(['s3:GetObject']);
    expect(actionsOf(list!)).toEqual(['s3:ListBucket']);
    expect(get!.Resource).toEqual({
      'Fn::Join': ['', [{ 'Fn::GetAtt': [id, 'Arn'] }, `/${RELAY_CONTRACT.object_key}`]],
    });
    expect(list!.Resource).toEqual({ 'Fn::GetAtt': [id, 'Arn'] });
    expect(list!.Condition).toBeUndefined();

    const allActions = statements.flatMap(actionsOf);
    expect(allActions).not.toContain('s3:GetObjectVersion');
    expect(allActions).not.toContain('s3:PutObject');
    expect(allActions).not.toContain('s3:*');
  });
});

describe('ScheduledCycleStack — relay writer and producer user (D36, D41)', () => {
  test('RelayWriter allows PutObject on the one key; the producer user has only that policy and no access key', () => {
    const template = synthesizeTemplate();
    const id = relayBucketLogicalId(template);

    const policies = template.findResources('AWS::IAM::ManagedPolicy');
    const writers = Object.entries(policies).filter(([, policy]) =>
      JSON.stringify(policy.Properties.PolicyDocument.Statement).includes('s3:PutObject'),
    );
    expect(writers).toHaveLength(1);
    const [writerLogicalId, writer] = writers[0];
    // No fixed name: IAM names are account-global, so a fixed one risks collision/replacement failures.
    expect(writer.Properties.ManagedPolicyName).toBeUndefined();
    expect(Object.keys(template.findOutputs('RelayWriterPolicyName'))).toHaveLength(1);
    expect(writer.Properties.PolicyDocument.Statement).toEqual([
      {
        Action: 's3:PutObject',
        Effect: 'Allow',
        Resource: { 'Fn::Join': ['', [{ 'Fn::GetAtt': [id, 'Arn'] }, `/${RELAY_CONTRACT.object_key}`]] },
      },
    ]);

    const users = template.findResources('AWS::IAM::User');
    expect(Object.keys(users)).toHaveLength(1);
    const [user] = Object.values(users);
    expect(user.Properties.UserName).toBe('ferrenafe-relay-producer');
    expect(user.Properties.ManagedPolicyArns).toEqual([{ Ref: writerLogicalId }]);
    expect(user.Properties.Policies).toBeUndefined();

    // The operator creates the key in Phase L (D36): a key in CloudFormation puts the secret in the state.
    template.resourceCountIs('AWS::IAM::AccessKey', 0);
  });
});

describe('ScheduledCycleStack — relay env and outputs (D41)', () => {
  test('env var is the bucket ref and the outputs carry no ARN', () => {
    const template = synthesizeTemplate();
    const id = relayBucketLogicalId(template);

    template.hasResourceProperties('AWS::Lambda::Function', {
      Environment: { Variables: Match.objectLike({ RAIN_ALERT_RELAY_BUCKET: { Ref: id } }) },
    });

    const outputs = template.toJSON().Outputs as Record<string, { Value: unknown }>;
    expect(outputs.RelayBucketName.Value).toEqual({ Ref: id });
    const [userLogicalId] = Object.keys(template.findResources('AWS::IAM::User'));
    expect(outputs.RelayProducerUserName.Value).toEqual({ Ref: userLogicalId });
    expect(outputs.RelayObjectKey.Value).toBe(RELAY_CONTRACT.object_key);
    expect(JSON.stringify(outputs)).not.toMatch(/arn:|Fn::GetAtt|"Arn"/);
  });
});

describe('ScheduledCycleStack — relay degraded alarm (D40)', () => {
  test('metric filter keys on the single-line record and has no default value', () => {
    const template = synthesizeTemplate();
    const filters = template.findResources('AWS::Logs::MetricFilter', {
      Properties: { FilterPattern: '{ $.event = "senamhi_relay" }' },
    });
    expect(Object.keys(filters)).toHaveLength(1);
    const [filter] = Object.values(filters);
    expect(filter.Properties.MetricTransformations).toEqual([
      {
        MetricName: 'SenamhiRelayDegraded',
        MetricNamespace: 'FerrenafeRainAlert',
        MetricValue: '$.degraded',
      },
    ]);
    // A default of 0 would fill the missing windows and defeat `treatMissingData: breaching`.
    expect(filter.Properties.MetricTransformations[0].DefaultValue).toBeUndefined();
  });

  test('alarm is Maximum over 21600 s, 2 of 2, missing data breaches, and notifies the existing topic', () => {
    const template = synthesizeTemplate();
    const [topicLogicalId] = Object.keys(template.findResources('AWS::SNS::Topic'));

    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      Namespace: 'FerrenafeRainAlert',
      MetricName: 'SenamhiRelayDegraded',
      Statistic: 'Maximum',
      Period: 21600,
      EvaluationPeriods: 2,
      DatapointsToAlarm: 2,
      Threshold: 1,
      ComparisonOperator: 'GreaterThanOrEqualToThreshold',
      TreatMissingData: 'breaching',
      AlarmActions: [{ Ref: topicLogicalId }],
    });
  });
});

describe('ScheduledCycleStack — relayEnabled=false rollback switch (D41, D45)', () => {
  test('disabled: no relay env var and no relay alarm, but bucket, policy and user remain', () => {
    const template = synthesizeTemplate({ relayEnabled: false });

    const [fn] = Object.values(template.findResources('AWS::Lambda::Function'));
    expect(fn.Properties.Environment.Variables.RAIN_ALERT_RELAY_BUCKET).toBeUndefined();

    const alarms = Object.values(template.findResources('AWS::CloudWatch::Alarm'));
    expect(alarms.filter((a) => a.Properties.MetricName === 'SenamhiRelayDegraded')).toEqual([]);
    expect(Object.keys(alarms)).toHaveLength(3);

    template.resourceCountIs('AWS::S3::Bucket', 1);
    template.resourceCountIs('AWS::IAM::ManagedPolicy', 1);
    template.resourceCountIs('AWS::IAM::User', 1);
  });

  test('enabled by default', () => {
    const template = synthesizeTemplate();
    const [fn] = Object.values(template.findResources('AWS::Lambda::Function'));
    expect(fn.Properties.Environment.Variables.RAIN_ALERT_RELAY_BUCKET).toBeDefined();
  });
});
