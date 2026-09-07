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

#### Scenario: The steps happen in the stated order
- GIVEN AlertPolicy authorizes sending
- WHEN `RunAlertCycle` runs
- THEN the recorded sequence of port calls is configuration, warnings, forecast, outage state, risk evaluation, dedup query, compose, recipients, notify, record — in that order

### Requirement: Deterministic message template satisfies MessageComposer

The `MessageComposer` port MUST be satisfied in this change by a deterministic template that MUST include the city name, the risk level, the affected window, and the evaluator's reasons, except where the body already states the same fact in its own dedicated sentence.

*Amended 2026-09-06 (verify finding W8).* The sentence previously required "each reason" without exception, which the shipped template contradicts: `SenamhiUnavailableReason` renders as `None` and is stated instead by the degraded sentence required below. The decision was made and argued in design section 3.1 — the outage was otherwise stated twice, in two languages, one of them English operator audit wording under a Spanish `Motivos:` header — but the spec sentence was never amended, so a reader comparing the two found a direct contradiction. The exception is deliberately narrow: a reason may be omitted only when another requirement makes the same fact mandatory elsewhere in the same body.

#### Scenario: Template content
- GIVEN a `prepare` assessment with reasons and a window
- WHEN the template composes the message
- THEN the message text includes the city name, the level, the window, and each reason

#### Scenario: A reason the body already states in its own sentence is not repeated
- GIVEN a `prepare` assessment produced with SENAMHI unavailable, whose reasons include the SENAMHI-unavailable reason
- WHEN the template composes the message
- THEN the outage is stated exactly once, by the degraded sentence, and not repeated as a `Motivos:` bullet

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

#### Scenario: Nothing is recorded before delivery
- GIVEN an authorized send
- WHEN `RunAlertCycle` completes
- THEN the call to `Notifier.send_alert` precedes the call to `AlertRepository.record_alert`
