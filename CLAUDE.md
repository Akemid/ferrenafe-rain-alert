# ferrenafe-rain-alert — project instructions

A rain early-warning system for the town of Ferreñafe, Peru. It reads official
SENAMHI warnings and Open-Meteo forecasts, decides an alert level, composes a
Spanish civil-protection message, and sends it to real residents.

**This is life-safety software for a real community.** Two habits follow from
that and are not negotiable here:

- **Verify against the real thing, not against memory.** Several defects in
  this repository survived review precisely because a check looked like it had
  run and had not. See `docs/blog/` for the specific ones.
- **Fail toward sending the deterministic template**, never toward sending
  something unverified. A false rejection costs phrasing quality; a false
  acceptance can cost trust in the alert.

## Where things are written down

| Directory | What lives there |
|---|---|
| `docs/architecture/` | What each part of the code does, layer by layer |
| `docs/decisions/` | ADRs — a choice, its evidence, and its consequences |
| `docs/blog/` | Findings log — a surprise, with the command that revealed it |
| `docs/evidence/` | Deployment audit trail — commands and their real output |
| `docs/runbooks/` | Operational procedures |
| `openspec/` | SDD planning artifacts per change |

Each of those has a README explaining its own conventions. Read the relevant
one before adding to it.

## Hard rules

- **Never commit a secret, an AWS account id, or an ARN containing one.**
  `tests/hygiene/test_repo_hygiene.py` scans the working tree *and all git
  history* for, among others, `AKIA[0-9A-Z]{16}` and `\b[0-9]{12}\b`. Write
  `<account>` in documentation instead.
- **Artifacts are in English** — code, comments, tests, docs, commit messages,
  PR descriptions. Only the composed alert text itself is Spanish, because its
  readers are.
- **No `unittest.mock`.** Hand-written fakes, following `tests/support/fakes.py`.
- **Strict TDD**: the failing test first, and it must fail for the reason you
  intend.
- A security review runs on the branch diff before every pull request.

## Verification gate

```bash
uv run pytest                      # root suite
uv run --directory agent pytest    # the AgentCore deployment unit
uv run ruff check                  # bare — naming a path bypasses the excludes
uv run ruff format --check
uv run mypy                        # files = ["src"]; tests/ and agent/ are unchecked
```

The default suite is **offline**: no network, no credentials, no model calls.
It must stay that way, and two `tests/conftest.py` fixtures enforce it
structurally rather than by convention: a session-scoped fixture forces
dummy AWS credentials, and a session-scoped socket guard refuses any
non-loopback **TCP** connection outright. `connect_ex`, UDP `sendto` and
DNS resolution all pass through untouched, so this is not "any network
egress": every HTTP call `botocore` and `urllib3` make goes through
`socket.connect` and is blocked, but botocore's client-side monitoring
does use UDP `sendto`. It is disabled by default, its default host is
loopback, and nothing here enables it — so the gap is unreachable in this
repository rather than absent. Running with the AWS environment
variables unset is a sanity check that nothing depends on the *ambient*
environment happening to be clean — it is not a test of whether a
credential or a network call is required, because the credentials fixture
supplies its own dummy values either way. What actually proves "no network"
is the socket guard raising on a real connection attempt, verified directly
by `tests/unit/test_conftest_fixtures.py`.

## AWS

Deployment target is `us-east-1`. The AWS CLI profile for this project is
`ferrenafe`, authenticated with `aws login` (short-lived credentials, not
access keys).

### Which AWS MCP server to use

Two servers expose the same five documentation tools under identical names:
`aws-mcp` (the Agent Toolkit's, authenticated as `ferrenafe`) and
`awsknowledge` (bundled with the `aws-agents`, `codebase-documentor-for-aws`
and `deploy-on-aws` plugins).

**Prefer `mcp__aws-mcp__*`.** It is a strict superset — the same
`search_documentation`, `read_documentation`, `retrieve_skill`,
`list_regions` and `get_regional_availability`, plus `run_script` for real
AWS API calls under this project's profile.

`awsknowledge` is the `aws-knowledge-mcp-server` that AWS's own setup guide
says to remove once the Agent Toolkit is installed. It stays only because it
ships inside `aws-agents`, which also supplies seven AgentCore-specific
skills (`agents-deploy`, `agents-debug`, `agents-harden`, …) that the
Toolkit's catalogue does **not** replace — its AgentCore coverage is one
section of the broad `amazon-bedrock` skill. Disabling the plugin to remove
one duplicate server would cost those seven.

`awsiac` (cfn-lint, cfn-guard, CDK guidance) is **not** a duplicate: static
template validation has no equivalent in `aws-mcp`. Use it for CloudFormation
and CDK work.

<!-- BEGIN AWS Agent Toolkit rules -->

# AWS Guidance

- Where these AWS rules conflict with the project's own instructions, the
  project's instructions take precedence.
- Prefer the AWS MCP Server for AWS interactions — it provides sandboxed
  execution, observability, and audit logging. If unavailable, use the
  AWS CLI directly.
- Before starting a task, check whether a relevant AWS skill is available.
  Load the skill with `retrieve_skill` and prefer its guidance over
  general knowledge.
- When uncertain about specific AWS details (API parameters, permissions,
  limits, error codes), verify against documentation rather than guessing.
  State uncertainty explicitly if you cannot confirm.
- When creating infrastructure, prefer infrastructure-as-code (AWS CDK or
  CloudFormation) over direct CLI commands.
- When working with infrastructure, follow AWS Well-Architected Framework
  principles.
- Do not use em dashes in AWS resource names or descriptions. Use
  hyphens instead.

## Secret Safety

- MUST load the `aws-secrets-manager` skill first for any secret,
  credential, API key, token, or password task. MUST NOT call
  `secretsmanager get-secret-value` or `batch-get-secret-value`, and MUST
  NOT hit the Secrets Manager Agent daemon directly. MUST use
  `{{resolve:secretsmanager:secret-id:SecretString:json-key}}` with
  `asm-exec` so the secret resolves at runtime without entering context.

<!-- END AWS Agent Toolkit rules -->
