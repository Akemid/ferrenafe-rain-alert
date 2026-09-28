import { Token } from 'aws-cdk-lib/core';
import { stack } from '../bin/infra';

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
