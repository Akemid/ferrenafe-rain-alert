# Cloud Configuration Specification

## Purpose

The SSM-backed `ConfigRepository`: which `AlertConfig` fields are
operator-editable in Parameter Store, which stay in code, provenance
handling for coordinates, and failure posture on a bad or missing
parameter.

## Requirements

### Requirement: SSM-backed repository reads only the operator-editable subset

`SsmConfigRepository.load()` MUST read from Parameter Store: `coordinates`
(with `coordinates_source`), the seven `RiskThresholds` fields, `checklist`,
`active_channel`, `composer`, `forecast_hours`, and `dedup_lookback_hours`.
It MUST NOT read `city`, `city_slug`, `region`, or `timezone` from SSM —
these remain code constants, identical in shape to
`StaticConfigRepository`'s split.

#### Scenario: Full load from valid parameters
- GIVEN every operator-editable SSM parameter is set to a valid value
- WHEN `.load()` is called
- THEN the returned `AlertConfig` carries those values, and `city`, `city_slug`, `region`, `timezone` from code constants

#### Scenario: `city_slug` is unaffected by any SSM value
- GIVEN no SSM parameter path exists for `city_slug` (by design)
- WHEN `.load()` runs
- THEN `city_slug` equals the code constant regardless of any other configuration change

### Requirement: Coordinates and their provenance travel together

`coordinates_source` MUST be read from the same configuration path as
`coordinates` and MUST NOT be inferred (e.g. from equality with a known
default). An operator-supplied coordinate pair without an explicit
`coordinates_source` MUST fail to load rather than default to
`public_reference`.

#### Scenario: Operator-supplied coordinates without provenance raises
- GIVEN `coordinates` is set in SSM to a non-default point and `coordinates_source` is absent
- WHEN `.load()` runs
- THEN it raises rather than defaulting `coordinates_source` to `public_reference`

#### Scenario: Provenance is preserved end to end
- GIVEN `coordinates_source` is set to `operator_supplied` in SSM
- WHEN `.load()` runs and the resulting `AlertConfig` reaches the published snapshot
- THEN the snapshot's coordinates-source field reads `operator_supplied`, matching what SSM held

### Requirement: A missing or unparseable parameter raises

`SsmConfigRepository.load()` MUST raise when a required parameter is
absent, empty, or fails to parse (numeric, enum), matching
`StaticConfigRepository`'s existing strictness on `RAIN_ALERT_LAT`/`LON`
and `RAIN_ALERT_COMPOSER`. It MUST NOT fall back to a built-in default for
any operator-editable field.

#### Scenario: Missing threshold parameter raises before returning config
- GIVEN one of the seven threshold parameters is absent from SSM
- WHEN `.load()` is called
- THEN it raises and no `AlertConfig` is returned

#### Scenario: An unrecognized `active_channel` value raises
- GIVEN `active_channel` in SSM holds a value other than `"console"`
- WHEN `.load()` is called
- THEN it raises, rather than silently accepting a channel nothing can deliver through

### Requirement: `composer` is SSM-backed and requires no deploy to change

`composer` MUST be sourced from SSM Parameter Store, so the rollback
level-2 switch (template ↔ agent) takes effect on the next scheduled
invocation with no redeploy.

#### Scenario: Flipping the composer takes effect without a deploy
- GIVEN the SSM `composer` parameter is changed from `"agent"` to `"template"` between two invocations
- WHEN the next invocation calls `.load()`
- THEN the returned `AlertConfig.composer` is `"template"`, with no code or infrastructure change deployed
