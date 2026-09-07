# Local Alert CLI Specification

## Purpose

A local entrypoint that runs the real cycle for calibration, per design spec section 10.1, without ever sending to a real channel.

## Requirements

### Requirement: CLI runs the cycle against real adapters

The CLI MUST invoke `RunAlertCycle` wired with the real Open-Meteo client and the real SENAMHI scraper adapter, using coordinates from `ConfigRepository`.

#### Scenario: Real-source run
- GIVEN the CLI is executed with no arguments beyond configuration
- WHEN it runs
- THEN it invokes `RunAlertCycle` with real `ForecastProvider` and `WarningProvider` adapters

### Requirement: CLI never sends externally

The CLI MUST use a console `Notifier` implementation that only prints to standard output and MUST NOT deliver to any external channel.

#### Scenario: Console-only delivery
- GIVEN an authorized send during a CLI run
- WHEN the cycle notifies
- THEN the message is printed to standard output and no network call to a notification channel occurs

### Requirement: CLI output is informative for calibration

The CLI output MUST include the resulting risk level, the evaluator's reasons, and the exact message text that would have been sent, whether or not a send was authorized.

#### Scenario: Full output on every run
- GIVEN any cycle outcome (sent or deduplicated)
- WHEN the CLI finishes
- THEN it prints the level, the reasons, and the composed message text
