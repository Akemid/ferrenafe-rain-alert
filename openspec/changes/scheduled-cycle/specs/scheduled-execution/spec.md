# Scheduled Execution Specification

## Purpose

The Lambda entry point contract for the six-hourly cycle: the timeout
invariant that keeps the template fallback reachable, the sequence shared
with the CLI path, and what a failed invocation must and must not do.

## Requirements

### Requirement: Lambda timeout strictly exceeds the agent deadline plus cycle overhead

The deployed Lambda function timeout MUST be strictly greater than the sum
of (a) the agent invocation deadline sourced from
`agentcore_invoker.READ_TIMEOUT` and `CONNECT_TIMEOUT`, and (b) the
worst-case duration of every other step in one cycle (SENAMHI scrape,
Open-Meteo fetch, SSM read, DynamoDB read/write, S3 put). This relation MUST
be asserted against the Python constants themselves, never a copied
integer, so a change to either constant is caught rather than silently
invalidating the deployed timeout.

#### Scenario: Deployed timeout exceeds the invoker's deadline
- GIVEN the CDK stack's Lambda timeout value and `agentcore_invoker.READ_TIMEOUT` + `CONNECT_TIMEOUT`
- WHEN the relation is asserted in a test
- THEN the deployed timeout is strictly greater than that sum plus a documented non-agent overhead margin

#### Scenario: A raised `READ_TIMEOUT` invalidates a stale assertion
- GIVEN `agentcore_invoker.READ_TIMEOUT` increases in source
- WHEN the same test runs against the unchanged deployed timeout
- THEN the test fails, because it reads the constant rather than a copy of its prior value

### Requirement: Both entry points execute the same post-dependency sequence

`cli.py:main` and `lambda_handler.handler` MUST both delegate to one shared
`run_once(deps, publisher) -> CycleResult` that loads config, executes
`RunAlertCycle`, and publishes the snapshot inside the same
failure-isolating guard that exists in `cli.py` today. Neither entry point
may reimplement this sequence independently.

#### Scenario: Identical `CycleResult` for identical dependencies
- GIVEN the same `CycleDependencies`-shaped graph and the same clock
- WHEN `run_once` is invoked directly, and separately via each entry point's wiring
- THEN both produce the same `CycleResult` and the same `AlertRecord` on an authorized send

#### Scenario: A snapshot-publish failure does not cost the alert, on either path
- GIVEN an authorized send that already called `Notifier.send_alert`
- WHEN `publisher.publish` then raises
- THEN `run_once` returns normally, the alert already went out, and the failure is reported but does not propagate

### Requirement: A cloud dependency construction failure fails the invocation visibly

If `build_cloud_deps` cannot construct the dependency graph (a missing or
unparseable SSM parameter, a missing agent ARN), `lambda_handler.handler`
MUST let that exception propagate rather than catching it and reporting a
fabricated successful cycle. This failure class is distinct from a
snapshot-publish failure: construction failures MUST be loud, publish
failures MUST be absorbed.

#### Scenario: Missing required parameter fails the invocation
- GIVEN an SSM parameter required by `build_cloud_deps` is absent
- WHEN the handler is invoked
- THEN the exception propagates out of the handler and no snapshot is published

#### Scenario: A construction failure is never reported as a completed cycle
- GIVEN `build_cloud_deps` raises
- WHEN the invocation ends
- THEN no `CycleResult`, no `as_json` log document, and no snapshot exist for that invocation

### Requirement: The handler logs the same machine-readable document the CLI's `--json` mode emits

`lambda_handler.handler` MUST log one `as_json`-shaped document per
completed invocation, and MUST NOT raise for any completed cycle outcome
(`none`, degraded, deduplicated), mirroring the CLI's exit-code-0 posture
for a cycle that ran.

#### Scenario: A deduplicated cycle still logs a document
- GIVEN a standing `prepare` already recorded for the current window
- WHEN the handler runs a second time within that window
- THEN it logs a document with `sent: false` and does not raise

#### Scenario: Dedup correctness does not depend on the six-hour interval
- GIVEN the handler is invoked at an interval other than six hours (e.g. a manual re-run)
- WHEN it computes the dedup query window
- THEN the window is derived from the actual cycle clock (`now`), not an assumed period
