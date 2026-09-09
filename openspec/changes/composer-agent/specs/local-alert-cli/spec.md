# Delta for Local Alert CLI

## MODIFIED Requirements

### Requirement: CLI output is informative for calibration

The CLI output MUST include the resulting risk level, the evaluator's reasons, the exact message text that would have been sent, and the composer that produced that text, whether or not a send was authorized.

(Previously: composer was not part of the printed output, because only the template composer existed.)

#### Scenario: Full output on every run
- GIVEN any cycle outcome (sent or deduplicated)
- WHEN the CLI finishes
- THEN it prints the level, the reasons, the composed message text, and the composer that produced it

#### Scenario: Printed composer reflects an actual fallback
- GIVEN the CLI runs with the agent composer selected and the agent invocation fails
- WHEN the cycle composes
- THEN the printed output states `"template"` as the composer, not `"agent"`

## ADDED Requirements

### Requirement: CLI agent composer selection is opt-in and defaults off

The CLI MUST expose a flag that selects the agent-backed composer for that run. Without the flag, the CLI MUST use the template composer, and cycle behavior MUST be identical to behavior before this change.

#### Scenario: No flag preserves pre-change behavior
- GIVEN the CLI runs with no opt-in flag
- WHEN it composes a message
- THEN it uses the template composer only, and the agent invocation seam is never called

#### Scenario: Flag enables the agent-backed path for that run
- GIVEN the CLI runs with the opt-in flag set
- WHEN it composes a message
- THEN it uses the agent-backed composer for that run, subject to `agent-message-composition`'s validation and fallback rules
