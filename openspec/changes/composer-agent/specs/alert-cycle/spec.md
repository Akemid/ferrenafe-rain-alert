# Delta for Alert Cycle

## MODIFIED Requirements

### Requirement: Deterministic message template satisfies MessageComposer

The `MessageComposer` port MAY be satisfied by either the deterministic template or the agent-backed composer (`agent-message-composition`); the agent-backed composer MUST fall back to producing the deterministic template's output whenever its own invocation or validation fails (`agent-message-composition`, "Every composer failure mode falls back to the template"). Whichever implementation runs, the resulting message MUST include the city name, the risk level, the affected window, and the evaluator's reasons, except where the body already states the same fact in its own dedicated sentence.

(Previously: only the deterministic template satisfied the port; no fallback concept existed.)

*Amended 2026-09-06 (verify finding W8).* The sentence previously required "each reason" without exception, which the shipped template contradicts: `SenamhiUnavailableReason` renders as `None` and is stated instead by the degraded sentence required below. The decision was made and argued in design section 3.1 — the outage was otherwise stated twice, in two languages, one of them English operator audit wording under a Spanish `Motivos:` header — but the spec sentence was never amended, so a reader comparing the two found a direct contradiction. The exception is deliberately narrow: a reason may be omitted only when another requirement makes the same fact mandatory elsewhere in the same body.

#### Scenario: Template content
- GIVEN a `prepare` assessment with reasons and a window
- WHEN the template composes the message
- THEN the message text includes the city name, the level, the window, and each reason

#### Scenario: A reason the body already states in its own sentence is not repeated
- GIVEN a `prepare` assessment produced with SENAMHI unavailable, whose reasons include the SENAMHI-unavailable reason
- WHEN the template composes the message
- THEN the outage is stated exactly once, by the degraded sentence, and not repeated as a `Motivos:` bullet

### Requirement: Sent alerts are recorded after notification

`RunAlertCycle` MUST call `AlertRepository` to record the level, window, message, and the composer that produced the message (`agent-message-composition`, "The alert record states which composer produced the message") only after `Notifier` delivery, and only for authorized sends.

(Previously: recorded level, window, and message only; `composer` was hardcoded and not treated as cycle-produced data.)

#### Scenario: Record after notify
- GIVEN an authorized send
- WHEN `RunAlertCycle` completes
- THEN `AlertRepository` holds a record of the level, window, and message for this cycle

#### Scenario: Nothing is recorded before delivery
- GIVEN an authorized send
- WHEN `RunAlertCycle` completes
- THEN the call to `Notifier.send_alert` precedes the call to `AlertRepository.record_alert`

#### Scenario: Recorded composer matches what was actually sent
- GIVEN an authorized send where the agent composer's output was accepted
- WHEN `RunAlertCycle` completes
- THEN the recorded `AlertRecord.composer` reads `"agent"`, matching the message text that was sent
