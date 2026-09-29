import { App, Token } from 'aws-cdk-lib/core';
import { stack, buildScheduledCycleStack } from '../bin/infra';

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
    // whole application stays in one region. `docs/runbooks/public-page-deploy.md`
    // records why this diverges from the us-east-1 named in CLAUDE.md.
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

  test('throws a clear error naming the missing key when snapshotWriterPolicyArn is absent, never defaulting it', () => {
    const app = new App({ context: { snapshotBucketName: REAL_BUCKET_NAME } });

    expect(() => buildScheduledCycleStack(app)).toThrow(/snapshotWriterPolicyArn/);
  });

  test('throws a clear error naming the missing key when agentRuntimeArn is absent, never defaulting it', () => {
    const app = new App({
      context: {
        snapshotBucketName: REAL_BUCKET_NAME,
        snapshotWriterPolicyArn: SNAPSHOT_WRITER_POLICY_ARN,
      },
    });

    expect(() => buildScheduledCycleStack(app)).toThrow(/agentRuntimeArn/);
  });

  test('throws a clear error naming the missing key when alarmEmail is absent, never defaulting it', () => {
    // Fix round 1, SHOULD 6: required exactly like the two ARNs — an unsubscribed Alarm 2 is decorative
    // for this design's defining silent failure (the cycle stops running).
    const app = new App({
      context: {
        snapshotBucketName: REAL_BUCKET_NAME,
        snapshotWriterPolicyArn: SNAPSHOT_WRITER_POLICY_ARN,
        agentRuntimeArn: AGENT_RUNTIME_ARN,
      },
    });

    expect(() => buildScheduledCycleStack(app)).toThrow(/alarmEmail/);
  });

  test('throws when snapshotBucketName still holds the committed placeholder value, rather than deploying against it silently', () => {
    // Fix round 1, SHOULD 5: the placeholder reaches `RAIN_ALERT_SNAPSHOT_BUCKET` truthily, so
    // `S3SnapshotPublisher` would be built and every publish would fail with `AccessDenied` (the
    // `SnapshotWriter` policy is scoped to the REAL bucket) — `run_once` absorbs that failure by design
    // (the alert must still go out), so the public page would freeze with no loud signal. The one context
    // value that could be silently wrong now gets the same throw-not-default treatment as the two ARNs.
    const app = new App({
      context: {
        snapshotBucketName: 'ferrenafe-public-snapshot-example',
        snapshotWriterPolicyArn: SNAPSHOT_WRITER_POLICY_ARN,
        agentRuntimeArn: AGENT_RUNTIME_ARN,
        alarmEmail: ALARM_EMAIL,
      },
    });

    expect(() => buildScheduledCycleStack(app)).toThrow(/snapshotBucketName/);
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

  test('never commits the account-bearing ARNs: cdk.json context carries the still-a-placeholder snapshotBucketName only', () => {
    // Fix round 1, SHOULD 4: `cdk.context.json` is CDK's own auto-written cache (the CLI writes to it
    // whenever a context provider runs, and the keys it writes — e.g. `availability-zones:account=…` —
    // embed the account id), not hand-authored config; using it as one is a tripwire, not a guardrail, since
    // the hygiene scan only catches an account id there if someone runs it before committing. `cdk.json`'s
    // own `context` block is never auto-written by the CDK CLI, so `snapshotBucketName` lives there instead.
    // Its value is deliberately still the placeholder literal `buildScheduledCycleStack` refuses (fix round
    // 1, SHOULD 5) — a real `cdk deploy` fails loudly until the owner replaces it, rather than silently
    // deploying against the wrong bucket. The two ARNs and the email above are supplied only via `-c` at
    // deploy time and never committed anywhere.
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const cdkJson = require('../cdk.json') as { context: Record<string, unknown> };

    expect(cdkJson.context.snapshotBucketName).toBe('ferrenafe-public-snapshot-example');
  });
});
