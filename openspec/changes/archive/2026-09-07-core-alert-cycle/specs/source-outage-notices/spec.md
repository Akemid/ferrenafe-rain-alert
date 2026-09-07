# Source Outage Notices Specification

## Purpose

Operator-facing technical notices for source unavailability, including the dual-outage rule introduced by this change's proposal (not in the base design spec). Notices are delivered via the `Notifier` port using a notice type distinct from community alerts; dedup state lives in `AlertRepository`.

## Requirements

### Requirement: Single-source outage notice

When exactly one of SENAMHI or Open-Meteo is `unavailable`, the domain MUST emit exactly one operator technical notice identifying the affected source, deduplicated for the duration of that source's outage.

#### Scenario: SENAMHI down triggers one notice
- GIVEN SENAMHI is unavailable and no notice has been sent for this outage
- WHEN the cycle runs
- THEN exactly one operator notice naming SENAMHI is emitted

#### Scenario: No duplicate notice while still down
- GIVEN a SENAMHI-unavailable notice was already sent for the ongoing outage
- WHEN the cycle runs again with SENAMHI still unavailable
- THEN no additional notice is emitted

### Requirement: Dual-source outage suppresses community alerts

When both sources are `unavailable`, the domain MUST NOT send a community alert regardless of the underlying `RiskAssessment`, and MUST emit exactly one deduplicated operator notice for the outage duration.

#### Scenario: Three consecutive dual-outage cycles
- GIVEN both sources are unavailable across three consecutive cycles
- WHEN each cycle runs
- THEN the level is `none`, no community alert is sent in any cycle, AND exactly one operator notice is emitted across all three cycles

### Requirement: Recovery notice on source return

When a source that was part of an active outage becomes `available` again, the domain MUST emit exactly one recovery notice for that outage and MUST clear the outage's dedup state.

#### Scenario: Recovery after dual outage
- GIVEN an operator notice was already sent for an active dual-source outage
- WHEN the next cycle runs with at least one source available again
- THEN exactly one recovery notice is emitted

#### Scenario: No recovery notice without a prior outage
- GIVEN no outage notice is currently active
- WHEN a cycle runs with both sources available
- THEN no recovery notice is emitted

### Requirement: Notices use a distinct type and dedup store

Operator notices MUST be delivered through the `Notifier` port using a notice type distinct from community alerts, and their dedup state MUST be tracked via `AlertRepository`.

#### Scenario: Distinct notice type
- GIVEN an active outage
- WHEN a notice is emitted
- THEN it is delivered via `Notifier` tagged with an operator-notice type distinct from a community alert
