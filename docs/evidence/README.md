# Deployment evidence

Verifiable record of the AWS deployment: what was checked, against what, and
what the account actually answered. Written as the work happens, not
reconstructed afterwards.

## Why this is separate from the other two logs

- [`docs/decisions/`](../decisions/) — a choice and its consequences (ADRs).
- [`docs/blog/`](../blog/) — a surprise, with the evidence that revealed it.
- **here** — the audit trail of the deploy itself: commands, their real output,
  and the resource state they produced.

A finding explains why something was surprising. Evidence proves a step
happened.

## Rules

- **English**, like the rest of `docs/`.
- **Never commit an account id, an access key, a session token, or a full
  ARN containing an account id.** `tests/hygiene/test_repo_hygiene.py` scans
  the working tree *and* all git history for, among others, `AKIA[0-9A-Z]{16}`
  and `\b[0-9]{12}\b` — a real account id fails the build, and rewriting git
  history to remove one is far more expensive than masking it now. Write
  `<account>` instead.
- **Real output only.** Paste what the command returned. An idealised
  transcript is not evidence.
- **Record the failures too.** A deploy that needed three attempts is a more
  useful record than one that claims to have worked first time.
- Date each entry and name the region.

## Index

- [`2026-09-20-preflight.md`](2026-09-20-preflight.md) — environment, service
  and model availability checks before the first deploy.
- [`2026-09-20-agent-toolkit-connection.md`](2026-09-20-agent-toolkit-connection.md)
  — connecting the coding agent to AWS via the Agent Toolkit: CLI upgrade,
  `aws login`, MCP server, skills, and the two environment corrections it took.
- [`2026-09-21-first-live-deploy-and-invocation.md`](2026-09-21-first-live-deploy-and-invocation.md)
  — CDK bootstrap, the AgentCore deploy, and the first real invocation: three
  stacked defects, a measured 9.71 s cold start, and a correct message the
  validator refuses.
