# Design: Scheduled Cycle (EventBridge → Lambda → SSM + DynamoDB → the deployed agent)

Source of truth: `docs/superpowers/specs/2026-09-03-rain-alert-core-design.md` sections 3.5, 3.6, 3.7, 4, 9 and 10.3. Scope and closed decisions: `proposal.md`. Decisions are numbered continuing the single series: change 1 left D1–D12, change 2 left D13–D24, so this document starts at **D25**.

Design spec 3.2 is preserved unchanged and nothing below touches it: **the model never decides whether to alert.** `RunAlertCycle` is not modified by this change at all. The composer is still invoked inside the authorized-send branch, its return value still appears in no predicate, and the scheduled path reaches it through the same `select_composer` the CLI already uses (D14). This change adds a second *entry point* and two second *adapters*; it adds no branch to the use case.

This design exceeds the 800-word SDD budget deliberately. The change spans two languages, two deployment units and a live stack, and seven of its decisions were handed here explicitly with evidence to weigh. Compressing them would move the cost to apply time.

---

## 1. Technical Approach

One sentence: *the cycle gains a second entry point and two second adapters, and everything new is either a pure mapping tested with no I/O, a client injected at the seam, or a relation asserted on a synthesized CloudFormation template — so nothing in the scheduled path is verified only by deploying it.*

Four structural commitments carry the rest.

| # | Commitment | Consequence |
|---|---|---|
| 1 | `RunAlertCycle`, `CycleDependencies` and every port are untouched | The scheduled path and the CLI path run the same use case over the same seven ports; only the leaves differ (D6) |
| 2 | The shared sequence is extracted, not reimplemented | D29 — `run_once` is the calibrated behaviour; neither entry point owns a second copy |
| 3 | Every new adapter injects its client and builds it lazily | D28 — the default suite stays offline with no credentials, exactly as `BedrockAgentCoreInvoker(client=...)` already does |
| 4 | Nothing that crosses the Python/TypeScript boundary is copied; one golden JSON file carries it | D31, D32 — the precedent is `contracts/public-snapshot.json`, asserted from `web/test/snapshot.test.ts` and from the Python suite |

---

## 2. Architecture Decisions

### D25 — A second stack, and the `SnapshotWriter` ARN crosses as a required prop, never as an export

**Choice**: a new `ScheduledCycleStack` in `infra/lib/scheduled-cycle-stack.ts`, deployed as `ferrenafe-scheduled-cycle` from the same app and the same region (`us-east-2`). `PublicSnapshotStack` is not modified. The stack takes two **required** props, supplied in `bin/infra.ts` from CDK context (**amended, Phase 3 fix round 1: two more required props were added during implementation, `agentRuntimeArn` and `alarmEmail` — see the AgentCore-grant addendum below and D33's amendment; the illustrative snippet below is left as originally written, showing only the two this decision itself introduced**):

```ts
export interface ScheduledCycleStackProps extends StackProps {
  readonly snapshotBucketName: string;       // committed to cdk.json's context block — no account id in it
  readonly snapshotWriterPolicyArn: string;  // supplied at deploy: -c snapshotWriterPolicyArn=…
}
```

and consumes the policy with `iam.ManagedPolicy.fromManagedPolicyArn(this, 'SnapshotWriter', props.snapshotWriterPolicyArn)`, attached to the Lambda role. The role defines **no `s3:PutObject` statement of its own** (proposal success criterion 6).

**Alternatives considered**:

| Option | Why rejected |
|---|---|
| Add the resources to `PublicSnapshotStack` | Rollback level 4 is `cdk destroy` of the new stack with the bucket surviving. One stack makes that impossible. It also puts compute in a stack whose bucket carries `RemovalPolicy.DESTROY` |
| `Fn.importValue('…SnapshotWriterPolicyArn')`, or a direct TS object reference across stacks (which CDK compiles into the same export/import) | The deadly embrace the proposal names, and the CDK skill's first critical warning: once the cycle stack consumes the export, `PublicSnapshotStack` cannot remove or rename it without a three-deploy dance. On the live page stack, days before judging |
| Give the policy a stable `managedPolicyName` and resolve it with `fromManagedPolicyName` | Structurally the nicest — no flag to forget, no id anywhere. But `ManagedPolicyName` is part of the ARN, so adding it to a policy that today has a generated name **replaces the resource**. The replacement is harmless only because nothing is attached yet, and it breaks whatever hand-attachment currently backs rollback level 3 until an operator re-attaches it. Recorded as the follow-up once judging is over; not worth mutating the live stack this week |
| SSM late-binding (`PublicSnapshotStack` writes the ARN to a parameter, the cycle stack reads it with `valueForStringParameter`) | Also sound, and the CDK skill's own suggestion for dependency cycles. Rejected for the same reason: it adds a resource to the deployed stack. Note for whoever revisits it — it must be `valueForStringParameter` (a deploy-time CFN dynamic reference), **never** `valueFromLookup`, which performs a synth-time AWS call and would make `npm test` in `infra/` require credentials |

**Rationale**: the proposal already stated the preference ("prefer passing the ARN in as a prop/context value over `Fn::ImportValue`"). This is that preference made concrete, and it is the only option that changes nothing already deployed.

**The cost, stated**: the ARN contains the twelve-digit account id, so it MUST NOT be committed. `cdk.json`'s `context` block carries `snapshotBucketName` only (**amended, Phase 3 fix round 1**: originally `cdk.context.json`; that file is CDK's own auto-written cache — the CLI writes to it whenever a context provider runs, and the keys it writes, e.g. `availability-zones:account=<12 digits>`, embed the account id — so it is a tripwire, not a guardrail, for exactly the trap the next paragraph names. `cdk.json`'s `context` block is never auto-written by the CDK CLI and is now gitignored for `cdk.context.json` instead). The deploy command carries the ARN; `docs/runbooks/` writes it as `<account>`. If the flag is forgotten, synth fails — the prop is required and `bin/infra.ts` throws on a missing context key rather than defaulting. **Amended further**: `snapshotBucketName` itself now has the same throw-not-default treatment when it is present but still the committed placeholder literal — see the "cost, stated" text above did not originally cover the case where the value is present and simply wrong, which fix round 1 found: a placeholder bucket name reaches `RAIN_ALERT_SNAPSHOT_BUCKET` truthily, so the Lambda deploys and every publish fails `AccessDenied` while the alert itself still goes out and reports success.

**A trap that will otherwise be found by a red hygiene test after a commit**: `tests/hygiene/test_repo_hygiene.py:202` scans the working tree **and all git history** for `\b[0-9]{12}\b`. Every ARN fixture in `infra/test/` must therefore use a non-numeric account placeholder — `arn:aws:iam::ACCOUNT:policy/SnapshotWriter` — not a plausible-looking twelve-digit one. Discovering this after the commit costs a history rewrite.

**Residual, accepted**: the two stacks are coupled by convention, not by CloudFormation. Destroying `PublicSnapshotStack` leaves the cycle role pointing at a policy that no longer exists. That is strictly better than the embrace, and the runbook records the destroy order.

#### Addendum (Phase 3 fix round 1) — the AgentCore grant needs two resources, not one

Neither this decision nor task 3.17 originally anticipated a second required context prop for the AgentCore grant, and both said "no wildcard" without specifying its resource form — the first implementation therefore granted `bedrock-agentcore:InvokeAgentRuntime` scoped to exactly `props.agentRuntimeArn` and nothing else. That is too narrow, and the failure is silent: two independent sources confirm `InvokeAgentRuntime` is evaluated against **both** the agent runtime resource and the agent endpoint resource being invoked. AWS's `bedrock-agentcore` resource-based-policies documentation, verbatim: *"AWS evaluates both identity-based and resource-based policies for both the agent runtime and the agent endpoint being invoked… must allow the action on both the agent runtime and agent endpoint resources."* The installed `aws-cdk-lib`'s own `aws-bedrockagentcore` construct helper (`aws-bedrockagentcore/lib/runtime/runtime-base.js`) grants exactly `resourceArns: [this.runtimeRef.agentRuntimeArn, \`${this.runtimeRef.agentRuntimeArn}/*\`]` for this reason. `agentcore_invoker.py` passes no `qualifier`, so the DEFAULT endpoint is the target, and a grant naming only the runtime ARN denies it.

Why this is the worst kind of defect here: `AccessDeniedException` → `AgentInvocationError` → caught at `agent_composer.py`'s broad `except` → the deterministic template fallback. The alert still goes out, the cycle reports success, the `Errors` metric stays at zero, and Alarm 1 never fires — the agent, deployed and working, would be permanently off in production with nothing to say so.

**Amended**: the grant is `resources: [props.agentRuntimeArn, \`${props.agentRuntimeArn}/*\`]`. Still resource-scoped — the `/*` spans only that one runtime's own endpoints, never a wildcard across runtimes. `task 3.17` is corrected in `tasks.md` to match.

### D26 — The DynamoDB item is `serialization.py`'s document, unchanged; the sort key is `TimeWindow.key()` plus the level

**Choice**: one table, `ferrenafe-alerts-sent`, on-demand billing, `RemovalPolicy.RETAIN`, explicit `tableName`, no GSI, no stream. **Amended, Phase 3 fix round 1**: also `pointInTimeRecoverySpecification` (PITR) and `deletionProtection`. `RemovalPolicy.RETAIN` protects the table from *stack* deletion only — it has no bearing on *data* deletion through a live IAM grant, and the review that found the grant was scoped to twelve DynamoDB actions instead of the four the adapter calls (see the IAM grant note under "File Changes"/apply-progress) made the point directly: without PITR there is nothing to restore from if that data is ever deleted in bulk. `deletionProtection` is a second, independent guard against an accidental `cdk destroy`/console delete of this one resource.

| Item | `pk` (S) | `sk` (S) | Attributes |
|---|---|---|---|
| Sent alert | `ALERT#<city_slug>` | `<window_start ISO>#<level>` | exactly what `alert_record_to_dict` emits, plus `expires_at` (N) |
| Active outage | `OUTAGE#<city_slug>` | `CURRENT` | exactly what `outage_record_to_dict` emits, **no `expires_at`** |

This is not a new decision. `adapters/serialization.py`'s module docstring already pins that table, verbatim, as a carried-forward contract, and the adapter is therefore a mechanical mapping with no translation layer.

**Does `TimeWindow.key()` still hold as the sort key?** Partly, and the difference matters. `docs/architecture/entrypoints-and-testing.md:64` says `key()` "becomes change 3's DynamoDB sort key". `key()` returns `self.start.isoformat()` and nothing else — and a single window can legitimately carry **two** records, because `should_send` authorises an escalation: `prepare` for window W, then `imminent` for the same W. With `key()` alone as the sort key the escalation would silently overwrite the `prepare` record, and the audit trail would lose the fact that the community was warned twice. So `key()` is the sort key's **prefix**, and the level is the discriminator, exactly as `serialization.py` already wrote it. The docs line is narrowed, not contradicted.

**The range query.** `alerts_with_window_start_between(city_slug, earliest_start, latest_start)` becomes one `Query`:

```
KeyConditionExpression: pk = :pk AND sk BETWEEN :lo AND :hi
:pk = f"ALERT#{city_slug}"
:lo = earliest_start.isoformat()
:hi = f"{latest_start.isoformat()}#￿"
ConsistentRead: True
ScanIndexForward: True          # the port documents ascending by window start
```

The upper sentinel is required for inclusivity: `latest_start.isoformat()` alone excludes `…#imminent`, which sorts after it. `￿` is chosen over an ASCII `~` so the bound holds regardless of what a future `Level` value spells; a unit test asserts every `Level` member sorts below the sentinel, so adding one that does not goes red.

`default_now()`'s truncation to whole seconds (`cli.py:141`) is load-bearing here for a second reason the docstring does not give: an aware UTC `isoformat()` with no microseconds is always 25 characters, so the prefix is fixed-width and lexical order equals chronological order across the `#`. A microsecond value would produce a 32-character prefix and break that.

**`ConsistentRead: True`**, per the proposal. Eventual consistency here means a cycle can miss the alert the previous cycle wrote and re-alert the community; it saves half an RCU 120 times a month.

**`record_alert` is a plain `PutItem` with no condition expression.** A `ConditionExpression: attribute_not_exists(pk)` would raise on a duplicate — but `record_alert` runs *after* `notifier.send_alert`, so raising means the alert went out and was not recorded, and the next cycle re-alerts. Last-write-wins is correct here because the key **is** the identity of the send.

**Alternatives considered**: a bare `city_slug` partition key with no `ALERT#`/`OUTAGE#` prefix (then the outage item needs a sort key that cannot collide with an ISO timestamp — a reserved literal inside the same key space, which is the prefix decision with less of it visible); two tables (two grants, two names, two things to destroy, for two access patterns that never join); a `sent_at` sort key (the dedup query is on *window start*, so `sent_at` would need a GSI to answer the one question the port asks).

### D27 — TTL on alert items only, derived from configuration, and never a correctness mechanism

**Choice**: `timeToLiveAttribute: 'expires_at'`, an epoch-seconds Number written by the adapter as `int(record.sent_at.timestamp()) + ALERT_RETENTION_DAYS * 86400`, with `ALERT_RETENTION_DAYS = 365`.

`adapters/local/json_alert_repository.py:111-119` records the reason this is here: the JSON `alerts` list grows without bound and rewrites the whole document on every send, and that comment states that "change 3's DynamoDB adapter should use a TTL attribute rather than a rewrite". This discharges it.

Three things the number has to clear, and they are asserted as a relation rather than pinned:

1. `dedup_lookback_hours` (72) — an alert expiring inside the dedup window re-alerts the community.
2. `MAX_RECENT_ALERTS` over the same lookback, which is what `cli.py::_recent_alerts` reads for the public page's "alertas enviadas" section.
3. The audit trail. 365 days is one rainy season plus the review of it, at roughly 4 KB per item and a handful of items a year — far inside the free tier.

The test is `expires_at - sent_at > dedup_lookback_hours * 3600` computed from the `AlertConfig` under test, not from a literal, so lowering the constant below the configured window goes red.

**The outage item carries no `expires_at`, and that is the mechanism.** DynamoDB TTL only deletes items that *have* the attribute, so omitting it is how the active-outage record is made permanent. Giving it a TTL would silently clear "we already notified the operator about this outage" mid-outage, and the next cycle would re-notify — the exact silent-inertness failure the proposal's "Why `alerts-sent` is not deferred" section is about.

**TTL is not a correctness mechanism and must not be read as one.** Deletion is asynchronous and best-effort; an expired item can still be returned by `Query` for some time after its timestamp passes. Dedup correctness comes from the key-range condition, which is evaluated on every read regardless of TTL. *To verify at apply:* the current documented upper bound on TTL deletion latency (the figure commonly cited is "typically within a few days"); this design deliberately does not assert a number from memory, and nothing here depends on one.

### D28 — Pure mappers with no doubles, `moto` for the query contract, and forced dummy credentials

Both new adapters follow `BedrockAgentCoreInvoker` exactly: `client: Any | None = None`, built lazily on the first call, so wiring never constructs a client and `uv run pytest` needs no region and no credentials.

The testing is split by what can actually fail:

| What | How | Why not the other way |
|---|---|---|
| Item ↔ `AlertRecord` mapping, key construction, sentinel ordering, TTL relation, SSM document parsing and its failure posture | Pure functions in the adapter module, table-tested with `@pytest.mark.parametrize`. No fakes, no client, no I/O — the `serialization.py` pattern | This is where most of the risk is and it needs nothing |
| `Query` key condition, `ConsistentRead`, pagination, `GetParametersByPath` paging | `moto`, as a new **dev-group** dependency | A hand-written `FakeDynamoDbClient` would mean re-implementing a key-condition evaluator in the test, and the test would then pass against a `KeyConditionExpression` real DynamoDB rejects. That is precisely the "a check that looked like it ran and did not" failure `CLAUDE.md` names |

**`moto` is not `unittest.mock`.** The standing rule in `tests/support/fakes.py` forbids mocks; `moto` is a service double in the same category as `FileHtmlFetcher` and `OfflineOpenMeteoProvider`, which this project already ships.

**Required, not optional**: a `conftest.py` fixture that forces `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN` and `AWS_DEFAULT_REGION` to dummy values for the whole session. Without it, a developer with a live `ferrenafe` session and a mis-scoped `mock_aws` decorator runs the suite against real AWS. The suite's offline guarantee is a property of that fixture, not of good intentions, and it is asserted by a test that reads the resolved credentials inside the fixture's scope.

**Pagination is not optional in the DynamoDB adapter.** `Query` paginates, and a first page that happens to hold everything is what makes a missing `LastEvaluatedKey` loop invisible until the data grows. It is driven by the `boto3` paginator, and the `moto` test seeds more items than one page holds. (`GetParametersByPath` paged too, in this decision's original text — see the amendment below: the SSM adapter no longer uses it at all.)

#### Amendment (fix round 1) — `AWS_DEFAULT_REGION` is not forced, and a socket guard replaces it

This decision originally mandated forcing `AWS_DEFAULT_REGION` to a dummy value alongside the three credential variables, verbatim, as part of the required `conftest.py` fixture. A fresh-context security review found the actual effect of doing so: a test with a forgotten `mock_aws()` decorator no longer failed fast with `NoRegionError` (a local, no-network error) — it now had enough configuration to construct a real client and reach `dynamodb.us-east-2.amazonaws.com` over genuine HTTPS, receiving `UnrecognizedClientException` back. DNS resolved, TLS completed, AWS answered. The dummy region bought the trade; nothing needed it, because both `moto` fixtures in this suite pass `region_name` explicitly.

**Amended**: `AWS_DEFAULT_REGION` is dropped from the fixture. In its place, a second session-scoped `autouse` fixture in `tests/conftest.py` monkeypatches `socket.socket.connect` to raise on any non-loopback address. This is the mechanism that actually makes "no network" true, rather than merely changing which error a real request returns — it fails locally, immediately, and names the destination that was blocked, attributed to whichever test caused it. **Corrected in fix round 2**: the guard intercepts the TCP `connect` call specifically, not "any network activity" — `socket.getaddrinfo` (DNS resolution) is not covered and runs to completion first, which a fresh-context probe demonstrated by finding a resolved public IP in the guard's own blocked-address message. The IP literal `192.0.2.1` used in the tests below means the *tests themselves* need no real lookup to prove the connect-time block, not that the guard stops DNS. `tests/unit/test_conftest_fixtures.py` asserts both halves: a non-loopback TCP connect attempt is blocked before it reaches the real socket layer, and a loopback attempt still reaches it (proven by a genuine `ConnectionRefusedError`, not the guard's own exception).

### D29 — `run_once` lives in `entrypoints/`, and the Lambda handler is forbidden to import the CLI

**Choice**: a new `src/rain_alert/entrypoints/run_once.py`:

```python
def run_once(
    deps: CycleDependencies,
    publisher: SnapshotPublisher | None,
    *,
    report: TextIO,
) -> tuple[CycleResult, AlertConfig]: ...
```

It performs the exact sequence `cli.py:396-409` performs today: `config.load()`, `RunAlertCycle(deps).execute()`, and the forgiving snapshot guard including `SNAPSHOT_FAILURE_PREFIX` written to `report`. It does no argument parsing, no rendering and no exit code. `cli.main` and `lambda_handler.handler` both call it.

**Why `entrypoints/` and not `application/`** — the proposal left the choice open. `ports/__init__.py`'s own docstring settles it: `SnapshotPublisher` "is the entry point's: `CycleResult` already carries everything the public page needs and is already returned, so the use case does not need to know a web page exists." Putting `run_once` in `application/` would drag the publisher into the application layer and make that docstring false.

**It returns the config as well as the result.** `cli.main` needs it for `render`/`as_json`, and returning it removes a third `config.load()` on a path where that call is now an SSM round trip.

**The handler:**

```python
def handler(event: dict[str, Any], context: Any) -> dict[str, Any]: ...
```

- **`event` is read for nothing.** A schedule payload carries no fact the cycle needs, and treating it as a configuration channel would give anything that can invoke the function a say in what the cycle does. The cycle's inputs are SSM, the two sources, and the table. Asserted by a test that passes a hostile event and shows the cycle behaves identically.
- **It catches nothing.** An exception propagates out of `handler`, Lambda records it on the `Errors` metric, and D33's alarm fires. Swallowing would make the Errors metric permanently zero and the alarm decorative. This is the load-bearing coupling between the handler contract and observability.
- It prints `as_json(result, config)` as one line to stdout, which is the CloudWatch Logs record for the cycle, and returns a small summary dict for a manual `aws lambda invoke`.
- No exit code. `EXIT_OK` / `EXIT_CANNOT_START` stay the CLI's.

**A new architecture test**, extending `tests/architecture/test_layer_boundaries.py`: `entrypoints/lambda_handler.py` imports neither `entrypoints.cli` nor `adapters.local.json_alert_repository` nor `adapters.local.static_config_repository`. This encodes the proposal's own argument as a check rather than a paragraph — a cloud path that reached `build_local_deps` would inherit `JsonFileAlertRepository` on `/tmp`, dedup would be silently inert in production, and nothing would fail, log, or look wrong.

`default_now` moves to `run_once.py`; `cli.py` imports and re-exports it so `cli.default_now`, its `__all__` entry and every docstring citation of it still resolve (`tests/hygiene/test_docstring_citations.py` checks those).

`build_cloud_deps` joins `build_local_deps` in `wiring.py` as the second leaf set over the same `CycleDependencies` type (D6). It calls the **same** `select_composer`, unchanged — that is what puts the agent in the loop without a second switch. It can construct only `ConsoleNotifier`, and the existing `test_the_wiring_can_only_construct_the_console_notifier` gains its cloud twin.

**`SsmConfigRepository` memoizes.** `select_composer` reads the config once in wiring and `RunAlertCycle.execute` reads it again (D14 flagged this and said "the natural fix there is a memoizing `ConfigRepository`"). Memoization is here for a correctness reason as well as a cost one: one cycle must run on one configuration, and an operator editing SSM mid-cycle must not have half of it applied.

### D30 — The Lambda artifact is pure Python, built by `uv` with no Docker, and drops `botocore[crt]`

**Choice**: a build script produces `infra/build/lambda/` and the stack uses `lambda.Code.fromAsset('build/lambda')` with `Runtime.PYTHON_3_12` and `Architecture.ARM_64`.

```
uv export --frozen --no-dev --no-emit-project   → filter out the excluded distributions
uv pip install -r <filtered> --target infra/build/lambda \
    --python-version 3.12 --python-platform aarch64-manylinux2014
cp -R src/rain_alert infra/build/lambda/
```

**Does `botocore[crt]` matter inside Lambda? No, and excluding it is what makes everything else work.** `pyproject.toml:10-26` already answers the first half: the `[crt]` extra exists because botocore's **login** credential provider refuses to load without it, `aws login` is this project's documented authentication, and — quoted from that comment — "the deployed Lambda (change 3) uses an IAM execution role and would not need it." The execution role's credentials arrive through the container credential provider, which has no CRT dependency. The conclusion this design adds is the second-order one: `awscrt` is the **only compiled wheel** in the dependency graph. Without it, every remaining distribution (`boto3`, `botocore`, `httpx`, `httpcore`, `h11`, `anyio`, `sniffio`, `idna`, `certifi`, `beautifulsoup4`, `soupsieve`, `jmespath`, `python-dateutil`, `urllib3`, `s3transfer`) is pure Python, so the artifact is architecture-independent, builds on a developer's macOS machine with no Docker and no cross-compilation, and `ARM_64` costs nothing to choose. *To verify at apply:* that this list is what `uv export --frozen --no-dev` actually resolves to, rather than what this document expects.

The build script FAILS if the tree contains any `*.so` or any `awscrt*` directory. That check is what keeps "architecture is free" true: the day a compiled dependency arrives, the build stops instead of shipping an x86 wheel to an ARM runtime, where it fails at import on the first real cycle and on no test.

**`boto3` is bundled, not taken from the runtime.** The Lambda Python runtimes ship their own boto3, but the version lags and `bedrock-agentcore` is a recent data plane — `adapters/agentcore_invoker.py` records that the operation was resolved against `botocore` 1.43.98. Relying on the runtime's copy means the service model may not know `invoke_agent_runtime`, and the failure is an `UnknownServiceError` at the first agent-composed alert. Bundling costs about 15–20 MB unzipped, well inside the 250 MB limit. *To verify at apply:* the boto3 version the current `python3.12` runtime ships, so the runbook can record what was avoided.

**Cold start.** Every invocation is cold at a six-hourly rate — the proposal and D19 both establish this. The dependency set imports in roughly 1–3 s (boto3 client construction dominates, because it parses the service model JSON); `memorySize: 512` is chosen for import speed rather than for working set, since Lambda scales CPU with memory. It is budgeted inside D31's headroom, not treated as free. *To verify at apply:* the measured `Init Duration` from the first live `REPORT` line, recorded in the change artifacts the way the ten AgentCore cold starts were.

**Alternatives considered**: `aws-lambda-python-alpha`'s `PythonFunction` and `Code.fromAsset(..., { bundling })` — both want Docker at synth time, which would make `npm test` in `infra/` depend on a running daemon; a Lambda layer for the dependencies — two assets and two build steps to solve a size problem this artifact does not have.

### D31 — The timeout is two relations, and only one number crosses the language boundary

The proposal states the invariant: Lambda timeout MUST exceed `RAIN_ALERT_AGENT_TIMEOUT_S` plus the worst case of the rest of the cycle. A Lambda timeout below it does not degrade — it kills the process mid-call, `AgentBackedComposer`'s `except` never runs, and no alert is sent at all.

That is **two** relations, and they cross different boundaries, so they get different mechanisms.

**Relation 1 — the agent deadline must not be tuned below what it was measured for.** This crosses Python → TypeScript. The mechanism is the golden-document pattern this repository already uses twice (`contracts/public-snapshot.json`, asserted from `web/test/snapshot.test.ts:32` and from the Python suite; `contracts/agent-composition.json` for the agent). A third file:

```json
{
  "schema_version": 1,
  "agent_read_timeout_seconds": 35.0,
  "cycle_headroom_seconds": 60,
  "ssm_path_prefix": "/ferrenafe/rain-alert/"
}
```

- `tests/unit/adapters/test_agentcore_invoker.py` asserts `contract["agent_read_timeout_seconds"] == agentcore_invoker.READ_TIMEOUT`. Moving the Python constant without the file goes red in the Python suite.
- `infra/test/scheduled-cycle-stack.test.ts` reads the same file with `readFileSync` and asserts the synthesized `Environment.Variables.RAIN_ALERT_AGENT_TIMEOUT_S` is **≥** `agent_read_timeout_seconds`. Setting the env var to 10 s — which would make every production cycle fall back, a kill switch by accident, D19's "why not 5 s" — goes red in the CDK suite.

**Relation 2 — the function timeout must exceed the agent deadline plus the rest of the cycle.** This crosses nothing: both values are on the same synthesized template. The test reads `Timeout` and `Environment.Variables.RAIN_ALERT_AGENT_TIMEOUT_S` off the same `AWS::Lambda::Function` resource and asserts

```
Timeout  >  Number(env.RAIN_ALERT_AGENT_TIMEOUT_S) + contract.cycle_headroom_seconds
Timeout <= 900
```

**Why not derive the timeout from the contract file in the stack.** Then the assertion would be tautological — the stack and the test would agree by construction and neither would notice that 35 + 60 no longer fits. The stack carries a deliberate, operator-visible `timeout: Duration.seconds(120)`; the test carries the relation. Raising `READ_TIMEOUT` to 200 turns the CDK suite red, and the fix is to raise the function timeout, which is exactly the conversation that should happen.

`cycle_headroom_seconds` is 60: the SENAMHI scrape and the Open-Meteo fetch under `adapters/http.py`'s own timeout, one `GetParametersByPath`, one `Query`, one `PutItem`, one `PutObject`, plus cold-start import. 35 + 60 = 95 < 120, with the difference visible rather than tuned away. *To verify at apply:* Lambda's maximum timeout (900 s is asserted from memory here and the test above depends on it); and the first live `REPORT` line's `Duration`, which is what should move 60.

**Accepted gap, stated rather than implied**: this asserts the template, not the deployed function. An operator editing the timeout or the env var in the console is drift, invisible to both suites. `cdk drift` is the tool; running it is a runbook step, not a test.

### D32 — SSM: one parameter per value object, all `String`, and CDK writes none of them

**Layout**, under one path prefix so the IAM grant is one resource and the read is one `GetParametersByPath`:

| Name | Type | Shape | Who writes it |
|---|---|---|---|
| `/ferrenafe/rain-alert/location` | String | `{"latitude": …, "longitude": …, "source": "operator_supplied"}` | operator |
| `/ferrenafe/rain-alert/thresholds` | String | the seven `RiskThresholds` fields as JSON, the two level sets as arrays | operator |
| `/ferrenafe/rain-alert/checklist` | String | a JSON **array** of strings | operator |
| `/ferrenafe/rain-alert/active-channel` | String | `console` | operator |
| `/ferrenafe/rain-alert/composer` | String | `template` \| `agent` | operator |
| `/ferrenafe/rain-alert/forecast-hours` | String | an integer | operator |
| `/ferrenafe/rain-alert/dedup-lookback-hours` | String | an integer | operator |

`city`, `city_slug`, `region` and `timezone` stay in code, per the proposal. `city_slug` above all: it is the partition-key input, and an operator editing it strands the entire dedup history under the old key and re-alerts a community that was already told.

**Why `location` is one parameter and not three.** `static_config_repository.py` already enforces "both or neither" for `RAIN_ALERT_LAT`/`LON`, and the proposal requires `coordinates_source` to "travel with the coordinates, never be inferred". A single JSON document makes both structurally true instead of validated: half a coordinate cannot exist, and a pair cannot arrive without its provenance. The same argument covers `thresholds`, which carry the cross-field invariant that `prepare_probability_pct_degraded` is the stricter of the two.

**Why nothing is a `StringList`, with the reproduction.** `checklist` is the obvious candidate and it is the one that breaks. SSM `StringList` is comma-delimited with no escaping, and two of the five shipped items contain commas — *"Limpia canaletas, techos y desagües cercanos."* and *"Ten a mano una linterna, un botiquín y los teléfonos de emergencia."* As a `StringList` those five items arrive as eight fragments, and the fragments render into a resident's message body as if they were instructions. Nothing raises. A JSON array in a `String` parameter has an escaping rule, so it does not have this failure. *To verify at apply:* that `StringList` has no escape mechanism (this design asserts it has none from the delimiter's documented behaviour; the check is cheap and the consequence is a mangled safety instruction).

**Why nothing is a `SecureString`.** Nothing here is a secret — thresholds, coordinates and a public-facing checklist. A `SecureString` would add a KMS decrypt grant and a key to manage for no confidentiality gain. Separately, `AWS::SSM::Parameter` supports `String` and `StringList` only, so CloudFormation could not create one anyway (*to verify at apply*, though it does not change the answer).

**CDK creates no parameters, and this is the trap the question is about.** If CDK declared `new ssm.StringParameter(…, { stringValue: DEFAULT })` and an operator later tuned a threshold in the console, the **next `cdk deploy` would silently restore the template's value** — CloudFormation owns the resource and reconciles it. An unrelated deploy (bumping the memory, retargeting the schedule) would therefore throw away a rainy season's calibration, with no error and no diff anybody reads. So:

- CDK grants `ssm:GetParametersByPath` on `arn:…:parameter/ferrenafe/rain-alert/*` and creates nothing.
- The runbook carries the seven `aws ssm put-parameter` commands as a seed step.
- A missing or unparseable parameter **raises**, matching `StaticConfigRepository`'s existing strictness on `RAIN_ALERT_LAT`/`LON` and `RAIN_ALERT_COMPOSER`. A deploy without the seed step therefore produces a Lambda that fails loudly every cycle and publishes nothing — the page freezes at the last good snapshot, which is today's behaviour, and the alarm fires. That is the correct direction of error; a built-in default would forecast for a point nobody chose and say nothing.

**The prefix is the third thing that would drift silently**, so it rides in `contracts/scheduled-cycle.json` alongside the timeout: the Python constant is asserted against the file, and the CDK test asserts the granted resource ARN ends with it. A one-character disagreement between the grant and the reader is `AccessDenied` on every cycle and on no test.

**Alternatives considered**: twelve flat parameters, one per field (an operator edits one number without JSON — at the cost of every cross-field invariant and a half-applied calibration); `AlertConfig` fields for the agent ARN and timeout (D23 already refused: an ARN is an AWS concern and must not widen the frozen core — CDK sets `RAIN_ALERT_AGENT_RUNTIME_ARN` and `RAIN_ALERT_AGENT_TIMEOUT_S` as Lambda environment); Secrets Manager (nothing here is a secret, and `CLAUDE.md`'s secret-handling rules would then apply to a checklist).

#### Amendment (fix round 1) — `GetParameters` by exact name, never `GetParametersByPath`

This decision originally chose `GetParametersByPath` over the shared prefix specifically so the IAM grant could be a single resource (`arn:…:parameter/ferrenafe/rain-alert/*`, quoted above). A security review named the cost that framing left out: a single resource that is a **wildcard over the whole prefix** grants `ssm:GetParametersByPath` on everything under it *forever*, including anything an operator parks there later that this design never anticipated — the grant is written once, in Phase 3, and a later adapter change to read one more value would otherwise need a separate, later policy change to narrow it back down.

**Amended**: there are exactly seven parameters, and `GetParameters` accepts up to ten names per call — no paginator needed. `SsmConfigRepository` now requests the seven exact names by `GetParameters`, and Phase 3's IAM grant will list seven exact ARNs instead of one prefix-wildcard resource. Two consequences, both improvements over the original text:

- **A stray parameter under the same prefix is never requested at all**, which is stronger than the original design's "read everything under the prefix, then filter by an explicit allow-list" (the `city_slug`-typo-tolerance scenario above). There is no filter step left to get wrong, because there is no wildcard read to filter.
- **A genuinely missing parameter is now named directly.** `GetParameters`' response carries an `InvalidParameters` list of exactly the names it could not find, which this adapter raises on explicitly — a clearer failure than `GetParametersByPath`'s silent omission of an unmatched name from its result set, which previously left the missing-parameter case indistinguishable from an "empty value" case until `_require` inferred it generically.

The prefix itself is unaffected by this amendment — it still names the one path all seven parameters live under, still rides in the golden contract file alongside the timeout, and the CDK test now asserts each of the seven granted ARNs starts with it, rather than asserting one granted resource ends with it.

### D33 — Observability: one JSON line, two alarms, one topic, and no subscriber in the repository

This **overrules the proposal's open question 4 default** ("out of scope; the frozen page is the de-facto signal"). The reason is in the proposal's own risk table, at High: operator notices land in `ConsoleNotifier` → CloudWatch Logs → nobody. Between 2026-10-02 and 10-12 nobody is watching a log group, and the de-facto signal — a resident noticing a stale page — arrives after the harm. The cost of closing it is two alarms and a topic.

| Layer | What | What the operator sees |
|---|---|---|
| Log | The `as_json` document, one line per cycle, to stdout. `logRetention: RetentionDays.ONE_MONTH` — the proposal flags never-expire as a cost that grows silently | The full cycle, greppable, with `level`, `sent`, `decision`, `composed_by` and both source statuses |
| Alarm 1 — **it broke** | `Errors` `Sum ≥ 1` over one period, `treatMissingData: NOT_BREACHING` | An email: the cycle raised. Only meaningful because D29's handler catches nothing |
| Alarm 2 — **it stopped** | `Invocations` `Sum < 1` over 8 hours, `treatMissingData: BREACHING` | An email: no cycle ran in a window longer than the schedule. This is the one that catches a disabled schedule, a deleted target, or a schedule nobody re-enabled after a rollback |
| Alarm 3 — **the page froze while the alert worked** (recommended, not minimum) | A metric filter on the log group for `SNAPSHOT_FAILURE_PREFIX`, alarmed at ≥ 1 | An email: the alert went out and the judged artifact did not update |
| Destination | One SNS topic, with the subscription created by the stack itself (**amended, Phase 3 fix round 1** — see below) | — |

**Alarm 2 is the one that earns its place.** `Errors` only catches a crash. The failure this change actually introduces is the cycle quietly not running, and `Invocations` with `treatMissingData: BREACHING` is the cheapest thing that distinguishes "healthy and calm" from "not running" — which is, in a different form, exactly the gap the proposal's question 1 names on the public page.

**Amended, Phase 3 fix round 1 — the subscription IS in the stack now, and the address still never is.** This decision originally left the subscription entirely to the operator, reasoning correctly that an email address is personal data and `openspec/config.yaml → rules.archive` forbids committing it — but drew the wrong conclusion from a correct premise. The conclusion left Alarm 2, the only detector of this design's defining silent failure (the cycle stops running), transitioning to ALARM in a console nobody watches until someone remembers the manual step. The fix keeps the premise and changes the mechanism: `alarmEmail` is a required stack prop, supplied only via `-c alarmEmail=<address>` at deploy time and never committed — exactly the pattern already established for `snapshotWriterPolicyArn`/`agentRuntimeArn` — and the stack creates the `AWS::SNS::Subscription` itself. The address still cannot enter git; it simply also cannot be forgotten. Gotcha for the runbook, unchanged by this amendment: an SNS email subscription stays `PendingConfirmation` until the recipient clicks the link, and an unconfirmed subscription is silent in exactly the way this whole decision exists to prevent. The runbook must still include "send a test notification and confirm it arrived" as a verification step, not just "deploy".

**Alarm 2 fires during a deliberate rollback**, because a disabled schedule *is* a frozen page. That is the alarm working. The runbook says to disable the alarm's action, not the alarm, so the state stays visible.

**What cannot be tested, said plainly** — strict TDD applies to what is assertable, and pretending otherwise is the failure mode this project documents. The CDK suite asserts the alarms exist with the right metric, threshold, period, `treatMissingData` and `AlarmActions`, and that the topic exists. It cannot assert that an email is delivered, that the address is confirmed, or that anyone reads it. *Alarm 3's filter pattern is a second gap:* CloudWatch filter patterns treat a leading `[` as the start of a space-delimited pattern, so the literal `[SNAPSHOT NOT PUBLISHED]` must be written as a quoted term. That the quoted form matches is **not** provable in the CDK suite — the suite only proves the string reached the template. *To verify at apply* with a real log event, and recorded in `docs/evidence/` with the command and its output, per that directory's convention.

### D34 — Rollback: disable first, restore second, and the restore is not the Lambda's to perform

The proposal's five levels stand. Two of them were handed here for a concrete mechanism.

**Disabling the schedule.** Two routes, and the order matters:

1. *Emergency, fastest:* `aws scheduler update-schedule --name ferrenafe-rain-alert --state DISABLED --profile ferrenafe`. Effective in seconds. **It is drift, and the next `cdk deploy` of this stack silently re-enables it** — the same class of trap as D32's SSM defaults, which is why it is named twice.
2. *Durable:* `cdk deploy ferrenafe-scheduled-cycle -c scheduleEnabled=false`. The schedule's `state` is read from context in `bin/infra.ts`, defaulting to `ENABLED`, so disabling is declarative, survives redeploys, and is reviewable in a diff. A CDK test asserts both context values produce the expected `State` on the template.

Use 1 to stop the bleeding, then 2 so it stays stopped.

**Restoring a known-good snapshot.** The bucket is versioned, deliberately: `public-snapshot-stack.ts:31-35` records that a well-formed document reading "sin riesgo" during a real flood renders faithfully, and without versioning there would be no prior copy to compare against.

```
# 1. disable the schedule FIRST — otherwise the next cycle overwrites the restore
# 2. list versions, oldest last
aws s3api list-object-versions --bucket <bucket> --prefix status.json --profile ferrenafe
# 3. promote a known-good version by copying it onto the key
aws s3api copy-object --bucket <bucket> --key status.json \
  --copy-source "<bucket>/status.json?versionId=<version>" --profile ferrenafe
```

Three things about that:

- **Copy, not delete.** Promoting an old version by copying creates a *new* current version and destroys no history. Deleting the current version would also work and is refused: the writer policy deliberately carries no `s3:DeleteObject` (`public-snapshot-stack.ts:66-68`), and the rollback should not need a capability the design removed.
- **The Lambda role cannot do this, and that is deliberate.** Reading a specific version requires `s3:GetObjectVersion`, which neither the `SnapshotWriter` policy nor the bucket's public `s3:GetObject` statement grants. Restoring is an operator action under the `ferrenafe` profile. A compromised Lambda can write a bad current version; it cannot rewrite the history that proves it did.
- **Ordering is the whole point.** Restoring before disabling buys six hours at most.

**Level 3 (hand-publish) depends on the `SnapshotWriter` attachment**, which D25 deliberately does not disturb. Verifying that `uv run rain-alert-cycle` with `RAIN_ALERT_SNAPSHOT_BUCKET` still publishes is a runbook step **before** the first scheduled deploy, not after — a rollback path discovered broken during a rollback is not a rollback path.

**Level 4 (`cdk destroy`) leaves the table.** `RemovalPolicy.RETAIN` on a stateful resource, per the CDK skill. The consequence, stated so it is not discovered at the worst moment: a redeploy after a destroy fails with "table already exists", and the fix is to delete the orphaned table or `cdk import` it. The explicit `tableName` is what makes the import possible; it also stops CDK from replacing the table (and losing dedup state) on an unrelated change.

### D35 — Cron in `America/Lima`, exactly one concurrent execution, bounded retries

| Setting | Value | Why |
|---|---|---|
| `scheduleExpression` | `cron(0 0,6,12,18 * * ? *)` | Not `rate(6 hours)`: a rate schedule anchors to creation time and re-anchors on some updates, so the publish times drift and no two deploys agree. Fixed hours mean a resident and an operator can both say when the page last refreshed |
| `scheduleExpressionTimezone` | `America/Lima` | The `timezone` the messages already render in. 00:00 / 06:00 / 12:00 / 18:00 local are hours a person can reason about; the same instants in UTC are not |
| `flexibleTimeWindow` | `OFF` | Deterministic timing. A flexible window buys throughput smoothing this workload does not need |
| `reservedConcurrentExecutions` | **1** | EventBridge Scheduler delivery is at-least-once. Two concurrent cycles would both read an empty dedup history and both send — re-alerting the community, the one thing the design says must never happen. With a reservation of 1 the second invocation is throttled, Scheduler retries it, and the retry sees the record the first one wrote and correctly sends nothing. This is a one-line, template-assertable mitigation for a real correctness risk |
| Retry policy | `maximumRetryAttempts: 2`, `maximumEventAgeInSeconds: 3600` | *To verify at apply:* the Scheduler defaults, believed to be 185 attempts over 24 h, which is far too many for a six-hourly job — a stale retry landing five hours later publishes a snapshot stamped with an old cycle. An event older than an hour should be dropped; the next cycle is an hour away |
| Rate as IaC | unchanged | Design 3.5 and the proposal's question 3: six hours, and changing it is a redeploy |

---

## 3. Components and Data Flow

```
EventBridge Scheduler  cron(0 0,6,12,18) America/Lima
        │  (payload ignored — D29)
        ▼
entrypoints/lambda_handler.handler(event, context)
        │
        ├── wiring.build_cloud_deps()  ──────────────┐   leaf set #2, same CycleDependencies (D6)
        │        SsmConfigRepository (memoized)      │
        │        SenamhiWarningScraper(HttpxHtmlFetcher)
        │        OpenMeteoForecastProvider           │
        │        select_composer(config, …)  ← UNCHANGED (D14) → AgentBackedComposer
        │        StaticContactRepository             │
        │        ConsoleNotifier(stdout)             │
        │        DynamoDbAlertRepository             │
        │
        ▼
entrypoints/run_once(deps, publisher, report=stderr) -> (CycleResult, AlertConfig)
        │
        ├── RunAlertCycle(deps).execute()   ← UNCHANGED; the model still decides nothing
        │
        └── try: publisher.publish(build_snapshot(...))   ← forgiving guard, SNAPSHOT_FAILURE_PREFIX
        │
        ▼
print(as_json(result, config))  →  CloudWatch Logs  →  metric filter → Alarm 3
raise (never caught)            →  Errors metric    →  Alarm 1
no invocation at all            →  Invocations      →  Alarm 2 (treatMissingData: BREACHING)
```

```
       SSM /ferrenafe/rain-alert/*        DynamoDB ferrenafe-alerts-sent        S3 status.json
   GetParametersByPath (paginated)   Query pk=ALERT#slug, ConsistentRead      PutObject (versioned)
                 │                              │  PutItem (alerts, TTL)             │
                 └──────────── one execution role, four narrow grants ───────────────┘
                        + bedrock-agentcore:InvokeAgentRuntime on one ARN
                        + the existing SnapshotWriter managed policy, ATTACHED not re-granted
```

---

## 4. Sequence Diagram — one scheduled cycle that sends

```mermaid
sequenceDiagram
    autonumber
    participant S as EventBridge Scheduler
    participant L as lambda_handler
    participant W as build_cloud_deps
    participant C as SsmConfigRepository
    participant R as run_once
    participant U as RunAlertCycle
    participant A as AgentBackedComposer
    participant D as DynamoDbAlertRepository
    participant P as S3SnapshotPublisher

    S->>L: invoke (payload ignored)
    L->>W: build_cloud_deps()
    W->>C: load()            %% one GetParametersByPath, memoized for this cycle
    C-->>W: AlertConfig
    W->>W: select_composer(config)   %% UNCHANGED switch — config.composer decides
    L->>R: run_once(deps, publisher, report=stderr)
    R->>C: load()            %% memoized — no second SSM call
    R->>U: execute()
    U->>D: alerts_with_window_start_between(slug, …)   %% Query, ConsistentRead
    D-->>U: () — nothing sent for this window
    Note over U: AlertPolicy authorises; the level is already fixed
    U->>A: compose(request)
    A-->>U: AlertMessage(composed_by=agent | template fallback)
    U->>D: record_alert(...)   %% PutItem, sk = window_start#level, expires_at set
    U-->>R: CycleResult
    R->>P: publish(build_snapshot(...))
    Note over R,P: forgiving guard — a publish failure cannot cost the alert,<br/>and says so with SNAPSHOT_FAILURE_PREFIX on stderr
    R-->>L: (CycleResult, AlertConfig)
    L->>L: print(as_json(...))    %% the CloudWatch record for this cycle
    L-->>S: summary dict
```

Failure rows, all already decided elsewhere and none of them new here: the agent times out → D15's single `try` returns the template and emits one `AGENT_FALLBACK_USED` notice; SSM is unreadable → the cycle raises, Alarm 1 fires, the page freezes at the last good snapshot; the snapshot publish fails → the alert already went out, the log carries the prefix, Alarm 3 fires.

---

## 5. Contracts Touched

Nothing in `domain/`, `ports/` or `application/` changes. That is the headline and it is what makes the risk of this change infrastructural rather than behavioural.

| Contract | Change | Breaking? |
|---|---|---|
| `AlertRepository`, `ConfigRepository`, every other port | none | no — the two new adapters satisfy them structurally, like every other adapter |
| `CycleDependencies`, `RunAlertCycle`, `CycleResult` | none | no |
| The persisted alert/outage document (`serialization.py`) | none | no — the DynamoDB item **is** that document plus `pk`/`sk`/`expires_at` |
| `cli.main`'s observable behaviour | none, and this becomes an explicit spec requirement | no — `run_once` is extracted from it, not around it |
| `cli.default_now` | moves to `run_once.py`, re-exported | no — the name and `__all__` entry are preserved so citations resolve |
| `contracts/scheduled-cycle.json` | new | — |
| `PublicSnapshotStack` | **none** | no — D25's whole point |

---

## 6. File Changes

| Path | Action | Slice |
|---|---|---|
| `src/rain_alert/adapters/dynamodb_alert_repository.py` | Create | 1 — D26, D27, D28 |
| `src/rain_alert/adapters/ssm_config_repository.py` | Create | 1 — D28, D32 |
| `tests/unit/adapters/test_dynamodb_alert_repository.py`, `test_ssm_config_repository.py` | Create | 1 |
| `tests/conftest.py` | Modify | 1 — the forced-dummy-credentials fixture (D28) |
| `pyproject.toml` | Modify | 1 — `moto` in the dev group |
| `src/rain_alert/entrypoints/run_once.py` | Create | 2 — D29, `default_now` |
| `src/rain_alert/entrypoints/lambda_handler.py` | Create | 2 — D29 |
| `src/rain_alert/entrypoints/cli.py` | Modify | 2 — delegates to `run_once`, re-exports `default_now` |
| `src/rain_alert/entrypoints/wiring.py` | Modify | 2 — `build_cloud_deps` |
| `tests/architecture/test_layer_boundaries.py` | Modify | 2 — the handler's forbidden imports (D29) |
| `contracts/scheduled-cycle.json` | Create | 3 — D31, D32 |
| `tests/unit/adapters/test_agentcore_invoker.py` | Modify | 3 — the contract assertion |
| `infra/lib/scheduled-cycle-stack.ts` | Create | 3 — D25, D30, D31, D33, D35 |
| `infra/bin/infra.ts` | Modify | 3 — the second stack, its context props, `scheduleEnabled` |
| `infra/test/scheduled-cycle-stack.test.ts`, `infra/test/app.test.ts` | Create / Modify | 3 |
| `infra/scripts/build-lambda.sh` | Create | 3 — D30, including the no-`.so` / no-`awscrt` check |
| `infra/.gitignore`, `infra/cdk.json` | Modify | 3 — ignore `build/` and `cdk.context.json` (fix round 1: moved off it — it is CDK's own auto-written cache), commit the placeholder bucket name in `cdk.json`'s `context` block instead |
| `docs/runbooks/scheduled-cycle-deploy.md` | Create | 4 — seed SSM, deploy, subscribe SNS and confirm, verify rollback level 3 |
| `docs/superpowers/specs/2026-09-03-rain-alert-core-design.md` §10.3, `CLAUDE.md` | Modify | 4 — `us-east-2` |
| `docs/architecture/adapters.md`, `entrypoints-and-testing.md` | Modify | 4 — the two new adapters, the second entry point, the narrowed `TimeWindow.key()` line |

---

## 7. Testing Strategy

| Layer | What | How |
|---|---|---|
| Adapter (pure) | Item ↔ `AlertRecord`, `pk`/`sk` construction, the `￿` sentinel above every `Level`, `expires_at` > `sent_at + dedup_lookback_hours`, outage items carry no TTL | Parametrized tables, no client, no I/O |
| Adapter (pure) | SSM document parsing: every field, a missing parameter, a malformed JSON value, an out-of-range coordinate, an unknown `composer`, an unknown `active_channel` — all raise | Same |
| Adapter (service double) | `Query` key condition and `ConsistentRead`; `GetParametersByPath` paging; both paginators driven past one page | `moto`, under the forced-dummy-credentials fixture |
| Application | A standing `prepare` across two consecutive cycles sends once, against the table | `moto` + the real `DynamoDbAlertRepository` + `build_fake_deps` |
| Entrypoints | `cli.main` is byte-identical before and after the `run_once` extraction | Golden-output comparison against `--offline-fixtures` at a pinned `--now` |
| Entrypoints | `handler` ignores a hostile `event`; `handler` does not catch; the JSON line is emitted | `capsys`, injected deps |
| Architecture | `lambda_handler` imports neither `cli` nor the two local adapters, with a triangulation case proving the scan can fail | AST scan, extending `test_layer_boundaries.py` |
| Contract | `contracts/scheduled-cycle.json` matches `READ_TIMEOUT` and the SSM prefix constant | Python suite |
| CDK | `Timeout > env.RAIN_ALERT_AGENT_TIMEOUT_S + headroom`, `Timeout <= 900`, `env ≥ contract.agent_read_timeout_seconds` | `Template.fromStack` + `readFileSync` of the contract |
| CDK | The role has no `s3:PutObject` statement and does carry the imported managed policy ARN; each grant names one resource, never `*`; the SSM grant's resource ends with the contract prefix | `Template.hasResourceProperties` |
| CDK | Table `RemovalPolicy.RETAIN`, TTL attribute, no GSI; `reservedConcurrentExecutions: 1`; the cron and timezone; `scheduleEnabled` context → `State` | Same |
| CDK | Three alarms with their metric, threshold, period, `treatMissingData` and `AlarmActions`; the topic exists | Same |
| Hygiene | No twelve-digit id in any fixture, template or context file | Existing `test_repo_hygiene.py` |
| Live, once, recorded | One real cycle with `composer=agent`; the `Init Duration` and `Duration` from the `REPORT` line; one deliberate bad-ARN run proving the fallback still sends and still publishes; one SNS test notification | `docs/evidence/`, commands and real output |

**Not tested, and said so rather than implied**: that an SNS email is delivered, confirmed and read (D33); that Alarm 3's quoted filter pattern matches a real log event (D33, verified at apply); that the deployed function's timeout matches the template after a console edit (D31); that the Lambda's bundled `boto3` knows `invoke_agent_runtime` on the real runtime — the offline suite injects a client, so this is the one thing only a live invocation proves (D30), and it is the same gap `agentcore_invoker.py` was written under.

---

## 8. Migration / Rollout

No data migration. `.local-state/alerts.json` and the DynamoDB table are separate stores for separate entry points and neither reads the other; nothing is copied.

Rollout is staged so the risky part lands with a rollback already verified:

1. Slices 1–2 merge with no infrastructure. `main` behaves identically — nothing constructs the new adapters.
2. Verify rollback level 3 (hand-publish) still works, **before** the first stack deploy.
3. Seed the seven SSM parameters with `composer=template`. The change lands inert, as change 2 did.
4. Deploy the stack with `-c scheduleEnabled=false`. Invoke once by hand. Read the log line, the item in the table, the new object version.
5. Enable the schedule. Watch several real cycles.
6. Flip `composer=agent` in SSM. No deploy. Record the first agent-composed message in the change artifacts for the owner to judge.

Step 6 is last on purpose: it is the only step whose rollback needs no deploy at all.

---

## 9. Open Questions

- [ ] **`ALERT_RETENTION_DAYS` exact value.** Design floor: strictly above `dedup_lookback_hours`, asserted as a relation. Proposed 365. Spec to pin.
- [ ] **Alarm 3 (the snapshot metric filter) — minimum or recommended?** D33 lists it as recommended. It is the one that protects the judged artifact, and it is also the one whose filter pattern cannot be proven offline.
- [ ] **`memorySize`.** 512 MB is chosen for import speed, not working set, and is a guess until the first `REPORT` line. Nothing depends on it.
- [ ] **Whether `active_channel` should be operator-editable at all** (proposal question 2). This design keeps it, `String`, strictly validated against the one value a `Notifier` exists for. The spec may remove it.
- [ ] **When to take D25's follow-up** — the stable `managedPolicyName` and `fromManagedPolicyName`, deferred here only because of the judging window.

### To verify at apply

Nothing below is asserted from memory as load-bearing. Each names the check that must run before code depends on it.

1. **Lambda's maximum timeout** (900 s) and the current `python3.12` runtime's bundled boto3 version — D30, D31.
2. **`iam.ManagedPolicy.fromManagedPolicyArn`'s exact signature** in `aws-cdk-lib` 2.269, and that it performs no ARN parsing that would reject the test placeholder — D25.
3. **EventBridge Scheduler's default retry policy** (believed 185 attempts / 24 h) and the exact CDK prop names for `state`, `scheduleExpressionTimezone` and the retry policy on the L2 `Schedule` construct — D35.
4. **That SSM `StringList` has no escape mechanism for an embedded comma** — D32. The consequence is a mangled Spanish safety instruction, so the check is worth its minute.
5. **That `AWS::SSM::Parameter` cannot create a `SecureString`** — D32. Does not change the answer; recorded so the reasoning is checkable.
6. **DynamoDB TTL deletion latency wording**, and confirmation that expired-but-undeleted items can still be returned by `Query` — D27. Nothing depends on the number; the claim that TTL is not a correctness mechanism does.
7. **`uv export --frozen --no-dev` output**, and that the filtered set contains no compiled wheel — D30. The build script's own check is the standing guard; this is the first run of it.
8. **CloudWatch filter-pattern quoting** for a literal beginning with `[` — D33.
9. **The `bedrock-agentcore:InvokeAgentRuntime` action name and resource form** for the IAM grant, against the live service model, per the precedent `agentcore_invoker.py` set. **Closed, Phase 3 fix round 1**: the action name is confirmed correct. The *resource form* was wrong as originally implemented — see the new "AgentCore grant — two resources, not one" note after D25 below, which this item's own text anticipated ("resource form", not merely "action name") and the first implementation missed.
