import { App, Token } from 'aws-cdk-lib/core';
import { Annotations, Match, Template } from 'aws-cdk-lib/assertions';
import { stack, buildScheduledCycleStack } from '../bin/infra';
import { PublicSnapshotStack } from '../lib/public-snapshot-stack';
import { PLACEHOLDER_SNAPSHOT_BUCKET_NAME } from '../lib/scheduled-cycle-stack';

/**
 * Guards what `bin/infra.ts` configures, as opposed to what
 * `lib/public-snapshot-stack.ts` builds.
 *
 * `public-snapshot-stack.test.ts` synthesizes its own
 * `new PublicSnapshotStack(app, 'TestStack')` with no `env` and no
 * `stackName`, which is the right way to assert the template — but it means
 * the entrypoint's own decisions had no test behind them. Changing the region
 * on `bin/infra.ts` was caught by nothing.
 *
 * That matters more here than it usually would. The bucket this stack creates
 * is public by design. A deploy that silently targets the wrong region leaves
 * a second world-readable bucket where nobody is looking, and reports success.
 *
 * These assertions read the values CDK actually resolved on the stack object,
 * not the source text, so a change that reaches the same literal by another
 * route is still covered.
 */
describe('bin/infra.ts — the deploy target', () => {
  test('pins the region, so `cdk deploy` cannot follow whatever the shell happens to carry', () => {
    // us-east-2 is where the AgentCore composer runtime already lives, so the
    // whole application stays in one region, as CLAUDE.md records.
    expect(stack.region).toBe('us-east-2');
  });

  test('leaves the account unpinned, so no twelve-digit id enters this repository', () => {
    // Deliberate, not an oversight: `tests/hygiene/test_repo_hygiene.py`
    // refuses a bare twelve-digit number anywhere in the tree or its history.
    // The deploy runbook supplies the account through the `ferrenafe` profile,
    // and `cdk deploy` refuses a region mismatch. Written as an assertion so
    // that "fixing" this by hardcoding the account fails here, next to the
    // reason, rather than only in the hygiene scan.
    expect(Token.isUnresolved(stack.account)).toBe(true);
  });

  test('names the stack explicitly, so the runbook and the console agree', () => {
    // `cdk deploy ferrenafe-public-snapshot` in
    // `docs/runbooks/public-page-deploy.md` depends on this exact string; the
    // CDK default would derive it from the construct id instead.
    expect(stack.stackName).toBe('ferrenafe-public-snapshot');
  });
});

/**
 * Guards `buildScheduledCycleStack`, the function `bin/infra.ts` uses to decide whether — and how — to
 * synthesize `ScheduledCycleStack` (design D25, task 3.24). `snapshotWriterPolicyArn`/`agentRuntimeArn`/
 * `alarmEmail` each embed or carry data that is deliberately never committed (only supplied via `-c` at
 * deploy time), so this is exercised with hand-built `App({ context })` instances rather than the module-
 * level `app` — the real committed context/CLI `-c` flags are not present when `npm test` runs
 * `bin/infra.ts` directly.
 *
 * **Fix round 2, MUST 1 — these used to assert `toThrow`.** A thrown exception at this function's call
 * site aborted the ENTIRE app-construction process `bin/infra.ts` runs, including `PublicSnapshotStack`'s
 * own construction a few lines below it — verified live: `cdk synth PublicSnapshotStack` failed with
 * `ScheduledCycleStack`'s own missing-context error, which also breaks `docs/runbooks/public-page-deploy.md`'s
 * documented `cdk destroy` emergency teardown for infrastructure that is deployed and serving residents
 * right now. Every one of these checks now lives inside `ScheduledCycleStack`'s own constructor as an
 * `Annotations.of(this).addError(...)` — CDK's mechanism for a synthesis-time problem that fails only the
 * ONE stack that carries the annotation. `buildScheduledCycleStack` therefore now ALWAYS constructs the
 * stack once `snapshotBucketName` context is present at all (never throwing), and these tests assert the
 * resulting stack carries the expected error annotation instead of asserting a thrown exception.
 */
describe('bin/infra.ts — buildScheduledCycleStack', () => {
  const SNAPSHOT_WRITER_POLICY_ARN = 'arn:aws:iam::ACCOUNT:policy/SnapshotWriter';
  const AGENT_RUNTIME_ARN = 'arn:aws:bedrock-agentcore:us-east-2:ACCOUNT:runtime/example-runtime-abc123';
  // RFC 2606 documentation domain — never a real address. Assembled in parts (matching
  // `tests/hygiene/test_repo_hygiene.py`'s own `NPM_DEPRECATION_CONTACT` precedent): that scan is
  // deliberately email-shape-blind to any committed line, with no general test-fixture exemption.
  const ALARM_EMAIL = 'rain-alert-alarms' + '@' + 'example.com';
  // A real, non-placeholder bucket name for tests that are not exercising the placeholder-rejection
  // behaviour below (fix round 1, SHOULD 5).
  const REAL_BUCKET_NAME = 'ferrenafe-public-snapshot-test-fixture';

  test('is not synthesized at all when snapshotBucketName is absent', () => {
    const app = new App({ context: {} });

    expect(buildScheduledCycleStack(app)).toBeUndefined();
  });

  test('carries an error annotation naming the missing key when snapshotWriterPolicyArn is absent, never defaulting it', () => {
    const app = new App({ context: { snapshotBucketName: REAL_BUCKET_NAME } });

    const scheduledCycleStack = buildScheduledCycleStack(app);

    expect(scheduledCycleStack).toBeDefined();
    Annotations.fromStack(scheduledCycleStack!).hasError('*', Match.stringLikeRegexp('snapshotWriterPolicyArn'));
  });

  test('carries an error annotation naming the missing key when agentRuntimeArn is absent, never defaulting it', () => {
    const app = new App({
      context: {
        snapshotBucketName: REAL_BUCKET_NAME,
        snapshotWriterPolicyArn: SNAPSHOT_WRITER_POLICY_ARN,
      },
    });

    const scheduledCycleStack = buildScheduledCycleStack(app);

    expect(scheduledCycleStack).toBeDefined();
    Annotations.fromStack(scheduledCycleStack!).hasError('*', Match.stringLikeRegexp('agentRuntimeArn'));
  });

  test('carries an error annotation naming the missing key when alarmEmail is absent, never defaulting it', () => {
    // Fix round 1, SHOULD 6: required exactly like the two ARNs — an unsubscribed Alarm 2 is decorative
    // for this design's defining silent failure (the cycle stops running).
    const app = new App({
      context: {
        snapshotBucketName: REAL_BUCKET_NAME,
        snapshotWriterPolicyArn: SNAPSHOT_WRITER_POLICY_ARN,
        agentRuntimeArn: AGENT_RUNTIME_ARN,
      },
    });

    const scheduledCycleStack = buildScheduledCycleStack(app);

    expect(scheduledCycleStack).toBeDefined();
    Annotations.fromStack(scheduledCycleStack!).hasError('*', Match.stringLikeRegexp('alarmEmail'));
  });

  test('carries an error annotation when snapshotBucketName still holds the committed placeholder value, rather than deploying against it silently', () => {
    // Fix round 1, SHOULD 5: the placeholder reaches `RAIN_ALERT_SNAPSHOT_BUCKET` truthily, so
    // `S3SnapshotPublisher` would be built and every publish would fail with `AccessDenied` (the
    // `SnapshotWriter` policy is scoped to the REAL bucket) — `run_once` absorbs that failure by design
    // (the alert must still go out), so the public page would freeze with no loud signal. The one context
    // value that could be silently wrong now gets the same error-annotation treatment as the two ARNs.
    const app = new App({
      context: {
        snapshotBucketName: 'ferrenafe-public-snapshot-example',
        snapshotWriterPolicyArn: SNAPSHOT_WRITER_POLICY_ARN,
        agentRuntimeArn: AGENT_RUNTIME_ARN,
        alarmEmail: ALARM_EMAIL,
      },
    });

    const scheduledCycleStack = buildScheduledCycleStack(app);

    expect(scheduledCycleStack).toBeDefined();
    Annotations.fromStack(scheduledCycleStack!).hasError('*', Match.stringLikeRegexp('snapshotBucketName'));
  });

  test('never throws, and never prevents PublicSnapshotStack from being constructed in the same app', () => {
    // Fix round 2, MUST 1 — the specific regression this whole round exists to close: constructing
    // `ScheduledCycleStack` with the required ARNs/email missing must never throw, and must never prevent
    // `PublicSnapshotStack` from being constructed in the same app.
    const app = new App({ context: { snapshotBucketName: REAL_BUCKET_NAME } });

    expect(() => buildScheduledCycleStack(app)).not.toThrow();
    const publicSnapshotStack = new PublicSnapshotStack(app, 'PublicSnapshotStackFixture');
    expect(publicSnapshotStack).toBeDefined();
  });

  test('synthesizes when all required context props are supplied, targeting the pinned region and named stack', () => {
    const app = new App({
      context: {
        snapshotBucketName: REAL_BUCKET_NAME,
        snapshotWriterPolicyArn: SNAPSHOT_WRITER_POLICY_ARN,
        agentRuntimeArn: AGENT_RUNTIME_ARN,
        alarmEmail: ALARM_EMAIL,
      },
    });

    const scheduledCycleStack = buildScheduledCycleStack(app);

    expect(scheduledCycleStack).toBeDefined();
    expect(scheduledCycleStack!.region).toBe('us-east-2');
    expect(scheduledCycleStack!.stackName).toBe('ferrenafe-scheduled-cycle');
  });

  test('never commits the account-bearing ARNs: cdk.json context carries a real snapshotBucketName and nothing else', () => {
    // Fix round 1, SHOULD 4: `cdk.context.json` is CDK's own auto-written cache (the CLI writes to it
    // whenever a context provider runs, and the keys it writes — e.g. `availability-zones:account=…` —
    // embed the account id), not hand-authored config; using it as one is a tripwire, not a guardrail, since
    // the hygiene scan only catches an account id there if someone runs it before committing. `cdk.json`'s
    // own `context` block is never auto-written by the CDK CLI, so `snapshotBucketName` lives there instead.
    // The value is now the real deployed bucket's CloudFormation-generated name. It held the placeholder
    // literal while that name was unknown from source, and `buildScheduledCycleStack` still refuses the
    // placeholder (fix round 1, SHOULD 5) so a half-configured checkout fails loudly rather than deploying
    // against the wrong bucket. What this test guards is not the literal — pinning it would now fail on
    // every legitimate change of bucket — but the property that matters: the committed context carries a
    // usable bucket name and nothing account-bearing. The two ARNs and the email are supplied only via `-c`
    // at deploy time and never committed anywhere.
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const cdkJson = require('../cdk.json') as { context: Record<string, unknown> };
    const bucketName = cdkJson.context.snapshotBucketName;

    expect(typeof bucketName).toBe('string');
    expect(bucketName).not.toBe(PLACEHOLDER_SNAPSHOT_BUCKET_NAME);
    // An S3 bucket name cannot contain a colon, so an ARN here would be a category error as well as a leak.
    expect(bucketName as string).not.toContain('arn:');
    // The repository forbids a committed account id; a bucket name has no reason to carry a 12-digit run.
    expect(bucketName as string).not.toMatch(/\d{12}/);

    for (const forbidden of ['snapshotWriterPolicyArn', 'agentRuntimeArn', 'alarmEmail']) {
      expect(cdkJson.context).not.toHaveProperty(forbidden);
    }
  });

  describe('relayEnabled context parsing (D45)', () => {
    const baseContext = {
      snapshotBucketName: REAL_BUCKET_NAME,
      snapshotWriterPolicyArn: SNAPSHOT_WRITER_POLICY_ARN,
      agentRuntimeArn: AGENT_RUNTIME_ARN,
      alarmEmail: ALARM_EMAIL,
    };
    const synth = (context: Record<string, unknown>): Template =>
      Template.fromStack(buildScheduledCycleStack(new App({ context }))!);
    const relayVariable = (template: Template): unknown => {
      const [fn] = Object.values(template.findResources('AWS::Lambda::Function'));
      return fn.Properties.Environment.Variables.RAIN_ALERT_RELAY_BUCKET;
    };
    const relayAlarms = (template: Template): unknown[] =>
      Object.values(template.findResources('AWS::CloudWatch::Alarm')).filter(
        (a) => a.Properties.MetricName === 'SenamhiRelayDegraded',
      );

    test('the real committed cdk.json context ships the relay OFF: no env var and no relay alarm', () => {
      const cdkJson = require('../cdk.json') as { context: Record<string, unknown> };
      const template = synth({ ...baseContext, relayEnabled: cdkJson.context.relayEnabled });

      expect(cdkJson.context).toHaveProperty('relayEnabled');
      expect(relayVariable(template)).toBeUndefined();
      expect(relayAlarms(template)).toEqual([]);
    });

    test.each([['true'], [true]])('%p enables the relay: env var and alarm present', (value) => {
      const template = synth({ ...baseContext, relayEnabled: value });

      expect(relayVariable(template)).toBeDefined();
      expect(relayAlarms(template)).toHaveLength(1);
    });

    test.each([['false'], [false]])('%p disables the relay', (value) => {
      const template = synth({ ...baseContext, relayEnabled: value });

      expect(relayVariable(template)).toBeUndefined();
      expect(relayAlarms(template)).toEqual([]);
    });

    test('absent disables the relay (safe default, consistent with cdk.json)', () => {
      const template = synth(baseContext);

      expect(relayVariable(template)).toBeUndefined();
      expect(relayAlarms(template)).toEqual([]);
    });

    test.each([['yes'], [1], [''], [null]])('%p throws a clear synth-time error', (value) => {
      expect(() => buildScheduledCycleStack(new App({ context: { ...baseContext, relayEnabled: value } }))).toThrow(
        /relayEnabled/,
      );
    });

    test('keeps bucket, user and policy in both states', () => {
      for (const relayEnabled of ['true', 'false']) {
        const template = synth({ ...baseContext, relayEnabled });
        template.resourceCountIs('AWS::S3::Bucket', 2); // relay bucket + audit log bucket
        template.resourceCountIs('AWS::CloudTrail::Trail', 1);
        template.resourceCountIs('AWS::IAM::ManagedPolicy', 1);
        template.resourceCountIs('AWS::IAM::User', 1);
      }
    });
  });
});
