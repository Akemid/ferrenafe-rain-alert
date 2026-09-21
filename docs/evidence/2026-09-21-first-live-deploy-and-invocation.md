# First deploy to AgentCore Runtime, and the first live invocation

- **Date**: 2026-09-21
- **Region**: `us-east-2` (moved from `us-east-1`; see below)
- **Runtime**: `ferrenaferainalert_rainalert-upwZ4M5wl5`
- **Outcome**: the agent runs live and composes a correct message. That message
  is then **rejected by our own validator**, for a reason that is a defect in
  the validator. Recorded here; the fix is separate work.

Closes `tasks.md` 3.16 (one real end-to-end invocation) and 3.5 (cold-start
measurement). The proposal's success criterion "The agent runs live at least
once" is met.

## CDK bootstrap

```
$ npx aws-cdk bootstrap aws://<account>/us-east-1
✅  Environment aws://<account>/us-east-1 bootstrapped.
```

Eleven resources, `CREATE_COMPLETE`, read back from CloudFormation rather than
from the CLI's own output:

```
5x AWS::IAM::Role        CloudFormationExecution, DeploymentAction,
                         FilePublishing, ImagePublishing, Lookup
2x AWS::IAM::Policy
1x AWS::S3::Bucket       StagingBucket   ($0.023/GB-month)
1x AWS::S3::BucketPolicy
1x AWS::ECR::Repository  ContainerAssets ($0.10/GB-month)
1x AWS::SSM::Parameter
   kms_key_created: false
```

**No KMS key.** The template defines one under `Condition: CreateNewKey`, and
reading only the template suggested the default would create it at $1/month.
The CDK documentation says otherwise — *"By default, the Amazon S3 bucket in
the bootstrap stack is configured to use AWS managed keys"* — and the deployed
stack settles it. Reading a template's parameter default is not the same as
knowing what the CLI passes.

`us-east-2` was bootstrapped identically when the region moved.

## Deploy

```
$ AWS_PROFILE=ferrenafe agentcore deploy -y
✓ Load deployment target · Validate project · Sync CDK dependencies
✓ Build CDK project · Synthesize CloudFormation · Check bootstrap status
✅ AgentCore-ferrenaferainalert-default

  RuntimeArn : arn:aws:bedrock-agentcore:us-east-2:<account>:runtime/ferrenaferainalert_rainalert-upwZ4M5wl5
  RuntimeId  : ferrenaferainalert_rainalert-upwZ4M5wl5
  RoleArn    : arn:aws:iam::<account>:role/AgentCore-ferrenaferainal-...
```

Four resources: the Runtime, its execution role and policy, and CDK metadata.

Read back from `GetAgentRuntime`, not from the deploy log:

```
status              : READY
runtime             : PYTHON_3_14
entryPoint          : ["opentelemetry-instrument", "app.py"]
networkMode         : PUBLIC
idleSessionTimeout  : 900 s        maxLifetime : 28800 s
artifact            : s3://cdk-hnb659fds-assets-<account>-us-east-2/...zip
```

`opentelemetry-instrument` is in the entry point and we did not put it there.
The CLI prepended it **because `aws-opentelemetry-distro` is in the
dependencies** — the package that was discussed as optional turns out to be
what switches instrumentation on. Without it the entry point would have been
`app.py` alone and there would be no traces.

`idleSessionTimeout` 900 s against a six-hourly cycle confirms what design
decision D19 assumed: **every production invocation is a cold start.** That is
now measured rather than predicted.

## Three defects, found in sequence, each hidden by the previous one

### 1. `us-east-1` had an exhausted daily token quota

```
ModelThrottledException: (ThrottlingException) when calling ConverseStream
(reached max retries: 4): Too many tokens per day, please wait before trying again.
```

An account condition, not a code defect. Moved to `us-east-2` after verifying
AgentCore is available there, the model is ACTIVE, and the `us.` inference
profile carries an identical id.

Also visible in that stack trace: **`reached max retries: 4`**. Strands retries
*inside the container*, where our invoker's `total_max_attempts: 1` has no
reach. There is a retry layer we do not control, and it multiplies elapsed time
against our deadline.

### 2. `temperature` and `top_p` cannot both be set

```
ValidationException: The model returned the following errors:
`temperature` and `top_p` cannot both be specified for this model.
```

Ours. `BedrockModel` accepts the pair at construction and Bedrock refuses it at
`ConverseStream`, so the first symptom is a 500 from a deployed runtime.

The quota problem had been hiding this: the `us-east-1` attempt died at
throttling before ever reaching parameter validation. Changing region did not
fix a problem, it **revealed** one.

### 3. The root project could not read `aws login` credentials

```
MissingDependencyException: Using the login credential provider requires an
additional dependency ... pip install "botocore[crt]"
```

`aws login` is this repository's own documented authentication. Without the
`[crt]` extra an operator following the instructions cannot reach the runtime.

## The invocation

Payload built with the project's own `build_prompt` against the richest `V0`
fixture — `prepare`, two reasons, an official warning and a forecast — rather
than a hello-world:

```
session id : 40 chars          (the API minimum is 33; uuid4().hex is 32)
statusCode : 200
contentType: application/json
COLD START : 9.71 s
bytes      : 776
```

```json
{
  "title": "Ferreñafe: Alerta de lluvia - Preparación",
  "body": "Ferreñafe está en alerta por precipitaciones de moderada a fuerte
           intensidad. Se esperan 18.0 mm en 24 horas y 25.5 mm en 48 horas,
           con probabilidad del 75% de lluvia intensa el 3 de septiembre a las
           16:00. …",
  "level": "prepare",
  "valid_until": "2026-09-05T07:00:00-05:00"
}
```

## Two findings from that one call

### Cold start is 9.71 s against a 10 s read timeout

290 ms of headroom, on a path where **every** invocation is cold. At this
deadline the agent would time out most cycles, fall back to the template, and
the only trace would be an operator notice every six hours. The deadline needs
revisiting with measured numbers; one sample is not a distribution.

### The validator rejects a correct message

```
UNKNOWN_NUMBER: '3' is stated in '%' and traces to no such value on the request
```

Every number in the draft is right:

| In the message | On the request |
|---|---|
| 18.0 mm / 24 h | `mm_24h=18.0` |
| 25.5 mm / 48 h | `mm_48h=25.5` |
| 75% | `peak_probability_pct=75` |
| 29.8 mm, 70% | `ForecastThresholdReason` |
| "el 3 de septiembre a las 16:00" | `peak_at=2026-09-03T21:00Z` = 16:00 America/Lima |

The agent even converted to local time correctly. The `3` is a day of the
month; the clause-scoped scan sees it near `%` and demands a matching
percentage on the request.

This is the failure mode that matters most, and the only one of the three that
is **silent**. The other two shouted — a 500, a validation error. This one
rejects, the composer falls back, the community still gets its alert, and
nothing anywhere says the agent has been working perfectly and having its
output discarded every time.

947 tests and eleven contained prompt-injection attacks did not surface it,
because no test had ever used a real model's output.

The fix is not to loosen the rule. The rule says every number must trace to a
request value, and `3` **does** trace — to `peak_at`. What is missing is that
the scan compares only against numeric fields, never against the components of
the instants the request already carries.
