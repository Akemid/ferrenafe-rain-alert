# Letting the composer agent read recent alerts

Documentation only. Nothing here is automated, and nothing here has been
exercised against a live account yet: the policy and the seed script ship in
PR 2 of `recent-alerts-tool`, and the live smoke is task 3.4. Account ids and
ARNs are written `<account>` and `<...>` on purpose; never paste a real one
into a commit, an issue or a log.

The agent's `get_recent_alerts` tool queries the alerts table
(`ferrenafe-alerts-sent`) from inside the AgentCore runtime. That runtime runs
as an execution role the **agentcore CLI provisions**, which `ScheduledCycleStack`
does not own. So the stack creates a managed policy, `AlertHistoryReader`, and
the operator attaches it to that role by hand. Until it is attached the tool
is denied, degrades to an empty history (design D6) and the alert is still
sent, only without continuity. That is the dangerous part: **a missing or
wrong grant never fails loudly.** The checks below exist to catch it.

## What the policy allows

`dynamodb:Query` on the table ARN, and only when all of these hold:

| Condition | Value | Why |
|---|---|---|
| `ForAllValues:StringEquals dynamodb:LeadingKeys` | `ALERT#ferrenafe`, `ALERT#demo-ferrenafe` | Only those two partitions can be read. |
| `Null dynamodb:LeadingKeys` | `false` | `ForAllValues` is true when the key is absent; this demands it be present. |
| `ForAllValues:StringEquals dynamodb:Attributes` | `pk, sk, sent_at, level, reasons` | The agent's query projects exactly these. A prior alert's title and body cannot be read. `pk` and `sk` must be listed: AWS requires key attributes in any `Attributes` list. |
| `StringEquals dynamodb:Select` | `SPECIFIC_ATTRIBUTES` | `Attributes` is evaluated only on attributes the request names, so a Query with no projection would read everything. Requiring `Select` forces the projection. Plain `StringEquals`, not `IfExists`. |

If `agent/history.py` ever projects another attribute, or its
`KeyConditionExpression` names another one, the policy must change in the same
PR (`infra/test/scheduled-cycle-stack.test.ts` pins the list). Otherwise the
read is denied and history is silently empty.

## Step 1 - Deploy the policy (owner action)

```bash
cd infra
AWS_PROFILE=ferrenafe npx cdk deploy ScheduledCycleStack <your usual -c context flags>
```

Then read the generated policy name from the stack outputs (outputs carry
names, never ARNs):

```bash
POLICY_NAME=$(AWS_PROFILE=ferrenafe aws cloudformation describe-stacks \
  --stack-name ScheduledCycleStack --region us-east-2 \
  --query "Stacks[0].Outputs[?OutputKey=='AlertHistoryReaderPolicyName'].OutputValue" \
  --output text)
```

## Step 2 - Attach it to the AgentCore execution role

Find the role the CLI provisioned for the runtime (the runtime id is the last
segment of `RAIN_ALERT_AGENT_RUNTIME_ARN`):

```bash
ROLE_NAME=$(AWS_PROFILE=ferrenafe aws bedrock-agentcore-control get-agent-runtime \
  --agent-runtime-id <runtime-id> --region us-east-2 \
  --query roleArn --output text | sed 's#.*/##')

AWS_PROFILE=ferrenafe aws iam attach-role-policy \
  --role-name "$ROLE_NAME" \
  --policy-arn "arn:aws:iam::<account>:policy/$POLICY_NAME"
```

## Step 3 - Post-deploy check (must show a NON-EMPTY demo read)

Do not stop at "the attach command succeeded". An attached but wrong policy
looks exactly like a working one until a read comes back empty.

1. Seed one demo day (writes only to `ALERT#demo-ferrenafe`):

   ```bash
   AWS_PROFILE=ferrenafe AWS_DEFAULT_REGION=us-east-2 \
     uv run python scripts/seed_demo_history.py --day 1
   ```

2. Invoke the runtime with the demo binding and `as_of` on the next day's
   window start, so day 1 is "yesterday" and the tool must report
   `consecutive_days` 2. The cycle Lambda does not send `history` yet (a later
   PR), so use a one-off call:

   ```bash
   AWS_PROFILE=ferrenafe AWS_DEFAULT_REGION=us-east-2 \
   RAIN_ALERT_AGENT_RUNTIME_ARN=<arn> uv run python - <<'PY'
   import importlib.util, json, os, uuid
   import boto3
   from rain_alert.adapters.agent_prompt import _fence_token, build_prompt

   spec = importlib.util.spec_from_file_location("acceptance", "scripts/agent_live_acceptance.py")
   acceptance = importlib.util.module_from_spec(spec)
   spec.loader.exec_module(acceptance)
   incident_request = acceptance.incident_request

   prompt = build_prompt(incident_request(), token_factory=_fence_token)
   payload = {
       "prompt": prompt,
       "history": {
           "partition": "demo-ferrenafe",
           "as_of": "2026-10-21T05:00:00+00:00",
           "current_level": "imminent",
           "current_warning_level": "orange",
       },
   }
   client = boto3.client("bedrock-agentcore", region_name="us-east-2")
   out = client.invoke_agent_runtime(
       agentRuntimeArn=os.environ["RAIN_ALERT_AGENT_RUNTIME_ARN"],
       runtimeSessionId=uuid.uuid4().hex + uuid.uuid4().hex,
       payload=json.dumps(payload).encode(),
   )
   print(out["response"].read().decode())
   PY
   ```

3. Pass criteria, both required:
   - The composed body states `2 días consecutivos` and the day-1 date
     (`20/10/2026`). An empty read produces neither.
   - The runtime's CloudWatch log group has **no** `recent-alerts read failed:
     AccessDeniedException` line for that invocation. The tool logs the AWS
     error code only, never the message.

4. Policy-level cross-check, no model involved. The simulator evaluates the
   attached policies against explicit context, so it shows the boundary:

   ```bash
   # Expect: allowed
   AWS_PROFILE=ferrenafe aws iam simulate-principal-policy \
     --policy-source-arn "arn:aws:iam::<account>:role/$ROLE_NAME" \
     --action-names dynamodb:Query \
     --resource-arns "arn:aws:dynamodb:us-east-2:<account>:table/ferrenafe-alerts-sent" \
     --context-entries \
       ContextKeyName=dynamodb:LeadingKeys,ContextKeyType=stringList,ContextKeyValues=ALERT#demo-ferrenafe \
       ContextKeyName=dynamodb:Attributes,ContextKeyType=stringList,ContextKeyValues=pk,sk,sent_at,level,reasons \
       ContextKeyName=dynamodb:Select,ContextKeyType=string,ContextKeyValues=SPECIFIC_ATTRIBUTES \
     --query "EvaluationResults[0].EvalDecision"
   ```

   Repeat with `ContextKeyValues=ALERT#other` for `LeadingKeys`: expect
   `implicitDeny`. Repeat with `dynamodb:Attributes` including `title`:
   expect `implicitDeny`. The simulator is advisory; step 3.3 is the real
   evidence.

## Step 4 - After EVERY `agentcore deploy`

The CLI owns the role and a deploy can re-create or reset it, which detaches a
manually attached policy. A runtime replacement (a `[-]`/`[+]` in
`agentcore deploy --diff`, see `docs/runbooks/agentcore-deploy.md` step 1) also
changes the runtime id and the role. After every `agentcore deploy`:

1. `aws iam list-attached-role-policies --role-name "$ROLE_NAME"` still lists
   `AlertHistoryReader` (re-resolve `ROLE_NAME` first; it may have changed).
2. Re-run step 3 and require the non-empty read again.

## Rollback

1. Set the SSM composer flag to `template` (effective immediately).
2. `aws iam detach-role-policy --role-name "$ROLE_NAME" --policy-arn "arn:aws:iam::<account>:policy/$POLICY_NAME"`.
3. `uv run python scripts/seed_demo_history.py --reset` (deletes only the demo partition).
4. Redeploy the previous runtime if it is being reverted; then run step 4's check 1 to confirm the state you expect.
