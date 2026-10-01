# Delta for Local Alert CLI

## ADDED Requirements

### Requirement: CLI behavior is unchanged after extracting the shared runner

Extracting `run_once(deps, publisher) -> CycleResult` from `cli.py:main`
MUST NOT change any CLI-observable behavior: the same exit codes, the same
stdout/stderr content and stream assignment, and the same `render()`/
`as_json()` output for identical inputs as before the extraction.

#### Scenario: Identical output for an identical fixture run
- GIVEN a fixed `--now`, `--offline-fixtures` directory, and state file
- WHEN the CLI runs through `main` delegating to `run_once`
- THEN stdout, stderr, and the exit code are identical to the pre-extraction behavior

#### Scenario: The snapshot-publish guard is preserved through the extraction
- GIVEN `publisher.publish` raises during a CLI run
- WHEN `main` runs through `run_once`
- THEN the `SNAPSHOT_FAILURE_PREFIX` line is still printed to stderr and the exit code is still `EXIT_OK`
