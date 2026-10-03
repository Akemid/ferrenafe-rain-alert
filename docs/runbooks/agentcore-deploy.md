# Deploying the composer agent to Bedrock AgentCore Runtime

Documentation only — nothing here is automated. The agent runtime is deployed
with the Node **AgentCore CLI** (`agentcore` 0.30.0). Its project config is
`agentcore/agentcore.json`; the CLI drives CDK underneath, so a deploy is a
CloudFormation change set, not a container push. The build artifact is a
CodeZip of `agent/`. The deploy target is `default` (us-east-2), declared in
the gitignored `agentcore/aws-targets.json` (copy
`agentcore/aws-targets.json.example`). The real first deploy and its output
are recorded in
`docs/evidence/2026-09-21-first-live-deploy-and-invocation.md`.

Resolves `openspec/changes/composer-agent/state.yaml → open_decisions.runbook-location`.

## Scope

This deploys exactly one thing: the `agent/` unit (`agent/app.py`,
`agent/models.py`) as an AgentCore Runtime. It does **not** deploy the rest of
the application — no Lambda, no scheduler, no DynamoDB table. Those belong to
change 3. Until change 3 exists, this runtime is invoked from a developer's
machine or from `uv run rain-alert-cycle --composer agent`, against
`src/rain_alert/adapters/agentcore_invoker.py`.

## Prerequisites

- An AWS account with Bedrock model access enabled for
  `us.anthropic.claude-haiku-4-5-20251001-v1:0` in the target region
  (`agent/app.py::MODEL_ID`). Model access is granted per-account,
  per-region, in the Bedrock console under "Model access", and is not
  automatic.
- AWS credentials configured locally (`aws login`, profile `ferrenafe`) with
  permission to create and invoke an AgentCore Runtime. AgentCore Runtime's
  own execution role is provisioned by the CLI at deploy time; this change
  does not create or assume any IAM resource (task 3.4 — IAM belongs to
  change 3, and this runtime's own role is the CLI's concern, not this
  application's).
- Python 3.12, matching `agent/.python-version`.
- The `agentcore` CLI (Node) installed, and `agentcore/aws-targets.json`
  present with a `default` target in `us-east-2`.
- AWS CLI profile `ferrenafe`, authenticated with `aws login`.

## Step 1 — Preview the change (read-only)

```bash
AWS_PROFILE=ferrenafe agentcore deploy --diff -y
```

`--diff` changes nothing. Read it: for a code or prompt change expect a
**single `[~]` in-place change on the Runtime's code**. A `[-]`/`[+]`
replacement is a stop sign: a replaced Runtime gets a new ARN, and the cycle
Lambda holds the old one in `RAIN_ALERT_AGENT_RUNTIME_ARN`.

## Step 2 — Deploy

```bash
AWS_PROFILE=ferrenafe agentcore deploy -y
```

This builds the CodeZip of `agent/`, runs the CDK deployment, and updates the
Runtime. On success the CLI prints the `RuntimeArn` (record it masked as
`arn:aws:bedrock-agentcore:us-east-2:<account>:runtime/<id>`).

## Step 3 — Verify the Runtime is ready

Call `GetAgentRuntime` (control plane, `bedrock-agentcore-control`) for the
runtime id and confirm `status` is `READY` and `agentRuntimeVersion` is the
version you expect (it increments on every code change). Evidence of a live
verification: `docs/evidence/2026-10-03-agent-system-prompt-live.md`.

## Step 4 — Verify model access before the first real invocation

A wrong or unconfigured `MODEL_ID` fails every call.

**An inference profile is required — it is not optional and not regional.**
This model reports `inferenceTypesSupported: ['INFERENCE_PROFILE']`, with no
`ON_DEMAND`, so the bare foundation-model id cannot be invoked anywhere.
Confirm the profile rather than the model:

```bash
aws bedrock get-foundation-model --region <region> --output json \
  --model-identifier anthropic.claude-haiku-4-5-20251001-v1:0
# → inferenceTypesSupported: ["INFERENCE_PROFILE"]

aws bedrock list-inference-profiles --region <region> --output json \
  --query 'inferenceProfileSummaries[?contains(inferenceProfileId, `haiku-4-5`)].[inferenceProfileId,status]'
# → us.anthropic.claude-haiku-4-5-20251001-v1:0   ACTIVE
```

`--output json` matters if the shell runs a token-compressing command proxy:
without it the CLI may return the response *schema* instead of the values, and
it survives both redirection and piping. See
`docs/evidence/2026-09-20-preflight.md`.

## Step 5 — Wire this application to the deployed runtime

Three environment variables, all read by
`src/rain_alert/adapters/local/static_config_repository.py` and
`src/rain_alert/adapters/agentcore_invoker.py::AgentRuntimeSettings.from_env`:

| Variable | Required | Meaning |
|---|---|---|
| `RAIN_ALERT_COMPOSER` | No — defaults to `template` | Set to `agent` to select the agent-backed composer (design D23). The change ships inert; this is the switch. |
| `RAIN_ALERT_AGENT_RUNTIME_ARN` | Yes, once `RAIN_ALERT_COMPOSER=agent` | The ARN printed by `agentcore deploy` in step 2. |
| `RAIN_ALERT_AGENT_TIMEOUT_S` | No — defaults to 10 seconds | The read-timeout half of D19's deadline. Tunable without a code change once cold-start latency is measured (task 3.5/3.16). |

For a one-off local run without touching the environment, use the CLI flag
instead (design D24):

```bash
read -rs RAIN_ALERT_AGENT_RUNTIME_ARN && export RAIN_ALERT_AGENT_RUNTIME_ARN   # not in shell history
uv run rain-alert-cycle --composer agent
```

The AWS credential chain (`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/
`AWS_SESSION_TOKEN`, an SSO profile, or an execution role once change 3
exists) must resolve independently — `boto3.client("bedrock-agentcore")` is
built lazily on first invocation (design D18) and needs a region, from
`AWS_DEFAULT_REGION` or the profile in the shared config file. **botocore does
not read `AWS_REGION`** for this: with `AWS_PROFILE=ferrenafe AWS_REGION=us-east-2`
it used the profile's region and reached the stale `us-east-1` runtime
(`docs/evidence/2026-10-02-agent-composer-switched-on.md` §3). Use
`AWS_DEFAULT_REGION=us-east-2`, or a profile whose `region` is `us-east-2`.
Inside Lambda the runtime sets `AWS_DEFAULT_REGION` itself. This application
reads no separate `RAIN_ALERT_*` region variable; a second, redundant knob
would only be one more thing that could disagree with the first.

## Step 6 — First live run, and what to record

```bash
uv run rain-alert-cycle --composer agent
```

Record three things from this run, per task 3.16 and design §13's
open verification items:

1. **The composed message**, so an owner can judge its wording before it
   reaches a recipient.
2. **Measured cold-start latency** — the input that would move `READ_TIMEOUT`
   in `src/rain_alert/adapters/agentcore_invoker.py`. The cycle runs every six
   hours, so no warm reuse should be expected; this measurement is what
   confirms or corrects that assumption.
3. **The exact model id string used**, in case the account requires the
   cross-region inference-profile prefix.

If nothing was sent (`Decision NOT SENT`), the cycle never invoked the agent
at all — composition sits inside the authorized-send branch
(`application/run_alert_cycle.py`), and a calm forecast costs nothing. Force
an authorized send with `--offline-fixtures` pointed at a fixture pair that
clears a threshold to observe a real invocation.

## Changing the system prompt

The agent's fixed rules live in `agent/prompts.py::SYSTEM_PROMPT` (change
`agent-system-prompt`, design D48-D54). Changing them changes what is sent to
residents, so the order below is not optional. Production never runs agent code
that has not passed review and the live gate.

1. **Offline gate green** on the branch: `uv run pytest`,
   `uv run --directory agent pytest`, `uv run ruff check`,
   `uv run ruff format --check`, `uv run mypy`. The wording changes test-first:
   a failing test names the rule, then the text.
2. **Fresh-context reviews passed**: security AND correctness, on the branch
   diff. Both. `agentcore deploy` is allowed from an unmerged branch only after
   this step.
3. **Deploy from the branch** (steps 1 and 2 above): first
   `AWS_PROFILE=ferrenafe agentcore deploy --diff -y` and confirm a single `[~]`
   in-place Runtime code change, then `AWS_PROFILE=ferrenafe agentcore deploy -y`,
   then verify `READY` plus the version (step 3). Record the command and its real output in `docs/evidence/`, ARN masked as
   `arn:aws:bedrock-agentcore:us-east-2:<account>:runtime/<id>`.
4. **Run the 10-call live gate** (paid: ten real model calls):

   ```bash
   read -rs RAIN_ALERT_AGENT_RUNTIME_ARN   # paste the ARN; not echoed, not in shell history
   export RAIN_ALERT_AGENT_RUNTIME_ARN
   AWS_PROFILE=ferrenafe AWS_DEFAULT_REGION=us-east-2 \
     uv run python scripts/agent_live_acceptance.py --runs 10
   ```

   Or skip the variable and add `--from-lambda` to read the ARN from the
   deployed `ferrenafe-rain-alert-cycle` configuration (preferred: nothing to
   paste). `--runs` is fixed at 10. The script passes the
   region explicitly (botocore ignores `AWS_REGION`), never prints the ARN or a
   fence token, and exits non-zero below 8 of 10 accepted. Acceptance is decided
   by the unchanged `validate_message`; the script only counts. If the SSM
   checklist was edited since exploration, confirm it still equals
   `static_config_repository.CHECKLIST` first.
5. **Record all ten outcomes in the evidence file**, failures included, from
   `docs/evidence/_template-agent-system-prompt-live.md`. No cherry-picking: a
   second batch never replaces the first, it is recorded as a new round.
6. **Iterate if below 8/10**: write a new failing offline test that names the
   observed rejection, change the wording, redeploy, run a fresh ten. Never
   loosen the validator. **Three rounds at most**, then escalate to the owner.
7. **Commit the evidence to the same PR**, re-run the offline gate, and merge.
   Merging is the owner's decision once the gate is met.
8. **Deploy the application** from `main`: `infra/scripts/build-lambda.sh`,
   `cdk diff` (read it: only the function code should change), then `cdk deploy`
   with the existing `-c` flags. The agent goes first on purpose: while it runs
   the new prompt and the app still sends the old rules, the rules are duplicated,
   which is harmless. The reverse order leaves the agent without rules and every
   draft falls back to the template.

Rollback, cheapest first:

- **Instant**: `RAIN_ALERT_COMPOSER=template` in SSM. The next cycle sends the
  deterministic template; no deploy.
- **Agent**: redeploy (`agentcore deploy -y`) from the `main` commit that preceded the change; the same `--diff` check applies.
  **Order matters once the app is trimmed.** Since `agent-system-prompt` PR 4, the app's user turn carries no
  composition rules; they live only in the agent's `SYSTEM_PROMPT` (runtime version 3 and later). Rolling the
  agent back below that while the trimmed app is live leaves the model with **no rules at all**. The validator
  still guards every message, but most drafts would be rejected and fall back to the template (the version 2
  baseline was 6/10 even *with* the rules in the user turn). So: flip `RAIN_ALERT_COMPOSER=template` first
  (instant), or revert the app trim before rolling the agent back.
- **App**: revert the commit, rebuild the asset, deploy.

## Rollback

Four levels, cheapest first (this is the composer-agent proposal's own
rollback ladder, restated here because a runbook is where an operator looks
for it under pressure):

1. **Flip the switch.** `RAIN_ALERT_COMPOSER=template` (or drop the CLI flag).
   No deploy needed; the next cycle uses the deterministic template.
2. **Point the runtime elsewhere or invalidate the ARN.** Forces every
   invocation to fail transport-wise, which the fallback already handles —
   the community still gets the template's message, with one operator notice
   per cycle (`AGENT_FALLBACK_USED`).
3. **`agentcore` teardown** (delete the runtime). `agentcore remove agent`
   removes the agent from the project config (`agentcore/agentcore.json`); a
   following `agentcore deploy` would then delete it through CDK. That
   sequence is inferred from `agentcore remove --help` (CLI 0.30.0) and has
   **not been exercised**. Prefer levels 1 and 2, and if you do get here,
   preview with `agentcore deploy --diff -y` first.
4. **Revert this change's commits.** `agent/`, `agentcore_invoker.py`,
   `select_composer`, and the CLI flag are all this slice's own files —
   reverting them returns the application to template-only, exactly as it
   behaved before this change existed.

## What this runbook does not cover

- Change 3's infrastructure (the Lambda, the scheduler, DynamoDB, IAM for the
  application's own execution role). This unit is deployed independently of
  all of it.
- Automating any of the steps above. Every command here is run by hand, once,
  by the owner — that is the point of "documentation only, not automation"
  (design 3.7).
