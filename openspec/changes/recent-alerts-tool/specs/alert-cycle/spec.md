# Delta for Alert Cycle

Base: `openspec/specs/alert-cycle/spec.md` as amended by the unarchived `openspec/changes/composer-agent/specs/alert-cycle/spec.md`. Normative words follow RFC 2119. The domain stays free of infrastructure and model concerns: `ContinuitySummary` is computed by a pure function over already-recorded alerts.

## ADDED Requirements

### Requirement: ContinuitySummary is a pure domain value derived from recorded alerts

The domain MUST define `ContinuitySummary` with: `consecutive_days` (integer >= 0, counted over local calendar days in the city's timezone ending at the most recent recorded alert), `previous_level` (optional), `escalated` (boolean), and the prior local dates of the counted alerts. It MUST be produced by a pure function over `AlertRecord`s with no I/O. With no records it MUST be the empty summary (`consecutive_days == 0`, no previous level, `escalated == false`, no dates).

#### Scenario: Three consecutive escalating days
- GIVEN records on three consecutive local days at levels rising each day
- WHEN the summary is computed
- THEN `consecutive_days == 3`, `escalated == true`, and the three local dates are present

#### Scenario: Gap resets the streak
- GIVEN records on day 1 and day 3 only
- WHEN the summary is computed
- THEN `consecutive_days == 1`

#### Scenario: Several records on one day count once
- GIVEN two records on the same local day
- WHEN the summary is computed
- THEN that day counts once

#### Scenario: No history
- GIVEN no records
- WHEN the summary is computed
- THEN it is the empty summary

#### Scenario: Purity
- GIVEN the same records
- WHEN the function is called twice
- THEN the results are equal and no port is called

### Requirement: History is read through the existing alert repository before composition

Before composing, `RunAlertCycle` MUST obtain recent alerts through the `AlertRepository` port (the same port the dedup policy uses) and compute the `ContinuitySummary` for the request. A read failure MUST yield the empty summary and MUST NOT fail the cycle. The read MUST NOT change whether the policy authorizes a send, and MUST be performed only for authorized sends.

#### Scenario: Authorized send computes the summary
- GIVEN the policy authorizes a send and the repository holds prior records
- WHEN the cycle composes
- THEN the `MessageRequest` carries the `ContinuitySummary` of those records

#### Scenario: Repository read fails
- GIVEN the repository raises on the history read
- WHEN the cycle composes
- THEN the request carries the empty summary and the cycle continues

#### Scenario: Dedup short-circuit performs no history read
- GIVEN the policy does not authorize a send
- WHEN the cycle runs
- THEN no history read for continuity is made

#### Scenario: History never changes authorization
- GIVEN identical assessments with different histories
- WHEN the policy runs
- THEN its decision is the same as it was before this change for each

## MODIFIED Requirements

### Requirement: Deterministic message template satisfies MessageComposer

(Previously: `MessageRequest` carried level, window, reasons and related fields only.) `MessageRequest` MUST additionally carry a `ContinuitySummary` (empty by default). The deterministic template MUST remain valid for any `MessageRequest`: it MAY ignore the summary, and its output MUST still pass the validator including the new continuity rules (a template that states no continuity raises none). All existing content obligations are unchanged.

#### Scenario: Template output with a summary present
- GIVEN a `MessageRequest` carrying a non-empty `ContinuitySummary`
- WHEN the template composes
- THEN the message still includes the city, level, window and reasons, and passes validation

#### Scenario: Default summary
- GIVEN a `MessageRequest` built without a summary
- WHEN it is inspected
- THEN its summary is the empty summary

### Requirement: Validator accepts continuity day counts and prior dates only when traced

The message validator MUST accept day counts and prior dates in an agent body only as stated in `agent-message-composition` ("Continuity numbers and dates are accepted only when traceable to ContinuitySummary"). The validator's existing behavior for all other numbers MUST be unchanged, and the template-always-passes invariant MUST hold for every `MessageRequest` including those with a summary.

#### Scenario: Traced day count accepted
- GIVEN `consecutive_days = 3`
- WHEN a body states "3 dias consecutivos"
- THEN no violation is raised

#### Scenario: Untraced day count rejected
- GIVEN `consecutive_days = 0`
- WHEN a body states "3 dias consecutivos"
- THEN a violation is raised

#### Scenario: Existing rules unchanged
- GIVEN the existing validator test suite
- WHEN it runs after the change
- THEN every existing scenario still passes

### Requirement: Sent alerts are recorded after notification

(Unchanged in behavior.) `RunAlertCycle` MUST continue to record level, window, message and composer only after `Notifier` delivery and only for authorized sends. Records written by the cycle are the sole source of future history; this change MUST NOT add any write path from the agent or the tool, and the cycle's record format MUST remain readable by the history tool.

#### Scenario: Record after notify
- GIVEN an authorized send
- WHEN `RunAlertCycle` completes
- THEN the notification call precedes the record call

#### Scenario: Agent and tool write nothing
- GIVEN an authorized send composed by the agent using the tool
- WHEN the cycle completes
- THEN the only record written is the cycle's own single record
