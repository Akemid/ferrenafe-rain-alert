# Proposal: Scheduled Cycle (EventBridge → Lambda → SSM + DynamoDB, composing with the agent)

Source of truth: `docs/superpowers/specs/2026-09-03-rain-alert-core-design.md` sections 3.5, 3.6, 3.7, 4, 9 and 10.3. This proposal slices that design; it does not restate it and it does not reopen section 3.

Change 3 of 3. Change 1 (`core-alert-cycle`) is archived. Change 2 (`composer-agent`) is merged and the AgentCore runtime is deployed and live in `us-east-2`.

## Intent

Two facts, both true today on `main`:

1. **The public page freezes at whatever was last published by hand.** `S3SnapshotPublisher` works and the bucket is deployed, but nothing runs the cycle on a timer. A resident opening the page reads a verdict a person typed a command to produce, whenever they last remembered to. The page is the hackathon's ship gate and the community's only self-serve signal, and its freshness currently depends on a human running `uv run rain-alert-cycle`.
2. **The agent is deployed and unused.** `ferrenaferainalert_rainalert-upwZ4M5wl5` is `READY`, was invoked live on 2026-09-21, and composed a correct Spanish message. Every snapshot published since says `composed_by: "template"`, because nothing sets `RAIN_ALERT_COMPOSER=agent` on a scheduled path — there is no scheduled path.

This change closes both: EventBridge Scheduler runs the cycle every six hours on Lambda, configuration comes from SSM Parameter Store, dedup and alert history live in DynamoDB, and the scheduled cycle composes with the agent.

## Scope

### In Scope

- **Lambda handler** (`entrypoints/lambda_handler.py`) plus a cloud wiring function, and a shared cycle-runner extracted from `cli.py:main` so both entry points run the same sequence (see *The entry-point question*).
- **EventBridge Scheduler**, six-hourly, targeting the Lambda. The rate is IaC, not configuration (design 3.5).
- **`SsmConfigRepository`** — a second `ConfigRepository`, reading the operator-editable subset of `AlertConfig` from Parameter Store (standard tier).
- **`DynamoDbAlertRepository`** — a second `AlertRepository` over the `alerts-sent` table, carrying both collections (`alerts`, `active_outage`) through the existing `adapters/serialization.py`, which was written for exactly this.
- **One CDK stack in TypeScript** in `infra/`: Lambda, Scheduler, the table, the SSM parameters, the execution role. Region `us-east-2`.
- **Agent in the loop**: `RAIN_ALERT_COMPOSER=agent` and `RAIN_ALERT_AGENT_RUNTIME_ARN` as Lambda environment, with the Lambda timeout sized around `READ_TIMEOUT`.
- **Correcting the stale region** in `docs/superpowers/specs/2026-09-03-rain-alert-core-design.md` §10.3 and in `CLAUDE.md`.
- **Deploy runbook** for this stack, matching the pattern of `docs/runbooks/agentcore-deploy.md` and `public-page-deploy.md`.

### Out of Scope

- **The `contacts` DynamoDB table, and `ContactRepository`.** Deferred deliberately. It holds personal data, and **no real delivery channel exists** — `ConsoleNotifier` is the only `Notifier` in this codebase, by design (`wiring.py` can construct nothing else). A contacts table would be a personal-data store that nothing reads usefully: all cost, all risk, no behaviour. `StaticContactRepository`'s one synthetic `stdout` contact continues to stand in.
- **Real delivery** (Telegram, SES, WhatsApp). The Lambda's alerts land in CloudWatch Logs, exactly as they land on a terminal today.
- **The admin API / subproject 2** (writing SSM, changing the schedule rate at runtime, Cognito).
- **Rewriting the public status page or its bucket.** `PublicSnapshotStack` is deployed and live and is not touched except to consume what it already exports.
- Alarms, dashboards, X-Ray, retention tuning beyond a log-retention setting.

### Why `alerts-sent` is *not* deferred alongside `contacts`

`JsonAlertRepository` writes to a local `Path`. In Lambda that path is `/tmp`, which is per-execution-environment and discarded — and because the schedule is six-hourly while the idle session/environment lifetime is far shorter, **every invocation is cold**. Dedup state would therefore be lost between every run.

The consequence is not "dedup degrades". It is:

- `should_send` would see an empty history on every cycle, so a standing `prepare` would re-alert every six hours. The one thing the design says must never happen (*"repeated or false alerts make people stop reading"*, §1) would happen four times a day.
- `_recent_alerts` would always return empty, so the page's "alertas enviadas" history would never accumulate.
- Nothing would fail, log an error, or look wrong. The rule would be **silently inert in production**.

That is the failure mode `CLAUDE.md` names — a check that looks like it ran and did not. It is the reason this table ships in phase 1 and the other does not.

## Capabilities

### New Capabilities

- `scheduled-execution`: the Lambda entry point contract, the six-hourly trigger, the timeout invariant, what a failed invocation must and must not do, and what the scheduled path observably shares with the CLI path.
- `cloud-configuration`: the SSM-backed `ConfigRepository` — which keys exist, which are operator-editable, parse/validation posture on a bad value, and what stays in code.
- `alert-persistence`: the DynamoDB-backed `AlertRepository` — key design, the range-query contract, read consistency, the two collections, and TTL.

### Modified Capabilities

- `local-alert-cli`: no observable behaviour change, and that becomes an explicit requirement. Extracting the shared runner introduces the possibility of drift between the calibrated local path and the production path; the spec gains the invariant that both entry points execute the same sequence over the same `CycleResult`.

## Approach

### The entry-point question — recommendation

**Add a second entry point. Do not reuse `cli.py:main`. Extract the middle.**

`main`'s contract is `argv`, two text streams, and a process exit code. Every one of those is meaningless under `(event, context)`: a Lambda handler would have to synthesise a fake command line and parse it on each of 120 invocations a month, and an exit code is not how Lambda reports failure. Worse, `build_local_deps` carries a documented structural guarantee — *"this function can only construct `ConsoleNotifier`"* — and a cloud path that reused it would either inherit `JsonFileAlertRepository` and `StaticConfigRepository` (the failure above) or force branches into the one function whose value is that it has none.

But the part worth sharing is not `main`; it is the six lines `main` performs *after* dependencies exist: load config, run the cycle, publish the snapshot inside the forgiving guard, report. That sequence is the calibrated behaviour, and letting the cloud path re-implement it is how a bug reaches residents that the CLI never shows an owner.

So:

| Piece | Where | Note |
|---|---|---|
| `run_once(deps, publisher) -> CycleResult` | `application/` or `entrypoints/` | The shared sequence, including the snapshot guard and `SNAPSHOT_FAILURE_PREFIX` |
| `cli.py:main` | unchanged externally | argparse, rendering, exit code; delegates to `run_once` |
| `lambda_handler.handler(event, context)` | new | no argparse, no exit code; delegates to `run_once`, emits the `as_json` document to the log |
| `build_local_deps` / `build_cloud_deps` | `wiring.py` | two leaf sets, one `CycleDependencies` type — exactly the D6 pattern `tests/support/wiring.py` already mirrors |

### Configuration: what moves to SSM, and what the design got wrong

The design names four things (§9): coordinates, thresholds, active channel, checklist. `AlertConfig` as it exists today carries twelve fields. The mismatch is real and the spec phase must resolve it:

| Field | Design named it | Recommendation |
|---|---|---|
| `coordinates` | yes | **SSM.** Calibration input. |
| `coordinates_source` | **no — added after the design** | **SSM, with the pair.** It is printed on every run and published in the snapshot; an operator-supplied SSM point that still reports `public_reference` is a lie in the operator's own audit trail. It must travel with the coordinates, never be inferred. |
| `thresholds` (7 values) | yes | **SSM.** The design's whole reason for Parameter Store: *"hypotheses to calibrate during the first rainy season"*. |
| `checklist` | yes | **SSM.** Spanish by contract; the one recipient-facing string that is operator copy. |
| `active_channel` | yes | **SSM, but validated.** Today the only valid value is `console`, because no other `Notifier` exists. An unvalidated free-text key offers the operator a choice with one correct answer and silently does nothing for the others. |
| `composer` | **no — added by change 2** | **SSM.** This is the kill switch, and D23 already says *"`AlertConfig` is what SSM feeds in change 3, so flipping it there needs no deploy"*. Putting it anywhere else discards the rollback ladder. |
| `forecast_hours`, `dedup_lookback_hours` | **no** | **SSM.** They are calibration horizons and belong with the thresholds. |
| `city`, `region`, `timezone` | **no** | **Code.** Identity of the monitored place, not a tuning knob. |
| `city_slug` | **no** | **Code — and this one is a hazard.** It is the DynamoDB partition key. An operator editing it in SSM strands the entire dedup history under the old key, and the next cycle re-alerts a community that was already told. It must not be operator-editable in phase 1. |
| agent ARN, agent timeout | n/a | **Lambda environment, not `AlertConfig`.** D23 is explicit: an ARN is an AWS concern and must not widen the frozen core. CDK sets `RAIN_ALERT_AGENT_RUNTIME_ARN` and `RAIN_ALERT_AGENT_TIMEOUT_S` directly. |

Failure posture: a missing or unparseable parameter **raises**, matching `StaticConfigRepository`'s existing strictness on `RAIN_ALERT_LAT`/`LON` and `RAIN_ALERT_COMPOSER`. Falling back to a built-in default would forecast for the wrong point, or run thresholds nobody chose, and say nothing.

### Reaching the agent: IAM, region, timeout

**Timeout is the load-bearing constraint, not a detail.**

`adapters/agentcore_invoker.py` sets `READ_TIMEOUT = 35.0` and `CONNECT_TIMEOUT = 3.0`, sized from ten measured cold starts of 7.00–10.27 s on 2026-09-21. Every production invocation is cold (`idleSessionTimeout` 900 s against a six-hourly cycle — measured, not assumed). Strands additionally retries *inside the container* (`reached max retries: 4` observed), where `total_max_attempts: 1` on our client has no reach.

A Lambda timeout shorter than 35 s does not degrade gracefully. It kills the process mid-call: `AgentBackedComposer`'s `except` never runs, the template fallback never runs, the notifier never fires, the snapshot is never published, and **no alert is sent at all**. That is strictly worse than any failure the fallback was designed to absorb.

The requirement is therefore a relation, not a number: **Lambda timeout MUST exceed `RAIN_ALERT_AGENT_TIMEOUT_S` plus the worst case of the rest of the cycle** (SENAMHI scrape, Open-Meteo fetch, SSM read, DynamoDB read/write, S3 put). Proposed 120 s; the CDK test should assert the relation against the invoker's own constant rather than pin the integer.

IAM, on the execution role:

| Grant | Resource |
|---|---|
| `bedrock-agentcore:InvokeAgentRuntime` | the one runtime ARN (exact action/resource confirmed at design time against the live service model, per the precedent set in `agentcore_invoker.py`) |
| DynamoDB read/write | the one table, no `*` |
| `ssm:GetParameter(s)` / `GetParametersByPath` | one path prefix |
| **the existing `SnapshotWriter` managed policy** | attached, **not** re-granted |
| CloudWatch Logs | the function's log group |

That last row is the point. `PublicSnapshotStack` already defines a customer-managed policy granting `s3:PutObject` on exactly `status.json` and nothing else — no `DeleteObject`, no `PutBucketPolicy` — and its own comment says it exists so *"no principal is named because today that is a developer's session and in change 3 it is a Lambda's role."* This change attaches it. Inventing a fresh S3 grant here would silently reopen a decision that was already made carefully.

The Lambda needs **no Bedrock model permission**. The model call happens inside the AgentCore runtime under the execution role the toolkit provisions; this role only invokes.

Region is `us-east-2`, matching the runtime and the snapshot bucket. `boto3` resolves it from the Lambda environment; this project deliberately exposes no `RAIN_ALERT_*` region knob.

### Region: the docs are stale, correcting them is in scope

Design §10.3 says `us-east-1`. `CLAUDE.md` says *"Deployment target is `us-east-1`"*. Both predate 2026-09-21, when `us-east-1`'s daily Bedrock token quota was exhausted (`ModelThrottledException: Too many tokens per day`) and the runtime moved. `infra/bin/infra.ts` already pins `us-east-2` with that rationale in a comment. Two authoritative documents currently point a future operator at the wrong region; this change fixes both.

## Cost

| Item | Phase-1 volume | Cost |
|---|---|---|
| Lambda | ~120 invocations/month, seconds each | Free tier |
| EventBridge Scheduler | ~120 invocations/month | Free tier |
| DynamoDB (on-demand) | ~120 reads, a handful of writes | Free tier / cents |
| SSM Parameter Store (standard) | ~10 parameters, 120 reads | Free |
| S3 | one versioned object | Cents |
| **Bedrock + AgentCore** | **only when an alert is new or escalated** | Fractions of a cent per composition; nothing on a deduplicated or `none` cycle — structurally, the composer sits inside the authorized-send branch |

Two line items that are not zero and grow silently, and are therefore in scope to set explicitly: **CloudWatch Logs retention** (default is never-expire) and **S3 object versions** on a bucket written four times a day forever. Exact free-tier figures are confirmed at design/tasks time, not asserted here.

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| **The submission deadline is 2026-10-02; judging runs 2026-10-05 → 10-12.** This change replaces a hand-published, known-good `status.json` with an automated writer, days before judges open the page | **High impact** | Deploy early enough to watch several real cycles before 10-02. The hand-publish path still works and is the rollback. The snapshot publish is already best-effort — a publish failure cannot cost the alert, and it announces itself via `SNAPSHOT_FAILURE_PREFIX` |
| The first agent-composed message reaches residents with no human reading it first | Medium | `message_validation.py` + deterministic fallback + the SSM kill switch. Accepted consciously: this is what putting the agent in the loop means |
| Lambda timeout set below the invoker's deadline kills the fallback | Medium | Stated above as an invariant with a CDK assertion, not a tuning note |
| `us-east-2` Bedrock quota exhausts as `us-east-1` did | Medium | Fallback to the template is automatic; visible only as an operator notice in CloudWatch Logs, which nobody watches. Named as an accepted phase-1 gap |
| Operator notices (SENAMHI outage, agent fallback) go to `ConsoleNotifier` → CloudWatch Logs → nobody | **High** | Accepted for phase 1 and stated plainly. Real delivery is out of scope; the deferral is the whole reason `contacts` is deferred too |
| DynamoDB key design does not support `alerts_with_window_start_between` | Medium | PK `city_slug` + SK derived from `TimeWindow.key()` — already anticipated in `cli.py::default_now`'s docstring. Consistent reads for the dedup query; eventual consistency costs correctness and saves nothing at this volume |
| Unbounded table growth (the `alerts` list already grows forever in JSON) | Low | DynamoDB TTL attribute, as `json_alert_repository.py` already proposes |
| Cross-stack coupling to the deployed `PublicSnapshotStack` creates a deadly embrace on the exported policy ARN | Medium | Prefer passing the ARN in as a prop/context value over `Fn::ImportValue`; design phase decides |
| Scraper breaks in production and the page shows a confident `none` | Medium | Already handled by change 1: a broken parse reports `unavailable`, never "zero warnings", and the page renders `degraded` and `sources` (fixed during `public-status-page`) |
| Slice exceeds the 400-line review budget | **High** | Chained PRs expected. Every prior change overran. `sdd-tasks` owns the forecast |

## Rollback Plan

Five levels, cheapest first:

1. **Disable the schedule.** Set the EventBridge Scheduler to `DISABLED`. The page freezes at the last good snapshot — exactly today's behaviour — and nothing else changes.
2. **Flip the composer** to `template` in SSM. No deploy. The cycle keeps running, the agent leaves the loop.
3. **Publish by hand.** `uv run rain-alert-cycle` with `RAIN_ALERT_SNAPSHOT_BUCKET` set still works and is untouched by this change.
4. **`cdk destroy` the new stack.** `PublicSnapshotStack` and the AgentCore runtime are separate stacks and survive. Confirm the removal order for the imported policy ARN before destroying.
5. **Revert the commits.** `lambda_handler.py`, `build_cloud_deps`, the two new adapters and the new CDK stack are this change's own files. The CLI, the domain and the existing stack are unchanged, so reverting returns the system to "runs when a person runs it".

Data: the `alerts-sent` table is the only new state. It is derived — losing it costs dedup history, not an alert.

A security review runs on the branch diff before every pull request, per project practice.

## Dependencies

**Inbound (satisfied):** `AlertRepository` and `ConfigRepository` ports; `adapters/serialization.py`, written for this table; `S3SnapshotPublisher` and the deployed `PublicSnapshotStack` with its `SnapshotWriter` policy; `AgentBackedComposer`, `BedrockAgentCoreInvoker` and the live AgentCore runtime; `message_validation.py`; `infra/` with CDK + jest already wired.

**External:** AWS account `ferrenafe` profile, `us-east-2` CDK-bootstrapped (done 2026-09-21); Bedrock model access for `us.anthropic.claude-haiku-4-5-20251001-v1:0` in `us-east-2` (confirmed); `botocore[crt]` for `aws login` credentials in the root project.

**Nothing downstream depends on this change** — it is the last of the three.

## Success Criteria

- [ ] The public page's `evaluated_at` advances on its own, four times a day, with no human involved.
- [ ] A published snapshot reads `composed_by: "agent"` at least once, and the message is recorded in the change artifacts for the owner to judge.
- [ ] A standing `prepare` across two consecutive cycles sends **once**, proven against the real DynamoDB table (moto in the suite, plus one live observation).
- [ ] Killing the agent (bad ARN) still sends the template alert and still publishes the snapshot, with exactly one `AGENT_FALLBACK_USED` notice in the log.
- [ ] The Lambda timeout is strictly greater than `agentcore_invoker.READ_TIMEOUT`, asserted by a CDK test that reads the Python constant's value rather than a copy of it.
- [ ] The Lambda role holds no `s3:PutObject` statement of its own; it attaches the existing `SnapshotWriter` managed policy.
- [ ] `uv run pytest` still passes offline, with no network, no credentials and no model calls.
- [ ] `uv run rain-alert-cycle` behaves byte-identically to `main` today.
- [ ] No twelve-digit account id and no ARN containing one enters the repository (`tests/hygiene/test_repo_hygiene.py`).
- [ ] `CLAUDE.md` and design §10.3 name `us-east-2`.

## Proposal question round

The executor could not ask interactively. These are the product questions whose answers would sharpen the spec; each has a default recorded so the change is not blocked.

1. **Does the page need to distinguish "the cycle ran and found nothing" from "the cycle has not run"?** Today a frozen page and a healthy calm page look identical to a resident. A `evaluated_at` staleness indicator is a page change, currently out of scope. *Default: out of scope; noted as a known gap.*
2. **Should `active_channel` be operator-editable in SSM at all in phase 1**, given `console` is the only valid value? *Default: yes, present but strictly validated — it keeps the parameter shape stable for the delivery change.*
3. **Six hours, or something tighter during a rainy season?** The design fixed six hours; the rate is IaC and changing it is a redeploy. *Default: six hours, unchanged.*
4. **Is a CloudWatch alarm on Lambda errors in scope before judging?** It is the only thing that would tell the owner the cycle stopped. *Default: out of scope; the frozen page is the de-facto signal, which is weak and is named as a risk.*
5. **How long should the `alerts-sent` TTL be?** `dedup_lookback_hours` is 72, but the page shows history and the record is an audit trail. *Default: well above the dedup window — the spec phase picks the number.*
