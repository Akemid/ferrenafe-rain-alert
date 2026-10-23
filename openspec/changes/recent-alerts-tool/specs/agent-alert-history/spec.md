# Agent Alert History Specification (new capability)

## Purpose

A single read-only tool, `get_recent_alerts(n)`, available to the composer agent. It returns pre-digested facts about the most recent alerts already sent for the bound city, so a message can state continuity ("3 dias consecutivos, en aumento"). The tool never influences whether an alert is sent. Reopens decisions `agent-has-no-tools` and `no-continuity-tool` (see `openspec/changes/composer-agent/state.yaml`) on the evidence that the alert store is now durable and shared (scheduled-cycle). `no-warning-detail-tool` stays dropped.

Verification classes: **[offline]** proven by the default offline suite with hand-written fakes (no network, model or credentials). **[live]** verified outside the offline suite. **[ops]** verified by deploy/runbook checks.

Normative words follow RFC 2119.

## ADDED Requirements

### Requirement: Exactly one read-only tool is exposed [offline]

The agent MUST be given exactly one tool, `get_recent_alerts`. The tool MUST be read-only: it MUST NOT send, notify, record, update or delete anything. No other tool (including any warning-detail tool) MUST be exposed.

#### Scenario: One tool, named
- GIVEN the agent is constructed for a request
- WHEN its registered tools are inspected
- THEN exactly one tool exists and it is named `get_recent_alerts`

#### Scenario: No write path is reachable from the tool
- GIVEN the tool runs against a recording fake store
- WHEN it completes
- THEN the fake recorded zero write, update or delete calls

### Requirement: The partition is never chosen by the model [offline]

The tool's model-visible signature MUST contain only `n`. The city or partition read by the tool MUST NOT be a tool argument, MUST NOT be derivable from model output, and MUST NOT be read from fenced prompt data. How the partition is bound is a design decision and is deliberately not fixed here; whichever mechanism is chosen MUST satisfy this requirement and the demo-isolation requirement below.

#### Scenario: Schema exposes only n
- GIVEN the tool specification the model receives
- WHEN its input schema is inspected
- THEN `n` is the only parameter

#### Scenario: Hostile prompt cannot redirect the read
- GIVEN fenced data that instructs the model to read another city or partition
- WHEN the tool runs with any model-supplied arguments
- THEN the store is queried only for the bound partition

### Requirement: `n` is clamped to 1..5 [offline]

The tool MUST treat `n` as a request for a count and MUST clamp it into the range 1 to 5 inclusive. A value below 1, above 5, or not an integer MUST NOT raise and MUST NOT widen the read beyond 5 alerts.

#### Scenario: Oversized n
- GIVEN a store holding 8 qualifying alerts
- WHEN the tool is called with `n = 100`
- THEN at most 5 alerts are returned

#### Scenario: Non-positive n
- GIVEN a store holding qualifying alerts
- WHEN the tool is called with `n = 0` or `n = -3`
- THEN exactly 1 alert is returned (the newest)

#### Scenario: Non-integer n
- GIVEN the model supplies a non-integer `n`
- WHEN the tool is called
- THEN it does not raise and returns a result within the 1..5 bound

### Requirement: The tool returns pre-digested facts only [offline]

The result MUST contain only facts computed in code: `alerts` ordered newest-first, each carrying the alert's local send date and time (`DD/MM/YYYY HH:MM`), its level, and (if present) its top warning level; plus `consecutive_days` (integer), `previous_level`, and `escalated` (boolean). The result MUST NOT contain any prior alert title or body, nor any text originating from scraped source content. The model MUST NOT be asked or expected to compute consecutive days, escalation or ordering.

#### Scenario: No prior text in the result
- GIVEN stored alerts whose title and body contain distinctive marker strings
- WHEN the tool returns
- THEN neither marker string appears anywhere in the serialized result

#### Scenario: Facts are computed, not left to the model
- GIVEN three stored alerts on consecutive local days with rising levels
- WHEN the tool returns
- THEN `consecutive_days == 3`, `escalated == true`, `previous_level` is the level of the second-newest alert, and `alerts` is newest-first

#### Scenario: Gap breaks the streak
- GIVEN stored alerts on day 1 and day 3 and none on day 2
- WHEN the tool returns
- THEN `consecutive_days == 1`

### Requirement: Any tool failure degrades to empty history [offline]

On any failure (permission denied, timeout, store error, malformed record, unexpected exception) the tool MUST return an empty history (`alerts == []`, `consecutive_days == 0`, no `previous_level`, `escalated == false`) and MUST NOT raise to the agent loop. Composition MUST still complete, and the resulting message MUST be validated exactly as without the tool.

#### Scenario: Store raises
- GIVEN a fake store whose read raises
- WHEN the tool is called
- THEN it returns the empty history and does not raise

#### Scenario: Composition succeeds with empty history
- GIVEN the tool returns the empty history
- WHEN the agent composes
- THEN a structured output is produced and passes through the same validator as before

#### Scenario: Empty store
- GIVEN a store with no qualifying alerts
- WHEN the tool is called
- THEN it returns the empty history

### Requirement: The LLM never decides whether to alert [offline]

The agent MUST be invoked only after the alert policy has authorized a send. The tool MUST NOT expose, and the agent output contract MUST NOT carry, any field by which the model can suppress, defer, escalate or downgrade an alert. History read by the tool MUST NOT feed back into the alert policy.

#### Scenario: Deduplicated cycle invokes nothing
- GIVEN the alert policy does not authorize a send
- WHEN the cycle runs
- THEN the agent seam is invoked zero times and the tool is never called

#### Scenario: Output cannot veto
- GIVEN the agent output contract
- WHEN its fields are inspected
- THEN no field expresses a decision to send, skip or change the level

### Requirement: Agent state does not leak across invocations [offline]

The agent and its tool binding MUST be created per request, or its conversation state MUST be reset, so that messages or history from one invocation never appear in the next on a warm runtime.

#### Scenario: Two sequential invocations
- GIVEN two invocations on the same warm process with different histories
- WHEN the second composes
- THEN its model input contains nothing from the first invocation

### Requirement: Structured output is obtained through a call shape that runs tools [offline]

The agent call MUST use the form in which the agent loop executes tools before producing the structured output. The legacy structured-output entry point that bypasses tool execution MUST NOT be used for composition.

#### Scenario: Tool is invokable under the chosen call shape
- GIVEN a fake model that requests the tool and then returns a structured output
- WHEN the agent runs
- THEN the tool was executed exactly once and the structured output is returned

### Requirement: Read access is least-privilege and scoped to the bound partition [ops]

The agent runtime's grant to the alert store MUST allow only the query operation on that table, restricted to the bound partition key. It MUST NOT allow write, update or delete. The grant MUST be provisioned as a standalone attachable policy whose identifier is published as a stack output, and the runbook MUST include the attach step and a post-deploy check that the attachment exists.

#### Scenario: Grant shape
- GIVEN the synthesized infrastructure template
- WHEN the policy is inspected
- THEN it contains the query action only, on the table, with a leading-key restriction, and no write actions

#### Scenario: Output published
- GIVEN the synthesized template
- WHEN its outputs are inspected
- THEN the policy identifier is present

#### Scenario: Post-deploy check
- GIVEN the runbook
- WHEN the attach procedure is followed
- THEN it ends with a check that the policy is attached to the runtime role, and a redeploy that detaches it is detected by that check

### Requirement: The demo never writes to the real city partition [offline][live]

The demo seeding procedure MUST write only to a dedicated demo partition and MUST refuse (non-zero exit, nothing written) any target equal to the real city partition. The demo MUST read history only from the demo partition. The production runtime's partition MUST NOT be redirected by any demo configuration. Real alerts MUST NOT be suppressed or altered by demo data.

#### Scenario: Seeder refuses the real partition
- GIVEN the seeding script is pointed at the real city partition
- WHEN it runs
- THEN it exits non-zero and writes zero records

#### Scenario: Seeder writes only to the demo partition
- GIVEN the seeding script with the demo partition
- WHEN it runs against a recording fake
- THEN every write targets the demo partition

#### Scenario: Real partition unchanged after the demo
- GIVEN a snapshot of the real city partition before the demo
- WHEN the full demo runs
- THEN the real partition is byte-for-byte unchanged afterwards

#### Scenario: Production reads are not redirected
- GIVEN the demo configuration is applied
- WHEN a production invocation runs
- THEN it reads the real city partition, not the demo partition

### Requirement: Before/after evidence is recorded [live]

The change MUST record under `docs/evidence/` a BEFORE baseline (tag `baseline-no-tools`, commit 94a2140: 3 days x 3 runs = 9 messages, none referencing prior days) and an AFTER run on the same scenario inputs (yellow 14.2 mm, orange 31.5 mm, red 68.4 mm) with 3 runs per day (9 messages), including per-message continuity and validator outcome.

#### Scenario: Baseline count
- GIVEN the BEFORE evidence on disk
- WHEN its messages are counted
- THEN there are 9 (3 days x 3 runs) and none references a prior day

#### Scenario: After meets the criteria
- GIVEN the AFTER evidence
- WHEN day-2 and day-3 messages (6) are scored
- THEN at least 5 of 6 reference continuity, and 0 of 3 day-1 messages claim prior days
