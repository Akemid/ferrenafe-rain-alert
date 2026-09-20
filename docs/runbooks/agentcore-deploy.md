# Deploying the composer agent to Bedrock AgentCore Runtime

Documentation only — nothing here is automated. Design spec §3.7 chose the
AgentCore CLI (starter toolkit) over CDK for this one unit specifically,
because it is not infrastructure in the CloudFormation sense: it is a model
deployment with its own purpose-built tool. The rest of this project's
infrastructure (change 3) is CDK in TypeScript; this unit is the deliberate
exception, and this runbook exists so that exception has one place to live.

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
  `anthropic.claude-haiku-4-5` in the target region (`agent/app.py::MODEL_ID`).
  Model access is granted per-account, per-region, in the Bedrock console
  under "Model access", and is not automatic.
- AWS credentials configured locally (`aws configure` or an SSO profile) with
  permission to create and invoke an AgentCore Runtime. AgentCore Runtime's
  own execution role is provisioned by the CLI at deploy time; this change
  does not create or assume any IAM resource (task 3.4 — IAM belongs to
  change 3, and this runtime's own role is the toolkit's concern, not this
  application's).
- Python 3.12, matching `agent/.python-version`.

## Step 1 — Install the starter toolkit

```bash
cd agent
uv sync
uv run pip install bedrock-agentcore-starter-toolkit
```

The toolkit is a deploy-time tool, not a runtime dependency — it does not
belong in `agent/pyproject.toml`, which pins only what `agent/app.py` imports
at invocation time (`strands-agents`, `bedrock-agentcore`, `pydantic`).

## Step 2 — Configure the runtime

```bash
cd agent
uv run agentcore configure --entrypoint app.py --name rain-alert-composer
```

This inspects `agent/app.py`, finds the `@app.entrypoint`-decorated function
(`compose_message`), and prepares a container image and runtime
configuration. It does not deploy anything yet.

**Confirm the exact flags against `agentcore --help` before running.** The
starter toolkit's CLI surface was not exercised in this session and this
runbook is documentation, not a tested script; if a flag name above has
changed, the toolkit's own `--help` output and
<https://docs.aws.amazon.com/bedrock-agentcore/> are authoritative over this
file.

## Step 3 — Launch

```bash
uv run agentcore launch
```

This builds the container, pushes it, and creates (or updates) the AgentCore
Runtime. On success the CLI prints the runtime's ARN — record it, it is the
one value the rest of this runbook needs.

## Step 4 — Verify model access before the first real invocation

A wrong or unconfigured `MODEL_ID` fails every call. Confirm access to
`anthropic.claude-haiku-4-5` (or the cross-region inference-profile variant,
`us.anthropic.claude-haiku-4-5`, if the account requires one — `agent/app.py`
records this as an account fact, not a code fact) in the Bedrock console for
the deployed region before wiring this application to the runtime.

## Step 5 — Wire this application to the deployed runtime

Three environment variables, all read by
`src/rain_alert/adapters/local/static_config_repository.py` and
`src/rain_alert/adapters/agentcore_invoker.py::AgentRuntimeSettings.from_env`:

| Variable | Required | Meaning |
|---|---|---|
| `RAIN_ALERT_COMPOSER` | No — defaults to `template` | Set to `agent` to select the agent-backed composer (design D23). The change ships inert; this is the switch. |
| `RAIN_ALERT_AGENT_RUNTIME_ARN` | Yes, once `RAIN_ALERT_COMPOSER=agent` | The ARN printed by `agentcore launch` in step 3. |
| `RAIN_ALERT_AGENT_TIMEOUT_S` | No — defaults to 10 seconds | The read-timeout half of D19's deadline. Tunable without a code change once cold-start latency is measured (task 3.5/3.16). |

For a one-off local run without touching the environment, use the CLI flag
instead (design D24):

```bash
RAIN_ALERT_AGENT_RUNTIME_ARN="arn:aws:bedrock-agentcore:<region>:<account>:runtime/<id>" \
  uv run rain-alert-cycle --composer agent
```

The AWS credential chain (`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/
`AWS_SESSION_TOKEN`, an SSO profile, or an execution role once change 3
exists) must resolve independently — `boto3.client("bedrock-agentcore")` is
built lazily on first invocation (design D18) and needs a region, from
`AWS_REGION`/`AWS_DEFAULT_REGION`/the shared config file. This application
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
3. **`agentcore` toolkit teardown** (delete the runtime). Confirm the exact
   command against `agentcore --help`; not exercised in this session.
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
