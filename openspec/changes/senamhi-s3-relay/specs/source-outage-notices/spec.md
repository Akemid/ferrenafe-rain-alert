# Delta for Source Outage Notices

## ADDED Requirements

### Requirement: Operator notice names the relay cause

When SENAMHI is unavailable because of the relay, the operator notice MUST name the relay reason (`STALE_RELAY`, `RELAY_MISSING` or `RELAY_UNREADABLE`) so it reads as distinct from "SENAMHI down", MUST include the object `VersionId` when known, and MUST include the producer skew when flagged. The notice detail MUST stay within the existing 160-character sanitized limit. The `VersionId` MUST also appear in the cycle log.

#### Scenario: Stale relay notice
- GIVEN a `STALE_RELAY` result for an object with a known `VersionId`
- WHEN the operator notice is composed
- THEN it states the relay is stale (not that SENAMHI is down) and contains the `VersionId`

#### Scenario: Missing relay notice
- GIVEN a `RELAY_MISSING` result
- WHEN the operator notice is composed
- THEN it states the relay object is missing and contains no `VersionId`

#### Scenario: Unreadable relay notice
- GIVEN a `RELAY_UNREADABLE` result caused by `AccessDenied`
- WHEN the operator notice is composed
- THEN it states the relay is unreadable, names the error code, and does not state that the object is missing

#### Scenario: Direct SENAMHI failure unchanged
- GIVEN a direct-scrape `timeout`
- WHEN the operator notice is composed
- THEN it reads as before and mentions no relay

### Requirement: Resident text is unchanged by the relay

The resident-facing Spanish degraded-mode text ("SENAMHI no estuvo disponible...") MUST be byte-identical for relay-caused and direct-caused unavailability, and MUST NOT expose relay reasons, `VersionId` or skew.

#### Scenario: Relay-stale resident message
- GIVEN SENAMHI is unavailable as `STALE_RELAY` and a forecast-only alert is sent
- WHEN the resident message is composed
- THEN it equals the message composed for a direct `timeout`, byte for byte

## MODIFIED Requirements

### Requirement: Single-source outage notice

When exactly one of SENAMHI or Open-Meteo is `unavailable`, the domain MUST emit exactly one operator technical notice identifying the affected source and, for SENAMHI, whether the cause is the relay (`STALE_RELAY`, `RELAY_MISSING` or `RELAY_UNREADABLE`) or SENAMHI itself, deduplicated for the duration of that source's outage.
(Previously: the notice identified only the affected source.)

#### Scenario: SENAMHI down triggers one notice
- GIVEN SENAMHI is unavailable and no notice has been sent for this outage
- WHEN the cycle runs
- THEN exactly one operator notice naming SENAMHI is emitted

#### Scenario: No duplicate notice while still down
- GIVEN a SENAMHI-unavailable notice was already sent for the ongoing outage
- WHEN the cycle runs again with SENAMHI still unavailable
- THEN no additional notice is emitted

#### Scenario: Relay reason alone does not re-notify
- GIVEN a notice was sent for `STALE_RELAY` and the next cycle reports `RELAY_MISSING`
- WHEN the cycle runs
- THEN no additional notice is emitted while the outage is ongoing
