# Delta for Agent Message Composition

Base capability: `agent-message-composition`, not yet archived to `openspec/specs/`. This delta is written against:

- `openspec/changes/composer-agent/specs/agent-message-composition/spec.md` (cited as **composer-agent spec**), and
- `openspec/changes/agent-system-prompt/specs/agent-message-composition/spec.md` (cited as **agent-system-prompt spec**).

Validator rules from the composer-agent spec are NOT reopened except where stated below (additive: a new unit and a new allowed-date source). Normative words follow RFC 2119. Classes: **[offline]**, **[live]** as in the agent-system-prompt spec.

## MODIFIED Requirements

### Requirement: The agent has no side-effect capability [offline]

(Previously, composer-agent spec: "The agent MUST be given no tool, no send capability, and no write path.") The agent MUST be given no send capability and no write path, and exactly one tool: the read-only `get_recent_alerts` defined by `agent-alert-history`. The agent's return value is a structured output consumed by the validator; it cannot itself send, notify, or record anything. Any code, test or docstring asserting "no tools" MUST be updated to "one read-only tool".

#### Scenario: Agent execution reaches no side effect
- GIVEN the agent runs to completion, having called the tool zero or more times
- WHEN its output is received
- THEN no notification was sent and no record was written before validation ran, and both remain exclusively under `RunAlertCycle`'s control

#### Scenario: Tool list is exactly one
- GIVEN the agent is built for a request
- WHEN its tools are inspected
- THEN the only tool is `get_recent_alerts`

### Requirement: Prompt content is limited to MessageRequest and excludes recipients [offline]

(Previously, composer-agent spec and agent-system-prompt spec: fenced data built solely from `MessageRequest` fields.) The fenced data MUST be built solely from `MessageRequest` fields, which now include `ContinuitySummary` (see `alert-cycle` delta). It MUST NOT contain any contact identifier, token (other than the fence token), account id, nor any prior alert title or body. The per-call user turn MUST still carry no fixed rule text.

#### Scenario: Continuity in the prompt traces to the request
- GIVEN a `MessageRequest` with a `ContinuitySummary`
- WHEN the prompt is constructed
- THEN every continuity value in the fenced data equals the corresponding `ContinuitySummary` value

#### Scenario: No prior text in the prompt
- GIVEN stored prior alerts with distinctive titles and bodies
- WHEN the prompt is constructed
- THEN none of that text appears in the prompt

#### Scenario: Recipient data never reaches the prompt
- GIVEN a cycle with active contacts
- WHEN the prompt is constructed
- THEN it contains no contact identifier, token, or account id

### Requirement: The system prompt allows continuity, and only continuity, to refer to earlier days [offline][live]

(Amends the "report only what the data states" fixed rule 9 and the prohibition on describing anything as already having happened, owner-approved rule D49 of the agent-system-prompt change.) The system prompt MUST permit referring to previous days only to state continuity facts that come from the tool result or the fenced `ContinuitySummary`: the number of consecutive days, the previous level, and whether the situation is escalating. It MUST continue to forbid describing any other event as already having happened. It MUST instruct that continuity figures be written in digits (for example "3 dias consecutivos"), not as ordinals or words. It MUST instruct calling the tool before composing. It MUST remain free of decimal digits and of any concrete per-call token. All other fixed rules of the agent-system-prompt spec are unchanged.

The amended wording is a prompt change gated by the live acceptance requirement below.

#### Scenario: Continuity exception present and narrow
- GIVEN the system prompt passed by `build_agent`
- WHEN its text is inspected
- THEN it permits continuity references (consecutive days, previous level, escalation) and still forbids describing anything else as already having happened

#### Scenario: Digits for continuity figures
- GIVEN the system prompt
- WHEN its text is inspected
- THEN it instructs writing continuity figures in digits

#### Scenario: Tool-first instruction
- GIVEN the system prompt
- WHEN its text is inspected
- THEN it instructs consulting the recent-alerts tool before composing

#### Scenario: Prompt stays digit-free
- GIVEN the system prompt constant
- WHEN every character is checked with `str.isdigit`
- THEN none is a digit

### Requirement: Live acceptance gate [live]

(Extends the agent-system-prompt spec's live gate.) After deploy with the amended prompt and the tool, the live acceptance script MUST be re-run. At least 8 of 10 drafts on the incident-like request MUST be accepted by the validator as amended by this change (additive rules only). Separately, on the 3-day escalation scenario (yellow 14.2 mm, orange 31.5 mm, red 68.4 mm; 3 runs per day = 9 messages): at least 5 of the 6 day-2 and day-3 messages MUST reference continuity, 0 of the 3 day-1 messages MUST claim prior days, and 0 validator rejections MUST be caused by continuity numbers or dates. Outcomes MUST be recorded under `docs/evidence/`. Verified outside the offline suite; not a CI test.

#### Scenario: Gate met
- GIVEN the deployed agent with the tool and amended prompt
- WHEN the incident-like request is run 10 times and the 3-day scenario 3 times per day
- THEN at least 8 of 10 incident-like drafts are accepted, at least 5 of 6 day-2/3 messages reference continuity, 0 of 3 day-1 messages claim prior days, and the evidence file records every outcome

#### Scenario: Gate missed
- GIVEN any threshold above is not met
- WHEN the result is recorded
- THEN the change is not done, the wording is iterated, and the template keeps sending in the meantime

### Requirement: Continuity numbers and dates are accepted only when traceable to ContinuitySummary [offline]

(Extends "Every number in the body is traceable to MessageRequest", composer-agent spec; allowed set keyed by unit.) The validator's allowed set MUST gain a **days** unit and an additional date source:

- a day-count candidate (an integer followed by `dia`/`dias`, accent-folded, or written before it in the clause) is allowed only when it equals `ContinuitySummary.consecutive_days`;
- a date candidate (`DD/MM/YYYY`) is allowed in addition to window dates only when it equals one of `ContinuitySummary`'s prior local dates;
- every other existing rule (unit-keyed membership, ambiguous-form refusal, words-for-quantities rejection, verbatim-span exemption) MUST be unchanged;
- with no `ContinuitySummary`, or a summary with `consecutive_days == 0`, no day count and no prior date is allowed.

Numbers that appear in the tool result but not in `ContinuitySummary` MUST NOT be allowed.

#### Scenario: Matching day count accepted
- GIVEN `ContinuitySummary.consecutive_days = 3`
- WHEN the body states "3 dias consecutivos"
- THEN the number is accepted

#### Scenario: Wrong day count rejected
- GIVEN `ContinuitySummary.consecutive_days = 2`
- WHEN the body states "3 dias consecutivos"
- THEN the message is rejected and the template's output is sent

#### Scenario: Prior date accepted
- GIVEN a prior local date `22/10/2026` in the summary
- WHEN the body states "22/10/2026"
- THEN the date is accepted

#### Scenario: Untraceable date rejected
- GIVEN a body date equal to neither a window date nor a summary prior date
- WHEN validation runs
- THEN the message is rejected

#### Scenario: Day count not usable as another unit
- GIVEN `ContinuitySummary.consecutive_days = 3` and no request value of 3 mm
- WHEN the body states "3 mm"
- THEN the message is rejected, because 3 is a day count on this request, not a millimetre reading

#### Scenario: Day-1 claims no prior days
- GIVEN no history (`consecutive_days == 0`)
- WHEN the body states "2 dias consecutivos" or any prior date
- THEN the message is rejected

#### Scenario: Day-1 message without continuity is accepted
- GIVEN no history
- WHEN the body makes no continuity statement
- THEN validation raises no continuity violation

#### Scenario: Tool-vs-summary contract
- GIVEN the same stored alerts
- WHEN the tool's `consecutive_days`, `previous_level` and local dates are compared with the domain `ContinuitySummary`
- THEN they are identical (contract test; fails if either side changes alone)

### Requirement: Tool failure never fails composition [offline]

A tool failure (see `agent-alert-history`, "Any tool failure degrades to empty history") MUST NOT cause an agent fallback by itself. The draft produced with empty history MUST be validated against `ContinuitySummary` from the request; if the validator rejects it, or any other composer failure mode occurs, the composer MUST fall back to the template's output as already specified ("Every composer failure mode falls back to the template", composer-agent spec) with exactly one operator notice.

#### Scenario: Tool fails, draft accepted
- GIVEN the tool returns empty history and the draft contains no continuity claim
- WHEN the cycle composes
- THEN the draft is accepted and `composer == "agent"`

#### Scenario: Tool fails, draft claims continuity
- GIVEN the tool returns empty history but `ContinuitySummary` is empty and the draft states "3 dias consecutivos"
- WHEN the cycle composes
- THEN the draft is rejected, the template's output is sent, `composer == "template"`, and one operator notice is emitted

### Requirement: Composition stays within the deadline with the extra round trip [live]

The additional tool round trip MUST NOT cause composition to exceed `agent_read_timeout`. Measured composition latency for the evidence runs MUST be recorded; on timeout the existing deadline fallback applies.

#### Scenario: Latency within budget
- GIVEN the evidence runs
- WHEN their latencies are recorded
- THEN every run completes within `agent_read_timeout` or falls back per "Deadline exceeded"

## UNCHANGED (regression requirements) [offline]

### Requirement: Invocation only after send authorization, template fallback, composer attribution and default selection are unchanged

"Invocation only after send authorization", "Every composer failure mode falls back to the template", "Operator is notified exactly once per fallback", "The alert record states which composer produced the message" and "Composer selection defaults to the template" (composer-agent spec) MUST hold unchanged. The invocation payload shape `{"prompt": ...}` is unchanged unless the design explicitly changes it; that decision belongs to design and MUST preserve the fence-marker contract of the agent-system-prompt spec. The architecture test (no `agent/` <-> `rain_alert` imports) MUST stay green.

#### Scenario: Default configuration never invokes the agent
- GIVEN configuration does not select the agent composer
- WHEN an authorized send runs
- THEN the agent seam is called zero times, the tool is never called, and `composer == "template"`

#### Scenario: No cross-import
- GIVEN the architecture test
- WHEN it runs after the change
- THEN neither `agent/` imports `rain_alert` nor `rain_alert` imports `agent/`

#### Scenario: Offline suite needs no model, network or credentials
- GIVEN the default test command
- WHEN it runs
- THEN every offline scenario in this delta passes with fakes, no network call, no model call and no credential

## Assumptions

- Exact prompt wording, tool result field names beyond those listed, and the partition binding mechanism are design decisions.
- "Day" means a local calendar day in the city's timezone.
