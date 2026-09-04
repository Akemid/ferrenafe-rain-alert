# Risk Evaluation Specification

## Purpose

Domain rules that turn `Warning` and `Forecast` data (each carrying a per-source availability status) into a `RiskAssessment` (level, window, reasons). Reference: design spec sections 6.1–6.3. The evaluator has zero AWS/HTTP/HTML/LLM imports.

## Requirements

### Requirement: Full-source risk evaluation

When both SENAMHI and Open-Meteo report `available`, the evaluator MUST derive the risk level from the thresholds in configuration.

#### Scenario: Imminent from official warning
- GIVEN SENAMHI reports an orange or red warning for Lambayeque
- WHEN the evaluator runs
- THEN the level is `imminent`

#### Scenario: Imminent from forecast alone
- GIVEN both sources are available and Open-Meteo forecasts 29.8 mm in the next 24 h at 70% probability
- WHEN the evaluator runs
- THEN the level is `imminent`

#### Scenario: Prepare on threshold
- GIVEN SENAMHI reports a yellow warning AND Open-Meteo forecasts 10 mm in 48 h at 60% probability
- WHEN the evaluator runs
- THEN the level is `prepare`

#### Scenario: Below prepare threshold
- GIVEN SENAMHI reports a yellow warning AND Open-Meteo forecasts 9 mm in 48 h at 60% probability
- WHEN the evaluator runs
- THEN the level is `none`

### Requirement: Degraded evaluation when SENAMHI is unavailable

When SENAMHI status is `unavailable`, the evaluator MUST require probability >= 70% (instead of 60%) for `prepare` to fire from Open-Meteo alone, and MUST leave `imminent` conditions unchanged.

#### Scenario: Degraded prepare requires 70%
- GIVEN SENAMHI is unavailable AND Open-Meteo forecasts 15 mm in 48 h at 65% probability
- WHEN the evaluator runs
- THEN the level is `none`

#### Scenario: Degraded reasons disclose the outage
- GIVEN SENAMHI is unavailable AND Open-Meteo qualifies for `prepare` at >= 70%
- WHEN the evaluator runs
- THEN the reasons list states that the official SENAMHI source was unavailable

### Requirement: Degraded evaluation when Open-Meteo is unavailable

When Open-Meteo status is `unavailable`, the evaluator MUST still allow `imminent` from a SENAMHI orange or red warning and MUST NOT produce `prepare`.

#### Scenario: Imminent still fires
- GIVEN Open-Meteo is unavailable AND SENAMHI reports an orange warning
- WHEN the evaluator runs
- THEN the level is `imminent`

#### Scenario: Prepare cannot fire without forecast data
- GIVEN Open-Meteo is unavailable AND SENAMHI reports a yellow warning
- WHEN the evaluator runs
- THEN the level is `none`

### Requirement: Both sources unavailable yields no risk decision

When both SENAMHI and Open-Meteo are `unavailable`, the evaluator MUST return level `none` and MUST NOT infer risk from stale or absent data.

#### Scenario: Dual outage
- GIVEN both SENAMHI and Open-Meteo are unavailable
- WHEN the evaluator runs
- THEN the level is `none`

### Requirement: Thresholds and coordinates come from configuration

The evaluator MUST read thresholds and coordinates from `ConfigRepository` at evaluation time and MUST NOT hard-code them.

#### Scenario: Threshold source
- GIVEN a `ConfigRepository` fake with a documented threshold set
- WHEN the evaluator runs
- THEN the level produced reflects the values returned by that fake, not literals in the evaluator code
