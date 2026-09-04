# Alert Cycle Specification

## Purpose

`RunAlertCycle`, the use case orchestrating the cycle from design spec section 4 (steps 1–5 and 7). Step 6 (compose) resolves to the deterministic template satisfying the `MessageComposer` port in this change.

## Requirements

### Requirement: Cycle orchestration order

`RunAlertCycle` MUST execute, in order: load configuration, fetch warnings, fetch forecast, evaluate risk, apply the dedup policy, and — only if the policy authorizes sending — compose the message, notify, and record the sent alert.

#### Scenario: Authorized send runs the full chain
- GIVEN AlertPolicy authorizes sending for a new `prepare` assessment
- WHEN `RunAlertCycle` runs
- THEN the composer, `Notifier`, and `AlertRepository.record` are all invoked

#### Scenario: Dedup short-circuits the cycle
- GIVEN AlertPolicy does not authorize sending (same level, same window)
- WHEN `RunAlertCycle` runs
- THEN neither the composer nor `Notifier` is invoked, and nothing is recorded

### Requirement: Deterministic message template satisfies MessageComposer

The `MessageComposer` port MUST be satisfied in this change by a deterministic template that MUST include the city name, the risk level, the affected window, and the evaluator's reasons.

#### Scenario: Template content
- GIVEN a `prepare` assessment with reasons and a window
- WHEN the template composes the message
- THEN the message text includes the city name, the level, the window, and each reason

### Requirement: Degraded-mode message discloses source unavailability

When the assessment was produced in degraded mode (SENAMHI unavailable), the composed message MUST state that the official SENAMHI source was unavailable.

#### Scenario: Degraded disclosure in the message
- GIVEN a `prepare` assessment produced with SENAMHI unavailable
- WHEN the template composes the message
- THEN the message text states that the official SENAMHI source was unavailable

### Requirement: Sent alerts are recorded after notification

`RunAlertCycle` MUST call `AlertRepository` to record the level, window, and message only after `Notifier` delivery, and only for authorized sends.

#### Scenario: Record after notify
- GIVEN an authorized send
- WHEN `RunAlertCycle` completes
- THEN `AlertRepository` holds a record of the level, window, and message for this cycle
