```yaml
schema: gentle-ai.verify-result/v1
evidence_revision: sha256:a85fada3d1fb9c5d1052d2a4180ddbef50c4ab6d84b7d2215f8c429ab130e6ad
verdict: pass
blockers: 0
critical_findings: 0
requirements: 35/35
scenarios: 67/67
test_command: uv run pytest
test_exit_code: 0
test_output_hash: sha256:3ed9c83a12d7a9945bc938c83632259dacefd2ca0e9f72dda448274e493e5941
build_command: uv run mypy
build_exit_code: 0
build_output_hash: sha256:f948243de639409228dd4bee63b27f24b14f54c40abe7bc306836ead628b67b0
```

# Verify Report: Core Alert Cycle (Local, No Cloud)

**Change**: `core-alert-cycle` · **Project**: `ferrenafe-rain-alert`
**Revision verified**: `main` @ `a2f3af8` (`a2f3af853a9e090eeb0194bf1c671cb936cd55af`), working tree clean, nothing unpushed
**Date**: 2026-09-07 · **Mode**: full spec-driven verification (proposal + 7 specs + design + tasks + apply-progress), Strict TDD active
**Artifact store**: hybrid (`openspec/changes/core-alert-cycle`, `sdd/core-alert-cycle`)

This supersedes the 2026-09-06 verification, which ran on the unmerged branch
`chore/land-slice3-remainder` @ `bcba50b` and reported 0 CRITICAL, 8 WARNING and
11 SUGGESTION against 35 requirements and 64 scenarios. Everything it named has
since been dispositioned and merged (pull requests 1 through 7), the specs
gained three scenarios in the process, and every count below was re-measured
from the specs and the running suite rather than carried over.

---

## How the envelope was measured

Nothing in the envelope is copied from the previous report or from an example.

| Field | How it was obtained |
|---|---|
| `evidence_revision` | `git rev-parse HEAD \| shasum -a 256` — the digest of the 41-byte output (40 hex characters plus its newline) naming the revision the evidence was measured at, `a2f3af853a9e090eeb0194bf1c671cb936cd55af` |
| `requirements: 35/35` | `grep -cE '^### Requirement:'` over the seven files under `specs/`, summed; then each one traced to code and to a test that ran green in this run |
| `scenarios: 67/67` | `grep -cE '^#### Scenario:'` over the same seven files, summed; then each one traced to a covering test that ran green in this run |
| `test_command` / `test_exit_code` / `test_output_hash` | `uv run pytest`, stdout and stderr combined, exit code read from `$?`; the digest is of the captured bytes with trailing newlines stripped |
| `build_command` / `build_exit_code` / `build_output_hash` | `uv run mypy`, same capture and same stripping rule |

Per-file counts, which sum to the envelope totals:

| Spec file | Requirements | Scenarios |
|---|---|---|
| `specs/alert-cycle/spec.md` | 4 | 8 |
| `specs/alert-dedup/spec.md` | 5 | 6 |
| `specs/local-alert-cli/spec.md` | 3 | 3 |
| `specs/repo-hygiene/spec.md` | 5 | 6 |
| `specs/risk-evaluation/spec.md` | 7 | 18 |
| `specs/source-outage-notices/spec.md` | 4 | 6 |
| `specs/weather-sources/spec.md` | 7 | 20 |
| **Total** | **35** | **67** |

The scenario total moved from 64 to 67 because the remediation added three
scenarios to `alert-cycle` — "The steps happen in the stated order", "A reason
the body already states in its own sentence is not repeated" and "Nothing is
recorded before delivery". No other spec file changed its counts;
`risk-evaluation` was amended in place (finding W7) without gaining or losing a
scenario. `weather-sources` last gained scenarios during the slice-3 contract
changes, which the 2026-09-06 verification already traced.

**Reproducibility of the two output digests.** `build_output_hash` is exactly
reproducible: `uv run mypy` prints one stable line. `test_output_hash` is
**not** reproducible across runs, because pytest's summary line ends in the
wall-clock duration (`in 1.73s` here). The exact hashed bytes are therefore
printed verbatim in the gate section below, so the digest is re-derivable from
this report even though a fresh `uv run pytest` will produce a different one.
The owner's independently captured `test_output_hash` for this same revision
was `sha256:24b10a1f2484df4b6e727c3d5f7992ff747b8f96b33037c835990599b681ca20`;
it differs from the value in the envelope for exactly this reason, and the
observable result — `557 passed, 15 deselected`, exit 0 — is identical.
`evidence_revision` and `build_output_hash` match the owner's values byte for
byte.

**Counting convention.** A scenario counts toward the `scenarios` numerator when
a test whose GIVEN/WHEN match the scenario asserted its outcome and passed at
runtime. `PASS*` in the tables below marks a scenario whose covering test proves
less than the scenario sentence claims; those still count as covered, exactly as
the 2026-09-06 report counted them, and each one is named as a finding. There is
one `PASS*` in this run, and no scenario is uncovered.

---

## Verdict

**PASS**

35 requirements and 67 Given/When/Then scenarios traced across the seven spec
files. All 35 requirements are implemented and proven by a test that passed at
runtime in this verification. All 67 scenarios have a covering test that passed;
66 of them would fail if the behaviour broke, and one (`alert-cycle` → "A reason
the body already states in its own sentence is not repeated") asserts a proxy
for its second clause — mutation-proven below as finding W-A.

No CRITICAL issue. 1 WARNING, 5 SUGGESTIONs. Nothing blocks archive: the
artifacts now describe the shipped system, and the one WARNING is a
test-strength gap on a behaviour the shipped code gets right.

| Severity | Count |
|---|---|
| CRITICAL | 0 |
| WARNING | 1 |
| SUGGESTION | 5 |

---

## Gate output

Run from the repository root on `main` @ `a2f3af8`.

The block below is the exact byte sequence hashed into `test_output_hash`
(674 bytes, trailing newline stripped):

```
........................................................................ [ 12%]
........................................................................ [ 25%]
........................................................................ [ 38%]
........................................................................ [ 51%]
........................................................................ [ 64%]
........................................................................ [ 77%]
........................................................................ [ 90%]
.....................................................                    [100%]
557 passed, 15 deselected in 1.73s
```

and the block below is the exact byte sequence hashed into `build_output_hash`
(43 bytes, trailing newline stripped):

```
Success: no issues found in 35 source files
```

Both commands exited 0. The remaining gate commands, recorded but not part of
the envelope:

```
$ uv run ruff check .
All checks passed!
exit=0

$ uv run ruff format --check .
78 files already formatted
exit=0
```

### Opt-in live suite

```
$ uv run pytest -m integration
.......ss.sss..                                                          [100%]
10 passed, 5 skipped, 557 deselected in 2.80s
exit=0
```

Clean on the first attempt, which is the point of finding S9's remediation: the
canary now fetches the live page once per session behind `lru_cache` and uses a
patient timeout of its own instead of the production 3-second connect budget.
The 2026-09-06 verification needed three attempts on this suite and the
remediation run needed two. The five skips are the per-family classifier
canaries for hazard families with no row in force on today's Lambayeque page
(`LLOVIZNA`, `GARUA`, `NEVADA`, `GRANIZO`, `FRIAJE`); they assert nothing rather
than assert falsely.

### Live CLI run, this verification, verbatim

```
$ uv run rain-alert-cycle --state-file <a path outside the repository>
Ferreñafe  (-6.6400, -79.7900)  (PLACEHOLDER — pending confirmation)
Sources    senamhi: available (fetched 2026-09-07T11:56:09+00:00)
           open_meteo: available (fetched 2026-09-07T11:56:09+00:00)
Notes      - senamhi: discarded warning 28745 (INCREMENTO DE VIENTO EN LA SIERRA NORTE): phenomenon wind cannot cause flooding
Level      none
Window     2026-09-07T11:56:09+00:00 → 2026-09-09T11:56:09+00:00
Reasons    —
Decision   NOT SENT — level none
Message    PREVIEW (NOT SENT)
           title: Alerta de lluvias — Ferreñafe — sin riesgo
           body:
             Ciudad: Ferreñafe
             Nivel: sin riesgo
             Ventana: del 07/09/2026 06:56 al 09/09/2026 06:56 (hora local, America/Lima)
             Recomendaciones:
             - Almacena agua potable para al menos dos días.
             - Protege documentos y aparatos eléctricos por encima del nivel del piso.
             - Limpia canaletas, techos y desagües cercanos.
             - Asegura objetos sueltos y calaminas.
             - Ten a mano una linterna, un botiquín y los teléfonos de emergencia.
Notices    0 emitted
exit=0
```

One hazard was in force on the live page — a wind warning — and it was
discarded with a stated reason. Nothing was transmitted. No file was written
inside the repository, and none was written at all: a `none` level authorizes no
send, so there is no dedup record to persist.

---

## Proposal success criteria

Every row in `proposal.md` → Success Criteria was checked individually. All
twelve checkboxes in `proposal.md` are now ticked (finding S7), and this table
is what backs them.

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | 29.8 mm / 24 h at >= 70 % -> `imminent` | PASS | `test_risk.py::test_risk_evaluation_scenarios[imminent-from-forecast-alone]` |
| 2 | 10 mm / 48 h at >= 60 % + yellow -> `prepare` | PASS | `[prepare-on-threshold]` |
| 3 | 9 mm / 48 h at >= 60 % + yellow -> `none` | PASS | `[below-prepare-threshold]`, plus the 16-row boundary table |
| 4 | SENAMHI unavailable, 15 mm / 48 h at 65 % -> `none` | PASS | `[degraded-prepare-requires-70-percent]` |
| 5 | Altered SENAMHI fixture -> `unavailable`, never a false calm | PASS | `test_senamhi_scraper.py::TestBreakageIsNeverAFalseCalm` (5 tests) |
| 6 | Healthy fixture, zero current warnings -> `available` + empty list | PASS | `test_a_page_whose_rows_are_all_historical_is_available_and_empty` |
| 7 | Repeat sends nothing; escalation sends; de-escalation sends nothing | PASS | `test_dedup.py` (6 tests) + `test_cli.py::test_a_second_identical_run_is_deduplicated` |
| 8 | SENAMHI unavailable + qualifying forecast -> body states the outage **and** one operator notice | PASS | Now proven in one cycle by `test_run_alert_cycle.py::test_one_degraded_cycle_discloses_the_outage_and_notifies_the_operator`. This was the PARTIAL of the previous run (S6b) |
| 9 | Three dual-outage cycles -> one notice; recovery -> one recovery notice | PASS | `test_run_alert_cycle.py::TestDualSourceOutageSuppressesCommunityAlerts` (2 tests) |
| 10 | `pytest` + `ruff` pass; domain imports nothing forbidden | PASS | gate above + `tests/architecture/test_layer_boundaries.py` (4 tests, 2 of them triangulation) |
| 11 | CLI runs the full cycle against real sources, prints level/reasons/message, sends nothing | PASS | live run above |
| 12 | `git grep` finds no phone numbers, emails, tokens, AWS IDs; `.env.example` names only | PASS | Now automated. `test_repo_hygiene.py::test_no_secret_or_personal_data_pattern_is_committed` over ten patterns, working tree **and** all 84 reachable commits. This was PASS (manual) in the previous run (W3) |

---

## Traceability

Legend — **PASS**: implemented and proven by a test that ran green and would
fail if the behaviour broke. **PASS***: proven, but the covering test proves
less than the scenario sentence claims.

### `specs/risk-evaluation/spec.md` — 7 requirements, 18 scenarios

| Requirement | Scenarios | Implementation | Proving test | Status |
|---|---|---|---|---|
| Full-source risk evaluation | 4 | `domain/risk.py` (`_imminent_from_official`, `_imminent_from_forecast`, `_prepare_combined`) | `test_risk.py::test_risk_evaluation_scenarios` ids `imminent-from-official-warning`, `imminent-from-forecast-alone`, `prepare-on-threshold`, `below-prepare-threshold`; plus `test_every_numeric_threshold_is_pinned_at_and_just_below_its_boundary` (16 cases) and, new since the previous run, a positive coastal **red** case (finding S4) | PASS |
| A highlands-only official warning is an early signal | 5 | `domain/hazards.py` (`may_raise_imminent`), `domain/entities.py`, `domain/risk.py` | `TestHighlandsRainIsAnEarlySignalNotAnImminentClaim` (5 tests, one per scenario) | PASS |
| Forecast-only prepare when SENAMHI is available but silent | 3 | `domain/risk.py` (`_prepare_forecast_only`, branch 4) | `TestForecastOnlyPrepareWithASilentButHealthySenamhi` (3 tests + a 7-case parametrized monotonicity guard) | PASS |
| Degraded evaluation when SENAMHI is unavailable | 2 | `domain/risk.py` (`_prepare_degraded`, branch 5) | ids `degraded-prepare-requires-70-percent`, `degraded-reasons-disclose-the-outage`; 4 boundary rows | PASS |
| Degraded evaluation when Open-Meteo is unavailable | 2 | `domain/risk.py` branch guards | ids `imminent-still-fires`, `prepare-cannot-fire-without-forecast-data` | PASS |
| Both sources unavailable yields no risk decision | 1 | `domain/risk.py` fallthrough | id `dual-outage` | PASS |
| Thresholds come from configuration | 1 | `domain/risk.py` (`RiskEvaluator(thresholds)`), `application/run_alert_cycle.py` (`evaluator_factory(config.thresholds)`) | `test_threshold_source_reflects_the_injected_config_not_a_literal`, triangulated against a lenient threshold set | PASS — the requirement was amended on 2026-09-06 (finding W7) to drop the "and coordinates" clause the evaluator never could satisfy; coordinates stay a `ForecastProvider` concern under `weather-sources` |

### `specs/alert-dedup/spec.md` — 5 requirements, 6 scenarios

| Requirement | Scenarios | Implementation | Proving test | Status |
|---|---|---|---|---|
| Send on new level for the window | 1 | `domain/dedup.py` | `test_dedup.py::test_first_alert_for_a_window_is_authorized` + `test_non_overlapping_prior_record_does_not_block_a_new_window` | PASS |
| Send on escalation | 1 | `domain/dedup.py`, `domain/values.py` | `test_prepare_escalates_to_imminent_is_authorized` | PASS |
| Never re-send the same level in the same window | 1 | `domain/dedup.py` | `test_repeat_level_is_not_authorized`; end to end in `test_cli.py::test_a_second_identical_run_is_deduplicated` | PASS |
| De-escalation sends nothing | 2 | `domain/dedup.py` | `test_imminent_de_escalates_to_prepare_is_not_authorized`, `test_de_escalation_to_none_is_not_authorized` | PASS |
| Dedup state is read from AlertRepository | 1 | `application/policies.py` | `tests/unit/application/test_policies.py` (4 tests, incl. the window-start clamp) | PASS |

### `specs/source-outage-notices/spec.md` — 4 requirements, 6 scenarios

| Requirement | Scenarios | Implementation | Proving test | Status |
|---|---|---|---|---|
| Single-source outage notice | 2 | `domain/outage.py` (transition rows 3, 4 and the never-notified variant) | `test_outage.py` 4 tests; end to end `test_cli.py::test_the_outage_notice_is_not_repeated_on_the_next_cycle` | PASS |
| Dual-source outage suppresses community alerts | 1 | `domain/outage.py` (`suppress`), `application/run_alert_cycle.py` hard guard | `test_three_consecutive_dual_outage_cycles_send_zero_community_alerts_and_one_notice` | PASS |
| Recovery notice on source return | 2 | `domain/outage.py` | `test_outage.py` rows 2 and 7 + `test_no_recovery_notice_without_a_prior_outage`; end to end `test_recovery_after_dual_outage_emits_exactly_one_recovery_notice` | PASS |
| Notices use a distinct type and dedup store | 1 | `domain/values.py` (`NoticeKind`), `ports/__init__.py` (D3 two-method split), `adapters/console_notifier.py` | `test_console_notifier.py` (9 tests, distinct prefixes); `test_notices_use_a_distinct_kind_from_a_community_alert`, which now asserts the disjointness its docstring claims (`{kind.value}.isdisjoint({level.value})`) instead of a membership check that could not fail (finding S5) | PASS |

### `specs/alert-cycle/spec.md` — 4 requirements, 8 scenarios

| Requirement | Scenarios | Implementation | Proving test | Status |
|---|---|---|---|---|
| Cycle orchestration order | Authorized send runs the full chain; dedup short-circuits the cycle; **the steps happen in the stated order** | `application/run_alert_cycle.py` | `TestAuthorizedSendRunsTheFullChain` (2), `TestDedupShortCircuitsTheCycle` (2), and `TestTheCycleRunsItsStepsInTheSpecifiedOrder::test_an_authorized_send_calls_the_ports_in_the_order_the_spec_lists`, which pins the whole ten-step sequence against the shared `CallLog` | PASS — the "in order" clause is asserted for the first time, and mutation-proven below |
| Deterministic message template satisfies MessageComposer | Template content; **a reason the body already states in its own sentence is not repeated** | `domain/template.py` (`_reason_line_es`, the `Motivos:` block) | `TestTemplateContent` (4) + `TestLocalTimeDisplay` (2) for the first scenario; `TestDegradedDisclosure::test_the_outage_is_stated_exactly_once` for the second | **PASS\*** — see W-A: the second scenario's covering test asserts the outage sentence appears exactly once, not that the reason produced no `Motivos:` bullet |
| Degraded-mode message discloses source unavailability | 1 | `domain/template.py` | `TestDegradedDisclosure` (4, incl. the "not disclosed when available" triangulation) | PASS |
| Sent alerts are recorded after notification | Record after notify; **nothing is recorded before delivery** | `application/run_alert_cycle.py` | `TestRecordAfterNotify::test_recorded_alert_carries_level_window_and_message` and `TestTheCycleRunsItsStepsInTheSpecifiedOrder::test_the_alert_is_recorded_only_after_the_notifier_delivered_it` | PASS — the "only after" clause is asserted for the first time, and mutation-proven below |

### `specs/weather-sources/spec.md` — 7 requirements, 20 scenarios

| Requirement | Scenarios | Implementation | Proving test | Status |
|---|---|---|---|---|
| Open-Meteo forecast acquisition | 2 | `adapters/open_meteo.py` | `test_open_meteo.py` (`httpx.MockTransport`, incl. an 8-case malformed-payload table and the `NaN`/`Infinity` guard) | PASS |
| SENAMHI warning acquisition | 1 | `adapters/senamhi_scraper.py` | `test_a_page_whose_rows_are_all_historical_is_available_and_empty` | PASS |
| Scraper breakage yields unavailable, never a false calm | 2 | `senamhi_scraper.py` | `TestBreakageIsNeverAFalseCalm` (5 tests, incl. the renamed-header triangulation) | PASS |
| An unparseable row in force is a structural failure | 3 | `senamhi_scraper.py` (`_row_is_marked_in_force`, `_refuse_if_in_force`) | `TestAnUnparseableRowInForceIsStructuralFailure` (3) + `TestRowAnomalies` (4) + `test_a_historical_row_anomaly_reaches_the_operator_through_the_port` | PASS |
| Only flood-relevant official warnings may drive an alert | 8 | `domain/hazards.py`, `adapters/senamhi_classification.py`, `senamhi_scraper.py` | `test_hazards.py`, `test_senamhi_classification.py` (over live vocabulary), `test_senamhi_scraper.py::TestTheMultiHazardFilter` — which now includes `test_the_article_variant_of_the_heat_warning_is_discarded_too` at the provider level, closing finding S3b — plus `test_cli.py::test_the_red_heat_warning_in_force_does_not_raise_the_level` and its coastal triangulation | PASS |
| Only warnings currently in force may drive an alert | 3 | `senamhi_scraper.py` | `TestHistoricalRows` (4) | PASS |
| Coordinates come from configuration | 1 | `adapters/local/static_config_repository.py` | `test_local_repositories.py` + `test_cli.py::test_the_placeholder_coordinate_marker_is_printed` | PASS |

### `specs/local-alert-cli/spec.md` — 3 requirements, 3 scenarios

| Requirement | Scenarios | Implementation | Proving test | Status |
|---|---|---|---|---|
| CLI runs the cycle against real adapters | 1 | `entrypoints/wiring.py` | `TestWiring::test_without_fixtures_the_real_adapters_are_wired`; live behaviour by the opt-in canaries and the run above | PASS |
| CLI never sends externally | 1 | `adapters/console_notifier.py`, `entrypoints/wiring.py` | `test_the_wiring_can_only_construct_the_console_notifier`, `test_there_is_deliberately_no_dry_run_flag`, `test_the_module_imports_no_client_that_could_deliver_anything` (AST) | PASS |
| CLI output is informative for calibration | 1 | `entrypoints/cli.py` (D11 preview without invoking the composer) | `TestOutputOnEveryRun` (8) + `TestTheDecisionAndTheSend` (5), and `evaluated_at` is now asserted to be the cycle clock and to differ from `window_start` (finding W4) | PASS |

### `specs/repo-hygiene/spec.md` — 5 requirements, 6 scenarios

| Requirement | Scenarios | Implementation | Proving test | Status |
|---|---|---|---|---|
| Apache 2.0 license present | 1 | `LICENSE` | `test_license_file_contains_apache_2_0_text` | PASS |
| README carries the required disclaimers | 1 | `README.md` | `test_readme_contains_the_required_disclaimers` | PASS |
| No secrets or personal data ever committed | Grep check passes; `.env.example` has no values | the check itself in `tests/hygiene/test_repo_hygiene.py`; `.env.example`; `adapters/local/static_contact_repository.py` | `test_no_secret_or_personal_data_pattern_is_committed` (working tree **and** history), `test_env_example_lists_variable_names_with_no_values`, plus 8 tests pinning the guard's own behaviour | PASS — no longer manual (finding W3); see S-A on the scenario text |
| `.gitignore` excludes local secrets and environment files | 1 | `.gitignore` | `test_gitignore_excludes_local_secrets_and_state`, which now asks `git check-ignore -q` for `.env`, `.local-state/alerts.json` and `.venv/pyvenv.cfg` instead of scanning for the substring `.env` (finding S6) | PASS |
| Domain package stays free of adapter/AWS/LLM imports | 1 | `tests/architecture/test_layer_boundaries.py` | 2 assertions + 2 triangulation tests (function-nested and relative imports) | PASS |

---

## What the remediation closed, and how I confirmed it

The 2026-09-06 verification raised 8 WARNINGs and 11 SUGGESTIONs. All 19 are
dispositioned in `tasks.md` under "Added at the 2026-09-06 verification
remediation": 13 fixed in code, 9 in the artifacts (some findings in both), and
one partial deferral. I re-checked each claim against the merged code rather
than accepting the disposition.

### W2 — the two ordering guarantees, confirmed by mutation

`tests/support/fakes.py` gained a `CallLog` that every spy appends to, and
`tests/support/wiring.py` hands one instance to all seven leaves, so cross-port
order is observable for the first time.

I re-ran the owner's mutation independently, and without touching the
repository: `src/` was copied to a scratch directory, `AlertRecord` construction
and `deps.alerts.record_alert(record)` were moved **above**
`deps.notifier.send_alert(message, recipients)` in the copy, and the suite was
run against the copy by putting it first on `PYTHONPATH` (confirmed by printing
`rain_alert.application.run_alert_cycle.__file__`). Result:

```
2 failed, 555 passed, 15 deselected in 1.33s

FAILED test_run_alert_cycle.py::TestTheCycleRunsItsStepsInTheSpecifiedOrder::test_an_authorized_send_calls_the_ports_in_the_order_the_spec_lists
FAILED test_run_alert_cycle.py::TestTheCycleRunsItsStepsInTheSpecifiedOrder::test_the_alert_is_recorded_only_after_the_notifier_delivered_it
E       AssertionError: assert 9 < 8
E        +  where 9 = position_of('notifier', 'send_alert')
E        +  and   8 = position_of('alerts', 'record_alert')
```

Exactly two tests fail, with the `assert 9 < 8` the owner reported, and the
repository working tree was unchanged throughout. Before the shared `CallLog`
existed, the same mutation left the whole suite green. Both ordering guarantees
are genuinely guarded.

### W1 — the warning summary now agrees with the verdict

`_warnings_behind` in `application/run_alert_cycle.py` reads the contributing
warnings back off `assessment.reasons` instead of re-deriving the evaluator's
branch conditions, and `_most_severe_warning` picks from that set. The field had
zero assertions before; it now has three, in
`TestTheWarningSummaryAgreesWithTheVerdict`:

- an `imminent` verdict earned by a coastal orange, alongside a highlands red
  that branch 1 refused to act on, names the coastal one — the exact case the
  finding reproduced;
- a `prepare` verdict names the most severe **contributor**, which on the
  combined branch legitimately is the highlands red, so the rule is not "never
  highlands";
- a forecast-only verdict with a yellow aviso on the page names **no** warning
  at all, rather than an aviso the reasons do not support.

### W3 — the committed-secret guard

The requirement is "no secrets or personal data at **any commit**". The guard
now matches that:

- `_grep` scans the tracked working tree and `_grep_history` scans every line of
  every commit reachable from `--all` (84 commits here), because deleting a
  pasted credential and committing the deletion leaves it live in every clone.
  Two tests prove the difference on a purpose-built repository where a key was
  committed and then removed: the working-tree pass is blind to it, the full
  scan reports it.
- Ten patterns, up from the four the scenario names: Peruvian phone number,
  email address, AWS access key id, AWS account id, GitHub token, API secret
  key, Telegram bot token, AWS secret access key, Peruvian mobile in local
  nine-digit form, and Slack token. The last four are the shapes changes 2 and 3
  will introduce, added before that code lands.
- Forgiveness is per match and per pattern, not per line. The one allow-listed
  placeholder is `+51 999 000 111` inside the hostile-title attack payload;
  `TestTheAllowListForgivesTheMatchNotTheLine` proves that the same line outside
  an allow-listed file still offends, that another secret shape sharing the line
  is not forgiven, and that a real number sharing the line is not forgiven.
  `test_forgiveness_is_granted_only_for_the_shapes_the_placeholder_has` pins the
  granted set so adding a pattern cannot silently widen it.
- `test_every_allow_listed_placeholder_line_still_exists` fails if an
  allow-listed file stops carrying the placeholder, so the allow-list cannot
  outlive what it forgives.

The scan is green on this revision, and it proved itself on live content during
this phase rather than only on its own fixtures. The first draft of the
`state.yaml` verify block quoted the placeholder literally while explaining
finding S-C. `state.yaml` is not in `PLACEHOLDER_ALLOW_LIST`, so the guard
failed the suite immediately:

```
E   AssertionError: committed secrets or personal data:
E     Peruvian mobile, local form: openspec/changes/core-alert-cycle/state.yaml:520: ...
E     Peruvian phone number: openspec/changes/core-alert-cycle/state.yaml:520: ...
E   1 failed, 556 passed, 15 deselected
```

Both patterns fired, on the correct file and line, for a phone-shaped string
that had never been in the repository before, in a file the allow-list does not
cover — and it fired before the commit rather than after. That is the regression
guard finding W3 asked for, doing the job on a real case. The block was reworded
to describe the placeholder instead of repeating it, and the suite returned to
557 passed.

Its scope is now **wider** than the scenario's own wording, which is finding
S-A.

### The three scenarios added during the remediation

- "The steps happen in the stated order" — covered and mutation-proven above.
- "Nothing is recorded before delivery" — covered and mutation-proven above.
- "A reason the body already states in its own sentence is not repeated" —
  covered by `test_the_outage_is_stated_exactly_once`, but only as a proxy. See
  W-A.

### The rest, spot-checked against the code

| Finding | Confirmed |
|---|---|
| W4 `evaluated_at` | `CycleResult.evaluated_at` carries the cycle clock; `cli.py` emits it; `test_cli.py` asserts both `== NOW_DT.isoformat()` and `!= document["window_start"]` |
| W5 `forecast_days` | `tasks.md` now says 4 in both places, and marks where it used to say 3 |
| W5b `domain/sanitize.py` | `design.md` has a new section 3.3 stating the sanitization rule and its four rendering boundaries, section 12's file table lists the module, and the module docstring now cites 3.3 instead of 3.2 |
| W6 `state.yaml` | rewritten to the shipped system, including a `security-remediation` slice entry that previously did not exist |
| W7, W8 | both spec sentences amended in place, each with a dated note explaining what changed and why |
| S1, S2 | `domain/risk.py` now states one numbering: 4 is forecast-only, 5 is degraded, matching `design.md` section 5 |
| S4 | a positive coastal **red** case now asserts `imminent` directly |
| S5 | the near-tautological assertion replaced by the disjointness its docstring claimed |
| S6 | `.gitignore` scenario asks `git check-ignore` instead of scanning for a substring |
| S6b | one cycle now asserts both the body disclosure and the single operator notice |
| S7 | all twelve `proposal.md` criteria ticked |
| S9 | canary caches the page per session and uses its own patient timeout; the suite passed first try in this run |
| S10, S11 | `design.md` amended: five real fixture names, and a note that `TimeWindow.key()` exists as a change-3 affordance |
| S3 (partial) | **deferred on purpose.** `AlertConfig.forecast_hours` still does not move the evaluation window; the horizons stay module constants in `domain/risk.py`. `design.md` section 5 and `tasks.md` both say so. Making them configurable means deciding whether a threshold calibrated over 48 hours still means anything over 72, which is a calibration decision for the first rainy season, not a refactor |

Task completion: 44 of 44 checked in `tasks.md`, 0 pending, and the native
dispatcher agrees (`taskProgress.allComplete: true`). Every slice is merged;
pull requests 1 through 7 are on `main` and nothing is unpushed.

---

## Design coherence

| Contract | Result |
|---|---|
| §4 — the seven port signatures | **Exact match.** `ports/__init__.py` reproduces every signature in the design's code block, including the `Notifier` two-method split (D3) and the five-method `AlertRepository`. `@runtime_checkable` correctly absent. The `MessageComposer` docstring carrying the sanitization invariant is now recorded in the design (§3.3), which closes the one gap the previous run found here |
| §4.1 — `AlertRepository` key design | **Honoured.** `alerts_with_window_start_between` is a key-range read with the rules left in `AlertPolicy`; `JsonFileAlertRepository` persists the same two collections via `adapters/serialization.py`, so the file and DynamoDB shapes stay identical. `TimeWindow.key()` has no production caller and is now documented as a change-3 affordance |
| §5 — evaluation order | **Six branches in the specified order**, and the prose numbering now agrees with the table |
| §6 — the outage transition table | **All eight rows plus the ninth (`notified_at is None`)**, each with a dedicated test. `state_changed` is the only persistence gate; `test_a_healthy_cycle_performs_no_outage_writes` pins it |
| D1–D12 | All honoured. D1 frozen slotted dataclasses + `StrEnum`; D2 `Available`/`Unavailable` tagged union; D3; D4 `httpx` + `MockTransport`; D5 `beautifulsoup4` on `html.parser`; D6 `CycleDependencies`; D7 injected clock, UTC enforced, `zoneinfo` display only; D8 `requires-python >= 3.12`; D9 `mypy` clean on 35 files; D10 `max_probability_pct` is `max` over the horizon; D11 `message_request` always populated; D12 no retries |
| §12 file table | Complete. All six additions are listed: `domain/reasons.py`, `domain/hazards.py`, `domain/sanitize.py`, `application/policies.py`, `adapters/serialization.py`, `adapters/local/offline_sources.py` |
| §8.1 `forecast_days` | Code, design and the architecture guide all say 4, and `tasks.md` now agrees |
| §8.2 fixture names | Amended to the five files that exist |

No design deviation remains unrecorded.

---

## Strict TDD verification

Strict TDD Mode is active for this project (`state.yaml` -> `execution.strict_tdd: true`),
so the checks below are part of the gate, not extras.

### TDD Compliance

| Check | Result | Details |
|---|---|---|
| TDD Evidence reported | Yes | Three "TDD Cycle Evidence" tables in `apply-progress.md`, one per phase (lines 76, 218, 465) |
| All implementation tasks have tests | Yes | 34 of the 44 task ids appear in an evidence row. The 10 without one are not RED/GREEN work: 1.1 tooling scaffold, 1.5 / 2.22 / 3.16 verification gates, 2.9 the `Protocol`-only `ports/__init__.py` (enforced by `mypy --strict` per D9 and by fake conformance in 2.18), 2.18 the test-support fakes themselves, 3.3 the one-time fixture capture, 3.10 the local wiring, 3.14 the manual live smoke, 3.15 a REFACTOR task. 3.10 has no evidence row but is covered anyway, by `TestWiring` |
| RED confirmed (test files exist) | Yes | Every test file named in the three tables exists; 27 test files collected |
| GREEN confirmed (tests pass) | Yes | 557 of 557 default-suite tests passed in this run; the 15 integration tests are deselected by marker and were run separately, 10 passed / 5 skipped |
| Triangulation adequate | Yes | Triangulation is explicit and named in the code: 2 cases on the architecture boundary test, 5 in `TestBreakageIsNeverAFalseCalm`, 16 rows in the risk boundary table, 7 parametrized cases in the forecast-only monotonicity guard, and several tests carry a docstring that opens with "Triangulation:" and states which false positive they exist to kill |
| Safety net for modified files | Yes | Each evidence table records the suite state before modification, and `apply-progress.md` records the running counts across remediations (98 -> 149 -> 165 -> 455 -> 537 -> 549 -> 557) |

**TDD Compliance**: 6/6 checks passed.

### Test Layer Distribution

| Layer | Tests | Files | Tools |
|---|---|---|---|
| Unit | 539 | 23 | `pytest`, `httpx.MockTransport`, hand-written fakes (no `unittest.mock`, design §10) |
| Architecture | 4 | 1 | `pytest` + `ast` |
| Hygiene | 14 | 1 | `pytest` driving `git grep` and `git check-ignore` |
| Integration (live network, opt-in) | 15 | 2 | `pytest -m integration` against the real SENAMHI page and the Open-Meteo API |
| **Total** | **572** | **27** | |

572 collected, of which 557 run by default and 15 are deselected by the
`integration` marker. There is no E2E layer and none is expected: the system has
no user interface and no external delivery, so the CLI *is* the outermost
boundary, exercised by `tests/unit/entrypoints/` (36 tests) and by the live run
recorded above.

Every spec scenario is covered at the unit layer at minimum. Several
requirements are additionally covered end to end through the CLI — dedup, the
outage notice, the multi-hazard filter, the placeholder marker, `evaluated_at`
and the degraded exit code — while `repo-hygiene` and the domain-import boundary
are covered only at the hygiene and architecture layers, which is where they
belong.

### Changed File Coverage

Coverage analysis skipped — no coverage tool detected. `pytest-cov` is not a
dependency and `import pytest_cov` raises `ModuleNotFoundError`. This is not a
failure and it is not blocking: coverage is informational under this gate.
Requirement and scenario coverage was established by tracing each spec sentence
to a named test instead, and the two mutations above test the thing a
line-coverage number cannot — whether the tests would notice if the behaviour
changed.

### Assertion Quality

Every test file was scanned for the banned patterns.

| Pattern | Hits | Disposition |
|---|---|---|
| Tautology (`assert True`, `assert 1 == 1`, `assert not False`) | 0 | Clean. The one near-tautology the previous run found (finding S5, an `in set(NoticeKind)` membership check that could not fail) was replaced by the disjointness assertion its docstring had claimed |
| Assertion with no production call | 0 | Clean |
| Lone `is not None` | 14 | All 14 are narrowing guards followed within a line or two by a value assertion — for example `assert summary is not None` immediately followed by the `(title, level, source_id)` tuple assertion in `TestTheWarningSummaryAgreesWithTheVerdict`. None is a test's only assertion |
| Empty-collection assertion without a companion non-empty test | 0 of 32 | All 32 have a companion in the same class asserting the non-empty case, and several companions open their docstring with "Triangulation:" — `test_an_alert_outside_the_range_is_not_returned` beside `assert found == (record,)`, `test_the_scan_reports_the_credential_the_deletion_left_behind` beside `assert _grep(...) == []`, `test_the_same_line_outside_the_allow_list_still_offends` beside the forgiven-line assertion |
| Lone `len(...) == 0` | 0 | Clean |
| Ghost loop (assertions inside a loop over a possibly-empty collection) | 0 | The loops that exist assert *absence* over a pattern table (`for phrase in OPERATOR_ONLY_PHRASES`), whose collection is a module constant that cannot be empty |
| Mock-heavy test | 0 | `unittest.mock` is deliberately not used anywhere. `fakes.py` says why: `Mock` satisfies any `Protocol` and would hide exactly the drift these tests exist to catch |

**Assertion quality**: 0 CRITICAL, 0 WARNING from the pattern scan.

The one assertion-strength problem in the change is not one of these shapes and
was found by mutation rather than by scanning: finding W-A, where the assertion
is real and non-trivial but proves less than its scenario claims.

### Quality Metrics

**Linter**: no errors — `uv run ruff check .` clean and
`uv run ruff format --check .` reports 78 files already formatted, both exit 0.
**Type checker**: no errors — `uv run mypy` under `strict = true` (D9) reports
`Success: no issues found in 35 source files`, exit 0.

---

## Findings

### CRITICAL

None.

### WARNING

**W-A — the new `alert-cycle` scenario about the unrepeated reason is guarded by
a proxy assertion, and a differently worded bullet survives.**
`openspec/changes/core-alert-cycle/specs/alert-cycle/spec.md:39-42`;
`tests/unit/domain/test_template.py:259-274`; `src/rain_alert/domain/template.py`
(`_reason_line_es`).

The scenario's THEN has two clauses: the outage is stated exactly once **by the
degraded sentence**, and it is **not repeated as a `Motivos:` bullet**. The
covering test asserts only `message.body.count("no estuvo disponible") == 1`.

That catches the regression the finding was written about — a
`SenamhiUnavailableReason` rendering the same sentence again — but it is a
wording check, not a structural one. I confirmed the gap by mutation, again on a
scratch copy of `src/` with nothing written to the repository: with

```python
case SenamhiUnavailableReason():
    return "Sin datos oficiales del SENAMHI en esta evaluacion."
```

the reason **does** produce a second `Motivos:` bullet stating the same fact, the
scenario's second clause is violated, and the whole suite stays green:

```
557 passed, 15 deselected in 1.27s
```

Nothing else in the suite looks at the bullet set of a degraded body:
`test_body_of_a_degraded_alert_carries_no_english_audit_phrases` checks only for
English audit wording, and `TestTemplateContent` uses a non-degraded request.

The shipped code is correct — `_reason_line_es` returns `None` for that reason,
and the design argues the decision at §3.1. This is a test-strength finding, not
a behaviour defect, which is why it is a WARNING and why the scenario still
counts as covered.

*Do*: assert the structure the scenario actually states — that the `Motivos:`
block of a degraded body contains exactly the bullets for the reasons that are
not restated elsewhere (here, only the forecast-threshold bullet). One
additional assertion in `test_the_outage_is_stated_exactly_once` closes it.

### SUGGESTION

**S-A — the `repo-hygiene` "Grep check passes" scenario is narrower than both
its requirement and its implementation.** The scenario says "GIVEN the full
repository **working tree**" and names four shapes (phone numbers, emails,
tokens, AWS account IDs). The requirement above it says "at **any commit**", and
the shipped guard scans all 84 reachable commits over ten patterns. The
implementation is right and the requirement is satisfied; the scenario text was
simply never widened when the guard was. Amend the GIVEN to name history, and
point the WHEN at `SECRET_PATTERNS` rather than listing four shapes that will
drift again the next time one is added.

**S-B — `GIT_IDENTITY` is defined twice in
`tests/hygiene/test_repo_hygiene.py`**, at lines 224-231 and again at 266-273.
The second shadows the first before any use, so the first is dead code that
reads like a live constant. Delete the earlier definition; keep the later one,
which is the one carrying `commit.gpgsign=false`.

**S-C — the hygiene allow-list couples the test suite to this report's text.**
`PLACEHOLDER_ALLOW_LIST` names
`openspec/changes/core-alert-cycle/verify-report.md`, and
`test_every_allow_listed_placeholder_line_still_exists` fails if an allow-listed
file stops carrying the `+51 999 000 111` placeholder. I hit this while
rewriting the report: dropping the quoted transcript would have broken the
suite. The coupling is defensible today — it is what keeps the allow-list from
outliving what it forgives — but `sdd-archive` will move this file, so the entry
has to move with it or the suite will fail on the archive commit. Worth naming
now, in the artifact the archive is assembled from.

**S-D — `test_output_hash` cannot be reproduced from a rerun.** pytest's summary
line ends in the wall-clock duration, so the digest of the captured output is
run-specific by construction; two runs of an identical, all-green suite produce
two different hashes. This report works around it by printing the exact hashed
bytes. A future verification could make the field genuinely reproducible by
hashing a normalized capture — the same bytes with ` in <n>s` removed — and
saying so in the envelope's own evidence section. The field is doing its job
either way, but only the printed bytes make it auditable.

**S-E — S3 stays open, and it is the one thing the remediation deliberately did
not finish.** `AlertConfig.forecast_hours` governs what is fetched and what
`_build_message_request` summarizes, but not what the evaluator evaluates; the
horizons are `IMMINENT_HORIZON_HOURS = 24` and `PREPARE_HORIZON_HOURS = 48` in
`domain/risk.py`. Setting `forecast_hours = 72` fetches 72 hours and still
assesses 48. The design and `tasks.md` both say so now, so nothing is hidden,
but the knob still implies more than it does. Closing it is a calibration
decision — whether a threshold calibrated over 48 hours means anything over 72 —
and belongs to the first rainy season, not to this change.

---

## What this change does NOT deliver

Stated explicitly so the archive record is honest about scope.

**Deliberately out of scope (`proposal.md`, Out of Scope):**

- **No AWS anything.** No Lambda, DynamoDB, SSM, EventBridge Scheduler, CDK, IaC
  or `moto` tests. All of it is change 3.
- **No LLM.** No Strands, no AgentCore, no output validator, no fallback wiring.
  The `MessageComposer` port is declared and satisfied *only* by the
  deterministic Spanish template in `domain/template.py`. Change 2 swaps the
  adapter.
- **No delivery of any kind.** There is no Telegram, SES, SMS or webhook
  notifier, and no notifier capable of transmitting exists anywhere in the
  codebase — the dry-run guarantee is the absence of a capability, not a flag.
  `ConsoleNotifier` prints and nothing else.
- **No real recipients.** `StaticContactRepository` returns one synthetic console
  contact. No contact data, no consent handling, no recipient management, no
  admin UI. Automatic delivery is v2.

**Known limitations inside the delivered scope:**

- **The coordinates are a placeholder.** `(-6.64, -79.79)` with
  `coordinates_are_placeholder=True`; the CLI prints
  `(PLACEHOLDER — pending confirmation)` on every run, as it did in the live run
  above. `design.md` §15 and `state.yaml` → `open_decisions.ferrenafe-coordinates`
  remain open. Every forecast produced so far is for an approximate point.
- **The thresholds are uncalibrated.** 9.5 mm / 48 h comes from a Reque p95
  boundary; `max`-over-horizon probability aggregation (D10) is a documented
  *interpretation* of an underspecified rule. Neither has seen a rainy season.
  Calibration is what the CLI exists for.
- **The evaluation horizons are not configurable** (S-E), even though
  `forecast_hours` looks as if it should move them.
- **No retries or backoff** (D12). One failed fetch is an immediate
  `Unavailable`; the cycle repeats in six hours — except that nothing schedules
  the cycle yet.
- **No scheduling.** The only way to run a cycle is a human typing
  `uv run rain-alert-cycle`. Nothing runs every six hours.
- **The state file is not concurrency-safe.** Measured, not theorised: two
  concurrent runs lose each other's dedup records, and a lost dedup record means
  a community re-alerted for a window already delivered. Deliberately deferred
  with a concrete `fcntl.flock` sidecar proposal in `tasks.md` Open Items,
  because `AlertRepository` has no lifecycle in its contract and change 3
  replaces the adapter with a conditional DynamoDB write. Unrealistic for a
  hand-run CLI; real the moment a scheduler exists.
- **`JsonFileAlertRepository.alerts` grows without bound**, and the whole
  document is rewritten on every send. Retention rule proposed, not implemented.
- **Scraper drift is detected only by an opt-in canary** that is deselected from
  the default suite. It is more reliable than it was — one fetch per session,
  patient timeout, green first try here — but nothing offline can notice the live
  page changing.
- **The zone rule accepts any mention of `COSTA`, including `COSTA SUR`** (1 row
  of 789). Since highlands rain is now kept, this can only over-weight a
  warning, never drop one — a calibration question carried to change 3.
- **Detail pages are never fetched.** `Warning.source_id` carries the identifier
  for change 2's `get_warning_detail` tool; the aviso body is unread.
- **`MessageRequest.warning` is populated and now tested, but still never
  rendered.** It is a contract for change 2 that no current consumer exercises.
- **The 400-line review budget was exceeded in every slice** — 324, 2356 and 4507
  changed lines against ~150 / ~400 / ~450 estimates. Each was flagged and
  accepted as a `size:exception`, or split into the 3a/3b/3c chain, rather than
  absorbed silently; but the change as merged was never reviewed within the
  budget the process sets.

---

*Generated by `sdd-verify`. No source file and no test was modified by this
phase: both mutations were applied to scratch copies of `src/` outside the
repository and run by shadowing the package on `PYTHONPATH`.*
