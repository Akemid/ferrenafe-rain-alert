# Weather Sources Specification

## Purpose

Acquisition of the two external signals — Open-Meteo forecast and SENAMHI warnings — with explicit per-source availability semantics. Reference: design spec sections 5, 6.2, and 6.4.

## Requirements

### Requirement: Open-Meteo forecast acquisition

`ForecastProvider` MUST return 48 hours of hourly precipitation (mm) and probability (%) for the coordinates read from `ConfigRepository`, tagged `available`, or MUST report `unavailable` when the request cannot be completed.

#### Scenario: Successful fetch
- GIVEN Open-Meteo responds with valid hourly data
- WHEN the forecast is fetched
- THEN the result status is `available` and contains 48 hours of precipitation and probability values

#### Scenario: Network failure
- GIVEN the Open-Meteo request fails or times out
- WHEN the forecast is fetched
- THEN the result status is `unavailable`

### Requirement: SENAMHI warning acquisition

`WarningProvider` MUST return normalized current warnings for Lambayeque (level, window, title, source ID), tagged `available` (with a possibly empty list), or MUST report `unavailable` when the page cannot be parsed correctly.

#### Scenario: Healthy page with no current warnings
- GIVEN a valid SENAMHI fixture whose parsed table is structurally well-formed and has no currently active warnings
- WHEN the warnings are fetched
- THEN the result status is `available` with an empty warning list

### Requirement: Scraper breakage yields unavailable, never a false calm

Because the SENAMHI page always lists warning history since 2024, the adapter MUST report `unavailable` — never `available` with zero rows — when the expected table structure is not recognized or zero rows are extracted overall.

#### Scenario: Altered HTML structure
- GIVEN a SENAMHI fixture with an altered/unrecognized table structure
- WHEN the warnings are fetched
- THEN the result status is `unavailable`

#### Scenario: Zero rows extracted from a page that should have history
- GIVEN a SENAMHI fixture that yields zero extracted rows
- WHEN the warnings are fetched
- THEN the result status is `unavailable`, not `available` with an empty list

### Requirement: Coordinates come from configuration

`ForecastProvider` MUST read the target latitude/longitude from `ConfigRepository` rather than a hard-coded value; the local fake MUST use a documented placeholder, not an unverified "real" coordinate.

#### Scenario: Placeholder coordinates documented
- GIVEN the local `ConfigRepository` fake
- WHEN its coordinates are inspected
- THEN they are marked, in code or comments, as a documented placeholder pending confirmation (design spec section 12)
