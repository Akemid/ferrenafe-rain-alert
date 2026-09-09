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

### Requirement: An unparseable row in force is a structural failure

*Added 2026-09-04, from the slice-3 fresh-context review (finding C2).*

A share-based structural guard cannot defend the row that decides the cycle.
Breakage confined to the single row marked `(vigente)` is 1 of roughly 789
rows — 0.13% — so a "more than half the rows unparseable" guard passes it, and
the adapter reports `available` with an empty warning list while a red aviso is
in force.

That outcome is worse than a declared outage. With `senamhi_status` reported as
`available`, `risk-evaluation` routes to the forecast-only branch at the
stricter degraded probability threshold, so a broken parse leaves the system
**less** sensitive than a source known to be down, and no operator notice is
emitted.

The adapter MUST therefore report `unavailable` with reason
`structure_unrecognized` when a row carrying the in-force marker cannot be
parsed — a missing cell, an unparseable date, or an unrecognized level token.
Anomalies on rows NOT in force MUST remain skippable, because a historical row
cannot change the verdict.

#### Scenario: An unparseable date on the row in force
- GIVEN a page whose row marked `(vigente)` carries a start date the adapter cannot parse
- WHEN the warnings are fetched
- THEN the result status is `unavailable` with reason `structure_unrecognized`

#### Scenario: A missing cell on the row in force
- GIVEN a page whose row marked `(vigente)` has fewer cells than the header defines
- WHEN the warnings are fetched
- THEN the result status is `unavailable` with reason `structure_unrecognized`

#### Scenario: An unparseable historical row does not condemn the page
- GIVEN a page whose row marked `(vigente)` parses cleanly and one historical row does not
- WHEN the warnings are fetched
- THEN the result status is `available` and the warning in force is returned
- AND the result carries an operator-facing note that a row was unparseable and skipped

### Requirement: Only flood-relevant official warnings may drive an alert

*Added 2026-09-04, owner-approved, after the live page was inspected during
apply. See design spec section 5.1.*

The SENAMHI Lambayeque warnings page is a **multi-hazard** feed, not a rainfall
feed: of the 788 rows live on 2026-09-04, roughly 233 were wind, 131 daytime or
nighttime temperature and only 137 precipitation. The warning in force that day
was a RED daytime-temperature warning, which the evaluator's first branch would
have read as imminent flooding.

`WarningProvider` MUST classify each parsed row's meteorological phenomenon and
geographic zone, and MUST exclude from the returned tuple any warning whose
**phenomenon cannot cause flooding**. Wind (`INCREMENTO DE VIENTO`), heat
(`INCREMENTO DE [LA] TEMPERATURA`), cold (`DESCENSO DE [LA] TEMPERATURA`,
`FRIAJE`), snow (`NEVADA`) and hail (`GRANIZO`) warnings MUST NOT reach the
evaluator. Rainfall warnings — `PRECIPITACIONES`, `LLUVIA`, `LLOVIZNA`,
`GARÚA` — MUST reach it.

Geography MUST NOT exclude a rainfall warning from the returned tuple. It
constrains only how severe a level that warning may drive, which is specified
in `risk-evaluation` ("A highlands-only official warning is an early signal").

Classification MUST match on normalized text (accent-insensitive,
case-insensitive, **word-boundary**) rather than exact strings or substrings,
because the titles vary: `PRECIPITACIONES EN LA COSTA NORTE Y SIERRA` and
`PRECIPITACIONES EN COSTA NORTE Y SIERRA` both occur, `LLUVIA ...` titles and
`(EXTENSIÓN DEL AVISO n)` suffixes occur, and the **same aviso family is
published both with and without the article** (`INCREMENTO DE TEMPERATURA
DIURNA ...` and `INCREMENTO DE LA TEMPERATURA DIURNA ...`; 56 rows across 9 of
the 12 regional pages used the article form on 2026-09-04).

Every hazard family SENAMHI publishes MUST have a classification. A family
left unclassified is not a neutral gap: unknown is treated as flood-relevant,
so an unclassified family reaches the evaluator as though it were rain.

A warning whose phenomenon or zone CANNOT be classified MUST be treated as
relevant, and an unclassifiable zone MUST be treated as reaching the coast.
Missing a real flood warning is worse than one extra alert, so the unknown
case MUST fail towards alerting. This is deliberately asymmetric: a highlands
classification is a positive statement that the coast is not covered, whereas
an unknown zone only means the title did not say.

Every exclusion MUST be explainable **to the operator**, not only inside the
adapter, so that a filtered page is distinguishable from a broken scraper.

#### Scenario: A red heat warning in force does not raise the level
- GIVEN the page's only warning in force is `INCREMENTO DE TEMPERATURA DIURNA EN LA COSTA Y SIERRA` at level `ROJO`
- WHEN the warnings are fetched
- THEN the result status is `available` with an empty warning list
- AND the evaluator therefore does not raise `imminent` from an official warning

#### Scenario: A coastal precipitation warning in force is returned
- GIVEN the page has a warning in force whose title names precipitation and includes the coast
- WHEN the warnings are fetched
- THEN the result status is `available` and the warning is present with its normalized level and window

#### Scenario: A highlands-only precipitation warning is returned as an early signal
- GIVEN the page has a warning in force titled `PRECIPITACIONES EN LA SIERRA`
- WHEN the warnings are fetched
- THEN the warning is present in the returned warnings, classified as highlands
- AND `risk-evaluation` decides how far it may take the level

#### Scenario: The article variant of a temperature aviso is still excluded
- GIVEN the page has a warning in force titled `INCREMENTO DE LA TEMPERATURA DIURNA EN LA COSTA` at level `ROJO`
- WHEN the warnings are fetched
- THEN the result status is `available` with an empty warning list

#### Scenario: Wind, temperature, snow and hail warnings never reach the evaluator
- GIVEN a page whose warnings in force are wind, daytime-temperature, nighttime-temperature, `NEVADA`, `GRANIZO` and `FRIAJE` warnings
- WHEN the warnings are fetched
- THEN none of them appear in the returned warnings

#### Scenario: Drizzle is precipitation
- GIVEN a warning in force titled `LLOVIZNA EN LA COSTA`
- WHEN the warnings are fetched
- THEN the warning is present in the returned warnings

#### Scenario: An unclassifiable precipitation zone is kept
- GIVEN a warning in force titled `PRECIPITACIONES INTENSAS`, naming neither the coast nor the highlands
- WHEN the warnings are fetched
- THEN the warning is present in the returned warnings

#### Scenario: Exclusions are auditable by the operator
- GIVEN a page whose rows include excluded hazards
- WHEN the warnings are fetched
- THEN the `available` result carries, for each excluded warning, an operator-facing note naming the warning and the reason it was excluded
- AND the CLI prints those notes, so `level none` from a filtered page is distinguishable from `level none` from an idle page

### Requirement: Only warnings currently in force may drive an alert

The page lists warning history since 2024, so the adapter MUST return only the
warnings currently in force. The page marks these with a `(vigente)` token in
the warning-number cell and links them to a different detail page than
historical rows.

The marker MUST NOT be trusted on its own: the adapter MUST also verify that
`now` falls inside the row's start/end dates, and MUST exclude a row that fails
either check. A stale marker left on an expired row would otherwise alert the
community about rain that has already passed.

#### Scenario: A marked row whose window has passed is excluded
- GIVEN a row marked `(vigente)` whose end date is before `now`
- WHEN the warnings are fetched
- THEN the row is not present in the returned warnings

#### Scenario: An unmarked row inside its window is excluded
- GIVEN a historical row with no `(vigente)` marker whose start/end dates happen to contain `now`
- WHEN the warnings are fetched
- THEN the row is not present in the returned warnings

#### Scenario: Historical rows still prove the parse worked
- GIVEN a well-formed page whose rows are all historical
- WHEN the warnings are fetched
- THEN the result status is `available` with an empty warning list, not `unavailable`

### Requirement: Coordinates come from configuration

`ForecastProvider` MUST read the target latitude/longitude from `ConfigRepository` rather than a hard-coded value. The default coordinates in the local configuration MUST be sourced, with their references cited in the code, and MUST be pinned by a test.

The configuration MUST also carry how the coordinates were obtained, and both the operator output and the machine-readable document MUST show it. A value asserting a reading taken on the ground MUST NOT be produced by any code path until such a reading exists.

*Amended 2026-09-07 (sourced Ferrenafe coordinates).* The requirement previously said the local fake "MUST use a documented placeholder, not an unverified 'real' coordinate", and its scenario required the coordinates to be "marked, in code or comments, as a documented placeholder pending confirmation". The coordinates are now sourced, `6°38'10"S 79°47'23"W`, the centre of the city, agreed by two independent public references cited in `adapters/local/static_config_repository.py`. So `AlertConfig.coordinates_are_placeholder` and the CLI's `(PLACEHOLDER — pending confirmation)` marker are retired: a flag that can never be true again is dead configuration.

Retiring the flag first left no visible signal at all, which a security review judged wrong for a system whose job is warning people. Provenance therefore returns as `CoordinatesSource`, an enum rather than a boolean, because the useful question is how a point was obtained and that has three answers with different trust: a published reference, an operator who supplied it, and a reading taken on the ground. An operator during an event, and the deployment change that will write these into cloud configuration, both need the distinction.

#### Scenario: Default coordinates are sourced and pinned
- GIVEN the local `ConfigRepository`
- WHEN its default coordinates are inspected
- THEN they are the centre of the city of Ferreñafe, cited in the code to public references, and pinned by a test that fails if the value drifts

#### Scenario: Provenance travels with the coordinates
- GIVEN a cycle run against the default configuration
- WHEN the operator output and the machine-readable document are read
- THEN both state that the coordinates came from a published reference

#### Scenario: An operator-supplied point is not reported as published
- GIVEN both coordinate override variables set to a valid pair
- WHEN the configuration is loaded
- THEN the coordinates are the operator's, and their source says the operator supplied them

#### Scenario: No code path claims a surveyed point
- GIVEN any configuration this system can produce today
- WHEN its coordinate source is inspected
- THEN it is never the value reserved for a reading taken on the ground
