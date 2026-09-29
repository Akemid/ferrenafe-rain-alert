# Alert Persistence Specification

## Purpose

The DynamoDB-backed `AlertRepository`: durable dedup and outage state
across cold invocations, key design, read consistency, and bounded growth.

## Requirements

### Requirement: Dedup state survives across separate, cold invocations

`DynamoDbAlertRepository` MUST persist alert and outage records durably
outside the Lambda execution environment, such that a cold invocation
observes state written by an earlier, separate invocation. It MUST reuse
`adapters/serialization.py`'s existing functions with no translation layer,
so the DynamoDB item and the local JSON document stay shape-identical.

#### Scenario: A standing `prepare` sends once across two invocations
- GIVEN invocation 1 records an alert for a level and window via `record_alert`
- WHEN invocation 2 runs in a fresh execution environment, for the same `city_slug` and an overlapping window
- THEN `alerts_with_window_start_between` returns invocation 1's record and the cycle does not re-send

#### Scenario: Records round-trip through the same serialization the local adapter uses
- GIVEN a fully populated `AlertRecord` (including `reasons` and `composer`)
- WHEN it is written by `DynamoDbAlertRepository` and read back
- THEN the read record equals the one written, via `alert_record_to_dict`/`alert_record_from_dict`

### Requirement: `city_slug` is the partition key and is not operator-editable

The table's partition key MUST be `city_slug`. `city_slug` MUST remain a
code constant, never SSM-sourced (`cloud-configuration`), because changing
it strands existing dedup history under the old key and re-alerts a
community already told.

#### Scenario: An SSM configuration change never changes the partition key
- GIVEN any operator-editable `AlertConfig` field changes in SSM
- WHEN the next cycle queries or writes `alerts-sent`
- THEN it uses the same `city_slug` partition key as every prior cycle

#### Scenario: A key change would strand history (documented hazard, not a supported path)
- GIVEN `city_slug` were changed by any means
- WHEN a subsequent cycle queries dedup state
- THEN prior records under the old key become unreachable — accepted as a phase-1 hazard, never as a silent no-op

### Requirement: The dedup query is ordered and strongly consistent

`alerts_with_window_start_between` MUST return matching records ascending
by window start and MUST use a strongly consistent read, not an eventually
consistent one.

#### Scenario: Ascending order across the query bounds
- GIVEN two or more alert records with window starts inside `[earliest_start, latest_start]`
- WHEN the query runs
- THEN results are ordered ascending by window start

#### Scenario: A record written moments earlier in the same invocation is visible
- GIVEN `record_alert` completes for a window inside the query bounds
- WHEN `alerts_with_window_start_between` is called immediately after, in the same or a retried invocation
- THEN the just-written record is present in the result

### Requirement: Alert records carry a TTL well above the dedup lookback

Each alert record MUST carry a TTL attribute set to 30 days from `sent_at`
— well above the 72-hour `dedup_lookback_hours` window, long enough to
serve the page's recent-alerts history, and short enough that the table
does not grow unbounded.

#### Scenario: TTL exceeds the dedup window
- GIVEN a record's `sent_at`
- WHEN its TTL attribute is computed
- THEN the TTL is `sent_at` + 30 days, strictly greater than `sent_at` + `dedup_lookback_hours`

#### Scenario: A record within the dedup window is never eligible for deletion during that window
- GIVEN a record whose window start is within the current `dedup_lookback_hours` lookback
- WHEN a dedup query runs before the TTL elapses
- THEN the record is still present and returned
