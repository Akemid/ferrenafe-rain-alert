import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { Stack, StackProps, RemovalPolicy, Duration, TimeZone } from 'aws-cdk-lib/core';
import { Construct } from 'constructs';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as cw_actions from 'aws-cdk-lib/aws-cloudwatch-actions';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as scheduler from 'aws-cdk-lib/aws-scheduler';
import * as scheduler_targets from 'aws-cdk-lib/aws-scheduler-targets';

/**
 * The golden contract this stack shares with the Python side
 * (`src/rain_alert/adapters/agentcore_invoker.py`,
 * `src/rain_alert/adapters/ssm_config_repository.py`), per design D31/D32.
 * Read once, at synth time — never hand-copied.
 */
interface ScheduledCycleContract {
  readonly schema_version: number;
  readonly agent_read_timeout_seconds: number;
  readonly cycle_headroom_seconds: number;
  readonly ssm_path_prefix: string;
}

const CONTRACT: ScheduledCycleContract = JSON.parse(
  readFileSync(join(__dirname, '..', '..', 'contracts', 'scheduled-cycle.json'), 'utf-8'),
) as ScheduledCycleContract;

/** design.md D26. Matches `dynamodb_alert_repository.py::TABLE_NAME` by string, not by import — the two
 * languages agree by matching this literal, the same pattern `SSM_PATH_PREFIX` uses on the SSM side. */
export const TABLE_NAME = 'ferrenafe-alerts-sent';

/** The seven exact SSM parameter names this Lambda's role may read (design.md D32, amended fix round 1:
 * `GetParameters` on seven exact names, never a `GetParametersByPath` wildcard over the prefix). Must stay
 * in lockstep with `ssm_config_repository.py::_PARAMETER_NAMES`. */
const SSM_PARAMETER_SUFFIXES = [
  'location',
  'thresholds',
  'checklist',
  'active-channel',
  'composer',
  'forecast-hours',
  'dedup-lookback-hours',
] as const;

export interface ScheduledCycleStackProps extends StackProps {
  /** committed to `cdk.context.json` — no account id in it (design D25). */
  readonly snapshotBucketName: string;
  /** supplied only at deploy time via `-c snapshotWriterPolicyArn=…` — never committed, the ARN embeds the
   * account id (design D25). */
  readonly snapshotWriterPolicyArn: string;
  /** supplied only at deploy time via `-c agentRuntimeArn=…` — never committed, for the same reason as
   * `snapshotWriterPolicyArn`: an AgentCore runtime ARN embeds the account id. */
  readonly agentRuntimeArn: string;
  /**
   * Whether the schedule is enabled on deploy (design D34's durable disable route:
   * `cdk deploy ferrenafe-scheduled-cycle -c scheduleEnabled=false`).
   * @default true
   */
  readonly scheduleEnabled?: boolean;
}

/**
 * `ferrenafe-scheduled-cycle` — EventBridge Scheduler → Lambda → SSM + DynamoDB, composing with the
 * deployed AgentCore runtime (design.md D25–D35). `PublicSnapshotStack` is untouched by this stack; the
 * `SnapshotWriter` managed policy crosses as a required prop and is attached, never re-granted (D25).
 *
 * Synth-only in this change: no `cdk deploy` runs from `sdd-apply`. Every assertion in
 * `infra/test/scheduled-cycle-stack.test.ts` is against the template `Template.fromStack` produces.
 */
export class ScheduledCycleStack extends Stack {
  constructor(scope: Construct, id: string, props: ScheduledCycleStackProps) {
    super(scope, id, props);

    // --- DynamoDB (D26, D27, D34) ---
    const table = new dynamodb.Table(this, 'AlertsTable', {
      tableName: TABLE_NAME,
      partitionKey: { name: 'pk', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'sk', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      timeToLiveAttribute: 'expires_at',
      // Explicit `tableName`, per D26/D34: it is what makes `cdk import` possible after a `cdk destroy`
      // that leaves the table behind (RemovalPolicy.RETAIN on a stateful resource, per the CDK skill).
      removalPolicy: RemovalPolicy.RETAIN,
    });

    // --- CloudWatch Logs (D33) ---
    const logGroup = new logs.LogGroup(this, 'FunctionLogs', {
      // The proposal flags never-expire as a cost that grows silently.
      retention: logs.RetentionDays.ONE_MONTH,
      // The log group is not this design's audit trail — the table (RETAIN) and the versioned snapshot
      // bucket are. Destroying it on stack teardown costs nothing this design depends on.
      removalPolicy: RemovalPolicy.DESTROY,
    });

    // --- Lambda (D30, D31) ---
    const fn = new lambda.Function(this, 'ScheduledCycleFunction', {
      functionName: 'ferrenafe-rain-alert-cycle',
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      handler: 'rain_alert.entrypoints.lambda_handler.handler',
      code: lambda.Code.fromAsset(join(__dirname, '..', 'build', 'lambda')),
      // A deliberate, operator-visible literal — NOT derived from the contract file (D31's "why not
      // derive"). Deriving it would make Relation 2's assertion tautological.
      timeout: Duration.seconds(120),
      // Chosen for import speed (boto3 client construction dominates cold start), not working set —
      // budgeted inside D31's headroom, not treated as free (to verify at the first live `REPORT` line).
      memorySize: 512,
      // EventBridge Scheduler delivery is at-least-once; two concurrent cycles would both read an empty
      // dedup history and both send. This throttles the second one so its retry sees what the first wrote
      // (D35).
      reservedConcurrentExecutions: 1,
      logGroup,
      environment: {
        RAIN_ALERT_AGENT_RUNTIME_ARN: props.agentRuntimeArn,
        // Relation 1 (D31): the one value that crosses the Python/TypeScript boundary, read from the same
        // golden contract file `agentcore_invoker.py`'s own test asserts against. Not a hand-copied number.
        RAIN_ALERT_AGENT_TIMEOUT_S: String(CONTRACT.agent_read_timeout_seconds),
        RAIN_ALERT_SNAPSHOT_BUCKET: props.snapshotBucketName,
      },
    });

    // --- IAM grants (D25, D26, D32, and the AgentCore grant) ---

    // The imported SnapshotWriter policy, ATTACHED — never re-granted with a fresh s3:PutObject statement
    // of this stack's own (D25). `fromManagedPolicyArn` performs no ARN parsing that would reject a test
    // placeholder (confirmed against the installed aws-cdk-lib source, task 3.2).
    fn.role!.addManagedPolicy(
      iam.ManagedPolicy.fromManagedPolicyArn(this, 'SnapshotWriterPolicy', props.snapshotWriterPolicyArn),
    );

    // One table, named, never `*` (D26).
    table.grantReadWriteData(fn);

    // Seven exact parameter ARNs, never a prefix wildcard (D32, amended fix round 1 — the Python side
    // moved from `GetParametersByPath` to `GetParameters` over exact names for the same reason: a wildcard
    // grant would silently cover anything an operator later parks under the same prefix).
    const ssmParameterArns = SSM_PARAMETER_SUFFIXES.map((suffix) =>
      this.formatArn({
        service: 'ssm',
        resource: 'parameter',
        resourceName: `${CONTRACT.ssm_path_prefix}${suffix}`.replace(/^\//, ''),
      }),
    );
    fn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['ssm:GetParameters'],
        resources: ssmParameterArns,
      }),
    );

    // The one AgentCore runtime ARN, no wildcard, and deliberately no `bedrock:InvokeModel` statement
    // anywhere on this role — this Lambda calls the data-plane operation only, never a foundation model
    // directly.
    fn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['bedrock-agentcore:InvokeAgentRuntime'],
        resources: [props.agentRuntimeArn],
      }),
    );

    // --- EventBridge Scheduler (D35) ---
    const target = new scheduler_targets.LambdaInvoke(fn, {
      // Scheduler's own defaults are far too forgiving for a six-hourly job: 185 attempts over 24 hours
      // (confirmed against the installed aws-cdk-lib source, task 3.1/3.4) — a stale retry landing five
      // hours later would publish a snapshot stamped with an old cycle. An event older than an hour is
      // dropped; the next cycle is an hour away.
      maxEventAge: Duration.hours(1),
      retryAttempts: 2,
    });

    new scheduler.Schedule(this, 'Schedule', {
      // Not `rate(6 hours)`: a rate schedule anchors to creation time and re-anchors on some updates, so
      // publish times drift (D35).
      schedule: scheduler.ScheduleExpression.cron({
        minute: '0',
        hour: '0,6,12,18',
        day: '*',
        month: '*',
        // `weekDay` deliberately omitted: CDK's `CronOptions` rejects supplying both `day` and `weekDay`,
        // and the absence of one implies the correct `?` for the other, producing the intended
        // `cron(0 0,6,12,18 * * ? *)`.
        timeZone: TimeZone.AMERICA_LIMA,
      }),
      target,
      timeWindow: scheduler.TimeWindow.off(),
      enabled: props.scheduleEnabled ?? true,
    });

    // --- Observability (D33) ---

    const errorsAlarm = new cloudwatch.Alarm(this, 'ErrorsAlarm', {
      alarmDescription: 'The scheduled cycle raised — an exception propagated out of the handler.',
      metric: fn.metric('Errors', { period: Duration.minutes(5), statistic: 'Sum' }),
      threshold: 1,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      // Only meaningful because the handler catches nothing (D29) — an absent metric here means no
      // invocation happened at all, which Alarm 2 covers, not this one.
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    const noInvocationsAlarm = new cloudwatch.Alarm(this, 'NoInvocationsAlarm', {
      alarmDescription: 'No cycle ran in a window longer than the six-hourly schedule.',
      metric: fn.metricInvocations({ period: Duration.hours(8), statistic: 'Sum' }),
      threshold: 1,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
      // The one alarm that distinguishes "healthy and calm" from "not running": a disabled schedule, a
      // deleted target, or a schedule nobody re-enabled after a rollback (D33).
      treatMissingData: cloudwatch.TreatMissingData.BREACHING,
    });

    // The literal `run_once.py::SNAPSHOT_FAILURE_PREFIX` string. Quoted, because CloudWatch Logs filter
    // patterns treat an unquoted leading "[" as the start of a space-delimited field-list pattern rather
    // than as literal text (confirmed against AWS's filter-pattern syntax documentation, task 3.3: "Enclose
    // exact phrases and terms that include non-alphanumeric characters in double quotation marks").
    const snapshotFailureFilter = new logs.MetricFilter(this, 'SnapshotFailureFilter', {
      logGroup,
      filterPattern: logs.FilterPattern.literal('"[SNAPSHOT NOT PUBLISHED]"'),
      metricNamespace: 'FerrenafeRainAlert',
      metricName: 'SnapshotPublishFailures',
      metricValue: '1',
    });

    const snapshotFailureAlarm = new cloudwatch.Alarm(this, 'SnapshotFailureAlarm', {
      alarmDescription: 'The alert went out and the judged public snapshot did not update.',
      metric: snapshotFailureFilter.metric({ period: Duration.hours(6), statistic: 'Sum' }),
      threshold: 1,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    // One topic. No subscription is created here — an email address is personal data
    // (`openspec/config.yaml → rules.archive`); the runbook adds it by hand, then verifies delivery.
    const topic = new sns.Topic(this, 'AlarmsTopic', {
      topicName: 'ferrenafe-scheduled-cycle-alarms',
    });
    for (const alarm of [errorsAlarm, noInvocationsAlarm, snapshotFailureAlarm]) {
      alarm.addAlarmAction(new cw_actions.SnsAction(topic));
    }
  }
}
