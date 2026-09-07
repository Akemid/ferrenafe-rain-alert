# Repo Hygiene Specification

## Purpose

Public-repository hygiene rules from design spec section 11: license, disclaimer, and exclusion of secrets and personal data.

## Requirements

### Requirement: Apache 2.0 license present

The repository root MUST contain a `LICENSE` file with the Apache License 2.0 text.

#### Scenario: License file check
- GIVEN the repository root
- WHEN `LICENSE` is inspected
- THEN it contains the Apache License 2.0 text

### Requirement: README carries the required disclaimers

`README.md` MUST state that the system is a community complement to SENAMHI/INDECI's official warnings, not a replacement, provided without warranty, and MUST note that Open-Meteo is used under its non-commercial license.

#### Scenario: Disclaimer present
- GIVEN `README.md`
- WHEN its content is checked
- THEN it contains the SENAMHI/INDECI-complement statement, the no-warranty statement, and the Open-Meteo non-commercial note

### Requirement: No secrets or personal data ever committed

The repository MUST NOT contain phone numbers, emails, tokens, AWS account IDs, or other secrets/personal data at any commit; `.env.example` MUST list variable names only, with no values.

#### Scenario: Grep check passes
- GIVEN the full repository working tree
- WHEN a `git grep` scan runs for phone numbers, emails, tokens, and AWS account IDs
- THEN no matches are found outside documented fixture placeholders

#### Scenario: .env.example has no values
- GIVEN `.env.example`
- WHEN each line is checked
- THEN each line lists a variable name with no assigned secret value

### Requirement: .gitignore excludes local secrets and environment files

`.gitignore` MUST exclude `.env` and other local secret/state files from version control.

#### Scenario: Ignored env file
- GIVEN a local `.env` file created from `.env.example`
- WHEN `git status` is checked
- THEN `.env` does not appear as trackable

### Requirement: Domain package stays free of adapter/AWS/LLM imports

An automated check MUST verify that `src/rain_alert/domain/` imports nothing from `adapters/`, `boto3`, `requests`, `httpx`, or any LLM SDK.

#### Scenario: Import boundary test
- GIVEN the test suite
- WHEN the domain-import-boundary test runs
- THEN it fails if any forbidden import is found in `domain/` and passes otherwise
