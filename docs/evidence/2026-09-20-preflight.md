# Pre-flight before the first AgentCore deploy

- **Date**: 2026-09-20
- **Region**: `us-east-1`
- **Identity**: `arn:aws:iam::<account>:user/akemid`
- **Outcome**: cleared to deploy, after one blocking correction to `agent/app.py`

Follows the pre-flight sequence in the `aws-agents:agents-deploy` skill.

## 1. Toolchain

```
$ agentcore --version
0.27.0                          # skill requires >= 0.9.0

$ aws --version
aws-cli/2.34.35 Python/3.14.4 Darwin/25.6.0 exe/arm64

$ docker --version
Docker version 27.3.1, build ce12230

$ aws configure get region
us-east-1

$ aws sts get-caller-identity
{
    "UserId": "AIDA...",
    "Account": "<account>",
    "Arn": "arn:aws:iam::<account>:user/akemid"
}
```

## 2. Is AgentCore available in the target region?

Checked through the AWS Knowledge MCP server (`get_regional_availability`)
rather than by assumption:

```
resource_type: product
regions:       us-east-1, us-west-2
filter:        "Amazon Bedrock AgentCore"

→ {"Amazon Bedrock AgentCore": {
     "us-east-1":  {"status": "isAvailableIn"},
     "us-west-2":  {"status": "isAvailableIn"}}}
```

## 3. Is the model available, and under what identifier?

```
$ aws bedrock list-foundation-models --region us-east-1 \
    --query 'modelSummaries[?contains(modelId, `haiku`)].modelId' --output text
anthropic.claude-haiku-4-5-20251001-v1:0

$ aws bedrock list-inference-profiles --region us-east-1 \
    --query 'inferenceProfileSummaries[?contains(inferenceProfileId, `haiku-4-5`)].[inferenceProfileId,status]' --output text
global.anthropic.claude-haiku-4-5-20251001-v1:0   ACTIVE
us.anthropic.claude-haiku-4-5-20251001-v1:0       ACTIVE
```

### The blocking finding

```
$ aws bedrock get-foundation-model --region us-east-1 \
    --model-identifier anthropic.claude-haiku-4-5-20251001-v1:0 --output json

  modelId                : anthropic.claude-haiku-4-5-20251001-v1:0
  inferenceTypesSupported: ['INFERENCE_PROFILE']
  streaming              : True
  lifecycle              : ACTIVE
```

`inferenceTypesSupported` is `['INFERENCE_PROFILE']` — **not** `ON_DEMAND`.
The bare foundation-model identifier cannot be invoked at all; an inference
profile is mandatory, in every region, not conditionally.

`agent/app.py` carried `MODEL_ID = "anthropic.claude-haiku-4-5"` with a comment
asserting the identifier "carries **no** date suffix", and a test pinning that
shape. Both were wrong, and a unit test cannot see it: nothing offline knows
what Bedrock accepts.

The same comment did record that a profile prefix "may be needed depending on
the account's region … confirmed at deploy against the live account rather
than guessed here." Deferring it was right. The answer is that it is not
conditional.

### Which profile

```
us.anthropic.claude-haiku-4-5-20251001-v1:0
    status=ACTIVE  type=SYSTEM_DEFINED
    routes to: ['us-east-1', 'us-east-2', 'us-west-2']

global.anthropic.claude-haiku-4-5-20251001-v1:0
    status=ACTIVE  type=SYSTEM_DEFINED
    routes to: ['', 'us-east-1']
```

`us.` was chosen. It names every region it can route to, which is a property a
civic system should be able to state; `global.` exposes an unnamed destination.
Three US regions also give the availability the profile exists for.

Resulting identifier: **`us.anthropic.claude-haiku-4-5-20251001-v1:0`**

## 4. Environment note, recorded because it cost time

This shell runs a token-compressing command proxy that rewrites `aws`
invocations and returns the *response schema* instead of the values:

```
$ aws bedrock get-foundation-model ... 
{
  modelDetails:
  {
    inferenceTypesSupported:
    [
      string          ← the schema, not the data
    ]
```

It survives redirection to a file and piping, so a script reading the output
parses the schema and fails or, worse, silently reads nothing. `--output json`
on a bare stdout call works; `rtk proxy <cmd>` bypasses the filter entirely and
is what the checks above used.

Anything automating AWS reads in this environment has to account for it.

## 5. Not verified at this stage

- The runtime has never been deployed, so no `agentRuntimeArn` exists.
- No model invocation has been made. Cold-start latency and real cost remain
  unmeasured (`tasks.md` 3.5 and 3.16).
- IAM permissions for `iam:CreateRole` scoped to `*BedrockAgentCore*` were not
  simulated; the deploy will establish them.
