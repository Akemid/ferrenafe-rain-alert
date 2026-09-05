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

### Requirement: A highlands-only official warning is an early signal

*Added 2026-09-04, owner-approved, from the slice-3 fresh-context review.*

The Río La Leche rises at 4 230 m on Mount Choicopico, **inside Ferreñafe
province**, and flows west through Ferreñafe and Lambayeque provinces. SENAMHI
measures its critical level at the Puchaca station in Incahuasi — a sierra
district of Ferreñafe province — and attributes rises there to intense rain in
the basin headwaters. Riverside defence works protect Pítipo and Incahuasi in
this province, plus Pacora, Íllimo and Jayanca. Sierra rain is therefore
upstream of the city with hours of lead time, and it is the mechanism behind
the 2017 flooding of Ferreñafe.

A rainfall warning covering **only** the highlands (`EN LA SIERRA`, `EN LA
SIERRA NORTE Y CENTRO`) MUST therefore be evaluated, and MAY contribute to
`prepare` through the combined rule. It MUST NOT raise `imminent` on its own,
at any warning level: Ferreñafe city is coastal, so sierra rain reaches it by
river routing rather than by falling overhead, which warrants preparation
rather than a claim that flooding is already under way.

A warning covering the coast, or the coast and the highlands together, or whose
zone is unstated, keeps full weight including `imminent`.

#### Scenario: Highlands-only red alone does not raise imminent
- GIVEN SENAMHI reports a red warning titled `PRECIPITACIONES EN LA SIERRA` and no other warning
- WHEN the evaluator runs
- THEN the level is not `imminent`

#### Scenario: Highlands-only orange plus a qualifying forecast prepares
- GIVEN SENAMHI reports an orange highlands-only precipitation warning AND Open-Meteo forecasts 10 mm in 48 h at 60% probability
- WHEN the evaluator runs
- THEN the level is `prepare`
- AND the reasons include the official warning

#### Scenario: A coastal orange warning still raises imminent
- GIVEN SENAMHI reports an orange warning covering the coast
- WHEN the evaluator runs
- THEN the level is `imminent`

#### Scenario: A coast-and-highlands warning behaves as coastal
- GIVEN SENAMHI reports an orange warning titled `PRECIPITACIONES EN LA COSTA NORTE Y SIERRA`
- WHEN the evaluator runs
- THEN the level is `imminent`

#### Scenario: One barred warning does not bar the others
- GIVEN SENAMHI reports a highlands-only red warning AND a coastal orange warning
- WHEN the evaluator runs
- THEN the level is `imminent`
- AND the reasons name the coastal warning

### Requirement: Forecast-only prepare when SENAMHI is available but silent

When SENAMHI reports `available` with no warning at or above the `prepare` level, the evaluator MUST still raise `prepare` if Open-Meteo alone meets the accumulation threshold with a max probability at or above the degraded probability threshold (70%). A healthy official source that is merely silent MUST NOT make the system less sensitive than an unavailable one, because breaking the scraper would otherwise increase the chance of an alert.

The reasons MUST state that no official warning is in force and that the assessment rests on the forecast alone.

#### Scenario: Forecast-only prepare fires at the stricter threshold
- GIVEN SENAMHI is available and reports no qualifying warning AND Open-Meteo forecasts 24 mm in 48 h at 95% probability
- WHEN the evaluator runs
- THEN the level is `prepare`
- AND the reasons state that no official warning is in force

#### Scenario: Forecast-only prepare respects the 70% floor
- GIVEN SENAMHI is available and reports no qualifying warning AND Open-Meteo forecasts 24 mm in 48 h at 65% probability
- WHEN the evaluator runs
- THEN the level is `none`

#### Scenario: A healthy silent source is never less sensitive than an outage
- GIVEN identical Open-Meteo forecast data
- WHEN the evaluator runs once with SENAMHI available and silent, and once with SENAMHI unavailable
- THEN the level derived with the healthy source is at least as severe as the level derived during the outage

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
