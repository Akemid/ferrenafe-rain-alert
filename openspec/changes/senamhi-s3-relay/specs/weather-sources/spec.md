# Delta for Weather Sources

## ADDED Requirements

### Requirement: SENAMHI acquisition may come from the relay

When `RAIN_ALERT_RELAY_BUCKET` is set, the cloud wiring MUST supply a `WarningProvider` that reads the relay object, applies the freshness rule, and parses it with `parse_warnings_page`. `Available.fetched_at` MUST equal the object's `LastModified`. When unset, the direct scraper MUST be used. `build_local_deps` MUST be unchanged.

#### Scenario: Fresh object is available
- GIVEN the variable is set and a fresh object holds a well-formed page
- WHEN the warnings are fetched
- THEN the result is `Available` parsed by `parse_warnings_page`
- AND `fetched_at` equals `LastModified`

#### Scenario: Variable unset
- GIVEN `RAIN_ALERT_RELAY_BUCKET` is unset
- WHEN cloud dependencies are built
- THEN the direct scraper is the warning provider

#### Scenario: Local dependencies unchanged
- GIVEN `build_local_deps`
- WHEN it is called with any environment
- THEN it returns the direct scraper, as before

### Requirement: Stale or unreadable relay is never a calm

The relay provider MUST return `Unavailable(STALE_RELAY)` for age >= 3 h, `Unavailable(RELAY_MISSING)` for a missing key, and `Unavailable(RELAY_UNREADABLE)` for access denied, throttling or any other S3/client error. A configured relay MUST NOT fall back to direct scraping in any of these cases.

#### Scenario: Stale object
- GIVEN an object 4 h old holding a calm page
- WHEN the warnings are fetched
- THEN the result is `Unavailable(STALE_RELAY)`, not `Available` with an empty list

#### Scenario: Missing object, no fallback
- GIVEN the variable is set and the key does not exist
- WHEN the warnings are fetched
- THEN the result is `Unavailable(RELAY_MISSING)` and no direct HTTP fetch is attempted

#### Scenario: Access denied, no fallback
- GIVEN the variable is set and S3 denies access
- WHEN the warnings are fetched
- THEN the result is `Unavailable(RELAY_UNREADABLE)` and no direct HTTP fetch is attempted

## MODIFIED Requirements

### Requirement: SENAMHI warning acquisition

`WarningProvider` MUST return normalized current warnings for Lambayeque (level, window, title, source ID), tagged `available` (with a possibly empty list), or MUST report `unavailable` when the page cannot be parsed correctly, when the relay copy is stale (`STALE_RELAY`), or when the relay object is missing (`RELAY_MISSING`), or when it is unreadable (`RELAY_UNREADABLE`).
(Previously: unavailable only when the page could not be parsed.)

#### Scenario: Healthy page with no current warnings
- GIVEN a valid SENAMHI fixture whose parsed table is structurally well-formed and has no currently active warnings
- WHEN the warnings are fetched
- THEN the result status is `available` with an empty warning list

#### Scenario: Relay copy of a healthy page
- GIVEN a fresh relay object containing the same fixture
- WHEN the warnings are fetched
- THEN the result is identical to the direct case except that `fetched_at` is `LastModified`
