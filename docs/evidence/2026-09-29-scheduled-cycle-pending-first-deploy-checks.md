# `scheduled-cycle` — pending checks for the first live deploy

2026-09-29, `us-east-2`. **Not yet performed** — `sdd-apply` runs no AWS
command, per `CLAUDE.md`. This entry records what the first real deploy must
confirm, so the check is planned before the account is touched rather than
reconstructed afterwards. Once run, replace this entry's contents with the
real command output, per this directory's own convention ("real output
only", "record the failures too").

## `logs:CreateLogGroup` — present or absent from the execution role, and does it matter

`infra/lib/scheduled-cycle-stack.ts`'s explicit `ExecutionRole` (Phase 3 fix
round 1) is granted `logGroup.grantWrite(executionRole)` — `logs:CreateLogStream`
and `logs:PutLogEvents`, scoped to the one `FunctionLogs` log group this
stack itself creates via `logs.LogGroup(...)`. It does **not** grant
`logs:CreateLogGroup`.

AWS's own *Sending Lambda function logs to CloudWatch Logs* documentation
lists all three actions — `CreateLogGroup`, `CreateLogStream`,
`PutLogEvents` — as required, with no documented carve-out for a Lambda
whose log group is pre-created and pinned via `LoggingConfig` (which this
stack does: the function's `logGroup` prop points at the explicit
`FunctionLogs` construct, so the runtime should never need to *create* the
group — only write to it). The `Fn::GetAtt … Arn` resource form
`logGroup.grantWrite` produces is correct for the log group this stack owns.
**This cannot be settled without a deploy.**

**Blast radius if the log group ever needs `CreateLogGroup` and does not
have it**: the Lambda's own log delivery would fail. Concretely:

- No log events reach CloudWatch Logs.
- Alarm 3's metric filter (`"[SNAPSHOT NOT PUBLISHED]"` on the log group)
  never has anything to match, so it never fires — even on a real snapshot
  publish failure.
- The `as_json` audit line for every cycle never lands anywhere queryable.
- Alarms 1 (`Errors`) and 2 (`Invocations`) are unaffected — both read
  Lambda service metrics, not log content — so the function would keep
  invoking, keep sending real alerts, and keep reporting healthy on two of
  three alarms. **A partial blindness that looks like health.**

**Check, at the first live invocation** (`docs/runbooks/scheduled-cycle-deploy.md`'s
own "invoke once by hand" step, once written in Phase 4):

1. Confirm the `as_json` line for that invocation actually appears in
   `FunctionLogs` (CloudWatch Logs console or `aws logs tail`, run by the
   owner under the `ferrenafe` profile — not by `sdd-apply`).
2. If it is absent and the invocation's own return value / `REPORT` line
   shows no error, suspect `logs:CreateLogGroup`. Confirm via CloudTrail
   (`logs:CreateLogGroup` `AccessDenied` for the execution role) before
   widening the grant.
3. **Do not add `logs:CreateLogGroup` speculatively.** The point of this
   entry is to verify whether it is needed, not to widen the grant on a
   guess — this stack's own IAM grants are otherwise exact-scoped
   throughout, and an unverified addition would be the one exception with
   no evidence behind it.
