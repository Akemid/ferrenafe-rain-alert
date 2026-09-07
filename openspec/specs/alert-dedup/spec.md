# Alert Dedup Specification

## Purpose

`AlertPolicy` decides whether a `RiskAssessment` is sent. Reference: design spec section 6.1.

## Requirements

### Requirement: Send on new level for the window

AlertPolicy MUST authorize sending when no alert has been sent yet for the assessment's level and window.

#### Scenario: First alert for a window
- GIVEN no prior alert recorded for the current window
- WHEN AlertPolicy evaluates a `prepare` assessment
- THEN AlertPolicy authorizes sending

### Requirement: Send on escalation

AlertPolicy MUST authorize sending when the new level is higher than the last sent level for an overlapping window.

#### Scenario: Prepare escalates to imminent
- GIVEN a `prepare` alert was already sent for the current window
- WHEN AlertPolicy evaluates a new `imminent` assessment for the same window
- THEN AlertPolicy authorizes sending

### Requirement: Never re-send the same level in the same window

AlertPolicy MUST NOT authorize sending when the assessed level equals the last sent level for the same window.

#### Scenario: Repeat level
- GIVEN a `prepare` alert was already sent for the current window
- WHEN AlertPolicy evaluates another `prepare` assessment for the same window
- THEN AlertPolicy does not authorize sending

### Requirement: De-escalation sends nothing

AlertPolicy MUST NOT authorize sending when the new level is lower than the last sent level.

#### Scenario: Imminent de-escalates to prepare
- GIVEN an `imminent` alert was already sent for the current window
- WHEN AlertPolicy evaluates a new `prepare` assessment for the same window
- THEN AlertPolicy does not authorize sending

#### Scenario: De-escalation to none
- GIVEN a `prepare` alert was already sent for the current window
- WHEN AlertPolicy evaluates a new `none` assessment for the same window
- THEN AlertPolicy does not authorize sending

### Requirement: Dedup state is read from AlertRepository

AlertPolicy MUST query `AlertRepository` for the last sent level and window before deciding, and MUST NOT hold its own persistent state.

#### Scenario: Query before decision
- GIVEN an `AlertRepository` fake seeded with a prior sent alert
- WHEN AlertPolicy evaluates a new assessment
- THEN AlertPolicy's decision is based on the value returned by that repository query
