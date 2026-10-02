import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { Annotations, CfnOutput, Stack, StackProps, RemovalPolicy, Duration, TimeZone } from 'aws-cdk-lib/core';
import { Construct } from 'constructs';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as cw_actions from 'aws-cdk-lib/aws-cloudwatch-actions';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as sns_subscriptions from 'aws-cdk-lib/aws-sns-subscriptions';
import * as s3 from 'aws-cdk-lib/aws-s3';
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

/**
 * The relay contract shared with the Python reader and producer (`contracts/senamhi-relay.json`, design
 * D37). Only the object key is needed here; reading it at synth time keeps the IAM resource and the Python
 * constant from drifting.
 */
const RELAY_CONTRACT = JSON.parse(
  readFileSync(join(__dirname, '..', '..', 'contracts', 'senamhi-relay.json'), 'utf-8'),
) as { readonly object_key: string };

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

/**
 * The committed placeholder for `snapshotBucketName` (see `cdk.json`'s `context` block). Never a real
 * bucket name — the constructor below refuses to treat it as one (fix round 1, SHOULD 5), rather than
 * letting it reach `RAIN_ALERT_SNAPSHOT_BUCKET` truthily and fail every publish with `AccessDenied` while
 * the alert itself still goes out and reports success.
 *
 * **Fix round 2, MUST 1 — this check now lives here, not in `bin/infra.ts`.** A `throw` at
 * `bin/infra.ts`'s module scope killed the ENTIRE app-construction process, including `PublicSnapshotStack`
 * — verified live: `cdk synth PublicSnapshotStack` failed with this exact error, which also breaks
 * `docs/runbooks/public-page-deploy.md`'s documented `cdk destroy` emergency teardown for infrastructure
 * that is deployed and serving residents right now. `Annotations.of(this).addError(...)` below is CDK's own
 * mechanism for a synthesis-time problem that must fail *this stack's* `cdk synth`/`cdk deploy` without
 * aborting the whole app-construction process other stacks share (confirmed live: `Annotations.fromStack`
 * and the CDK CLI both scope error annotations to the stack that carries them).
 */
export const PLACEHOLDER_SNAPSHOT_BUCKET_NAME = 'ferrenafe-public-snapshot-example';

export interface ScheduledCycleStackProps extends StackProps {
  /** committed to `cdk.json`'s `context` block — no account id in it (design D25). */
  readonly snapshotBucketName: string;
  /** supplied only at deploy time via `-c snapshotWriterPolicyArn=…` — never committed, the ARN embeds the
   * account id (design D25). */
  readonly snapshotWriterPolicyArn: string;
  /** supplied only at deploy time via `-c agentRuntimeArn=…` — never committed, for the same reason as
   * `snapshotWriterPolicyArn`: an AgentCore runtime ARN embeds the account id. */
  readonly agentRuntimeArn: string;
  /** supplied only at deploy time via `-c alarmEmail=…` — never committed, because an email address is
   * personal data (`openspec/config.yaml → rules.archive`). Required, not optional (fix round 1, SHOULD 6):
   * Alarm 2 is the only detector for this design's defining silent failure (the cycle stops running), and
   * an alarm nobody is subscribed to is decorative. */
  readonly alarmEmail: string;
  /**
   * Whether the schedule is enabled on deploy (design D34's durable disable route:
   * `cdk deploy ScheduledCycleStack -c scheduleEnabled=false`).
   * @default true
   */
  readonly scheduleEnabled?: boolean;
  /**
   * Whether the Lambda reads SENAMHI warnings from the relay bucket (design D41/D45's declarative
   * rollback: `cdk deploy -c relayEnabled=false`). When false the stack omits the
   * `RAIN_ALERT_RELAY_BUCKET` variable and the relay alarm; the bucket, user and policy remain.
   * @default true
   */
  readonly relayEnabled?: boolean;
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

    // --- Required-value validation (fix round 2, MUST 1) ---
    // Every check here is an `Annotations` ERROR, never a thrown exception: `bin/infra.ts` always
    // constructs this stack once `snapshotBucketName` context is present at all (even a placeholder or an
    // empty-string fallback for a missing ARN/email), so that a problem specific to THIS stack fails only
    // `cdk synth`/`cdk deploy ScheduledCycleStack` and leaves `PublicSnapshotStack`'s own synth, diff and
    // destroy commands unaffected. The resources below are still constructed with whatever values were
    // given — none of the props below can make CDK itself throw synchronously (confirmed live for
    // `EmailSubscription('')`) — so an operator running `cdk synth ScheduledCycleStack` sees every one of
    // these errors at once, not just the first.
    if (!props.snapshotBucketName || props.snapshotBucketName === PLACEHOLDER_SNAPSHOT_BUCKET_NAME) {
      Annotations.of(this).addError(
        'snapshotBucketName is required and must not be the placeholder value ' +
          `'${PLACEHOLDER_SNAPSHOT_BUCKET_NAME}' committed in cdk.json's context. Replace it with the real ` +
          'deployed PublicSnapshotStack bucket name — the placeholder would otherwise reach ' +
          'RAIN_ALERT_SNAPSHOT_BUCKET truthily and fail every publish with AccessDenied while the alert ' +
          'itself still goes out and reports success.',
      );
    }
    if (!props.snapshotWriterPolicyArn) {
      Annotations.of(this).addError(
        'snapshotWriterPolicyArn is required and is never defaulted (design.md D25). Supply it at deploy ' +
          'time — it must never be committed, because the ARN embeds the account id: cdk deploy ' +
          'ScheduledCycleStack -c snapshotWriterPolicyArn=<arn>',
      );
    }
    if (!props.agentRuntimeArn) {
      Annotations.of(this).addError(
        'agentRuntimeArn is required and is never defaulted, for the same reason as ' +
          'snapshotWriterPolicyArn: the ARN embeds the account id. Supply it at deploy time: cdk deploy ' +
          'ScheduledCycleStack -c agentRuntimeArn=<arn>',
      );
    }
    if (!props.alarmEmail) {
      Annotations.of(this).addError(
        "alarmEmail is required and is never defaulted — an unsubscribed Alarm 2 is decorative for this " +
          "design's defining silent failure (the cycle stops running). An email address is personal data " +
          'and must never be committed. Supply it at deploy time: cdk deploy ScheduledCycleStack ' +
          '-c alarmEmail=<address>',
      );
    }

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
      // Fix round 1, MUST 2: `RemovalPolicy.RETAIN` protects the table from *stack* deletion, not from
      // *data* deletion through a live grant — without point-in-time recovery there is nothing to restore
      // from if the dedup history is ever erased in bulk. `deletionProtection` is a second, independent
      // guard against an accidental `cdk destroy`/console delete of this specific resource.
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      deletionProtection: true,
    });

    // --- Relay bucket (D41) ---
    // No `bucketName`: a generated name embeds no account id, and the repository must never hold one.
    // Retained: it is the forensic record of what the producer pushed (R5).
    const relayBucket = new s3.Bucket(this, 'RelayBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      versioned: true,
      encryption: s3.BucketEncryption.S3_MANAGED,
      objectOwnership: s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
      lifecycleRules: [
        { noncurrentVersionExpiration: Duration.days(90) },
        { abortIncompleteMultipartUploadAfter: Duration.days(1) },
      ],
      removalPolicy: RemovalPolicy.RETAIN,
    });
    const relayObjectArn = relayBucket.arnForObjects(RELAY_CONTRACT.object_key);

    // --- CloudWatch Logs (D33) ---
    const logGroup = new logs.LogGroup(this, 'FunctionLogs', {
      // The proposal flags never-expire as a cost that grows silently.
      retention: logs.RetentionDays.ONE_MONTH,
      // The log group is not this design's audit trail — the table (RETAIN) and the versioned snapshot
      // bucket are. Destroying it on stack teardown costs nothing this design depends on.
      removalPolicy: RemovalPolicy.DESTROY,
    });

    // --- Lambda execution role (fix round 1) ---
    // An explicit role, built with NO managed policies, rather than letting `lambda.Function` create one
    // for us. Letting CDK create the role attaches `AWSLambdaBasicExecutionRole` automatically, which
    // grants `logs:CreateLogGroup`/`logs:CreateLogStream`/`logs:PutLogEvents` on `Resource: "*"` — every
    // other grant in this stack is scoped to one named resource, and that one wildcard would make a claim
    // of "every grant is resource-scoped" false for the role as a whole. `logGroup.grantWrite(executionRole)`
    // below grants the same three actions, scoped to the one log group this stack itself created.
    const executionRole = new iam.Role(this, 'ExecutionRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
    });

    const relayEnabled = props.relayEnabled ?? true;

    // --- Lambda (D30, D31) ---
    const fn = new lambda.Function(this, 'ScheduledCycleFunction', {
      functionName: 'ferrenafe-rain-alert-cycle',
      role: executionRole,
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
      // **`reservedConcurrentExecutions: 1` belongs here and is absent, because this account cannot
      // express it.** D35's reasoning is unchanged and still correct: EventBridge Scheduler delivery is
      // at-least-once, two concurrent cycles would both read an empty dedup history and both send, and a
      // reservation of 1 throttles the second so its retry sees what the first wrote.
      //
      // The first deploy, 2026-10-01, failed on exactly that line:
      //
      //   Specified ReservedConcurrentExecutions for function decreases account's
      //   UnreservedConcurrentExecution below its minimum value of [10]
      //
      // The account's total concurrency is **10**, against AWS's 1000 default, and AWS requires 10 to
      // remain unreserved — so no reservation is possible at any value, not just an inconvenient one. A
      // support case to raise it to 100 was filed the same day; Service Quotas reports this quota as
      // `Adjustable: true` and its adjustment API refuses any value below the default, which is why it
      // could not be self-served. See
      // `docs/blog/2026-10-01-the-quota-that-says-adjustable-and-is-not.md`.
      //
      // What is exposed meanwhile: a duplicated cycle writes a duplicate `AlertRecord` and repeats an
      // entry under "Alertas enviadas" on the public page. It does not reach a resident twice, because
      // `ConsoleNotifier` is the only notifier that exists — the same coupling the `Notifier` port's own
      // docstring records for the dedup-suppression finding, and it changes on the same day, for the same
      // reason.
      //
      // **Restore this line when the quota lands.** It is a one-line change plus the assertion in
      // `infra/test/scheduled-cycle-stack.test.ts`, and it is strictly better than what is here now.
      logGroup,
      environment: {
        RAIN_ALERT_AGENT_RUNTIME_ARN: props.agentRuntimeArn,
        // Relation 1 (D31): the one value that crosses the Python/TypeScript boundary, read from the same
        // golden contract file `agentcore_invoker.py`'s own test asserts against. Not a hand-copied number.
        RAIN_ALERT_AGENT_TIMEOUT_S: String(CONTRACT.agent_read_timeout_seconds),
        RAIN_ALERT_SNAPSHOT_BUCKET: props.snapshotBucketName,
        // Presence selects relay mode (D43), so the variable is omitted entirely when disabled.
        ...(relayEnabled ? { RAIN_ALERT_RELAY_BUCKET: relayBucket.bucketName } : {}),
      },
    });

    // --- IAM grants (D25, D26, D32, and the AgentCore grant) ---

    // The one grant `AWSLambdaBasicExecutionRole` would otherwise have supplied on `Resource: "*"` — scoped
    // instead to the one log group this stack owns (fix round 1; see `ExecutionRole`'s own comment above).
    logGroup.grantWrite(executionRole);

    // The imported SnapshotWriter policy, ATTACHED — never re-granted with a fresh s3:PutObject statement
    // of this stack's own (D25). `fromManagedPolicyArn` performs no ARN parsing that would reject a test
    // placeholder (confirmed against the installed aws-cdk-lib source, task 3.2).
    fn.role!.addManagedPolicy(
      iam.ManagedPolicy.fromManagedPolicyArn(this, 'SnapshotWriterPolicy', props.snapshotWriterPolicyArn),
    );

    // Fix round 1, MUST 2: `table.grantReadWriteData(fn)` granted 12 actions, including `Scan` and
    // `BatchWriteItem`, against the four the adapter actually calls
    // (`dynamodb_alert_repository.py`: Query, GetItem, PutItem, DeleteItem). The cycle parses two
    // unauthenticated third-party responses every run (SENAMHI HTML, Open-Meteo JSON); a parser bug or a
    // compromised transitive dependency inherits this role, and `Scan` reads — `BatchWriteItem`/a broad
    // write erases — the entire dedup audit trail. One table, named, and now the exact four actions, never
    // `*` on either axis (D26).
    table.grant(fn, 'dynamodb:Query', 'dynamodb:GetItem', 'dynamodb:PutItem', 'dynamodb:DeleteItem');

    // Relay reads (D39, D41): two explicit statements so the action set is exact, never `grantRead` (which
    // would add `GetObjectVersion`, `GetBucket*` and `List*`). `ListBucket` is required: without it S3
    // answers a missing key with 403 instead of 404 (V.1), and the reader could not tell the two apart.
    fn.addToRolePolicy(new iam.PolicyStatement({ actions: ['s3:GetObject'], resources: [relayObjectArn] }));
    fn.addToRolePolicy(new iam.PolicyStatement({ actions: ['s3:ListBucket'], resources: [relayBucket.bucketArn] }));

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

    // Fix round 1, MUST 1: `InvokeAgentRuntime` is evaluated against BOTH the agent runtime resource and
    // the agent endpoint resource being invoked (AWS's `bedrock-agentcore` resource-based-policies
    // documentation, verbatim: "must allow the action on both the agent runtime and agent endpoint
    // resources"). `agentcore_invoker.py` passes no `qualifier`, so the DEFAULT endpoint is the target, and
    // a grant naming only the runtime ARN silently denies every invocation — confirmed against the
    // installed `aws-cdk-lib`'s own `aws-bedrockagentcore` helper (`runtime-base.js`), which grants exactly
    // `[runtimeArn, \`${runtimeArn}/*\`]` for this reason. Still resource-scoped (`/*` spans only this one
    // runtime's own endpoints, never a wildcard across runtimes) and deliberately no `bedrock:InvokeModel`
    // statement anywhere on this role — this Lambda calls the data-plane operation only, never a foundation
    // model directly.
    fn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['bedrock-agentcore:InvokeAgentRuntime'],
        resources: [props.agentRuntimeArn, `${props.agentRuntimeArn}/*`],
      }),
    );

    // --- Relay producer identity (D36, D41) ---
    // The managed policy and user stay even when `relayEnabled` is false. CDK creates NO access key: the
    // operator creates it in Phase L, so the secret never enters the template or the CloudFormation state.
    const relayWriter = new iam.ManagedPolicy(this, 'RelayWriter', {
      managedPolicyName: 'RelayWriter',
      statements: [new iam.PolicyStatement({ actions: ['s3:PutObject'], resources: [relayObjectArn] })],
    });
    const producerUser = new iam.User(this, 'RelayProducerUser', {
      userName: 'ferrenafe-relay-producer',
      managedPolicies: [relayWriter],
    });

    // Outputs carry names only, never ARNs (an ARN embeds the account id).
    new CfnOutput(this, 'RelayBucketName', { value: relayBucket.bucketName });
    new CfnOutput(this, 'RelayProducerUserName', { value: producerUser.userName });
    new CfnOutput(this, 'RelayObjectKey', { value: RELAY_CONTRACT.object_key });

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

    // One topic. Fix round 1, SHOULD 6: this stack used to create no subscription at all — the privacy
    // premise (an email address is personal data, `openspec/config.yaml → rules.archive`) was right, but
    // the conclusion left Alarm 2, the only detector of this design's defining silent failure (the cycle
    // stops running), transitioning to ALARM in a console nobody watches. `alarmEmail` is now a required
    // prop, supplied only via `-c alarmEmail=…` at deploy time and never committed, exactly like
    // `agentRuntimeArn` — the address cannot be forgotten and never enters git.
    const topic = new sns.Topic(this, 'AlarmsTopic', {
      topicName: 'ferrenafe-scheduled-cycle-alarms',
    });
    // Guarded rather than unconditional (fix round 2, MUST 1): an empty `alarmEmail` — the fallback
    // `bin/infra.ts` supplies when the context value is missing — throws synchronously from inside
    // `Subscription`'s own construct-id derivation (`Only root constructs may have an empty ID`, confirmed
    // live), which would defeat the whole point of reporting every missing value as an `Annotations` error
    // instead of crashing synthesis. The missing-value error above already covers this case; no subscription
    // is the correct behaviour for no address, not a placeholder one.
    if (props.alarmEmail) {
      topic.addSubscription(new sns_subscriptions.EmailSubscription(props.alarmEmail));
    }
    const alarms = [errorsAlarm, noInvocationsAlarm, snapshotFailureAlarm];

    // Relay degraded alarm (D40). The filter matches the single-line `on_read` record that
    // `wiring.py::log_relay_read` prints; `$.degraded` is 1 for any relay Unavailable. No `defaultValue`:
    // a default of 0 would fill windows with no cycle and defeat `breaching` below.
    if (relayEnabled) {
      const relayFilter = new logs.MetricFilter(this, 'RelayDegradedFilter', {
        logGroup,
        filterPattern: logs.FilterPattern.stringValue('$.event', '=', 'senamhi_relay'),
        metricNamespace: 'FerrenafeRainAlert',
        metricName: 'SenamhiRelayDegraded',
        metricValue: '$.degraded',
      });
      // 21600 s is a sliding window. Two consecutive periods span 12 h, and a healthy schedule puts at
      // least one record in any 12 h, so normal operation cannot breach both; a stopped cycle can.
      alarms.push(
        new cloudwatch.Alarm(this, 'RelayDegradedAlarm', {
          alarmDescription: 'The SENAMHI relay was degraded or absent for two consecutive cycles.',
          metric: relayFilter.metric({ period: Duration.seconds(21600), statistic: 'Maximum' }),
          threshold: 1,
          evaluationPeriods: 2,
          datapointsToAlarm: 2,
          comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
          treatMissingData: cloudwatch.TreatMissingData.BREACHING,
        }),
      );
    }
    for (const alarm of alarms) {
      alarm.addAlarmAction(new cw_actions.SnsAction(topic));
    }
  }
}
