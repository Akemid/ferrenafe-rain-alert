# Apply progress: agent-system-prompt

## Batch 1 (PR 1, branch feat/agent-sp-1-contract): Phase 1 tasks 1.1-1.10 done; 1.11 (security review) pending

- 1.1/1.3/1.4: app-side test written first. Red 1: `FileNotFoundError` on `contracts/composer-fence.json` (5/5 tests). With a JSON lacking `prompt_key`: red on `KeyError: 'prompt_key'` and on the pinned-literal assertion ("Right contains 1 more item"). Green after `prompt_key` added.
- 1.5 mutation proof: contract open marker changed to `<data:{token}>` -> `test_the_contract_pins_the_values_the_design_chose` and `test_the_user_turn_carries_the_contract_markers` red; reverted.
- 1.6: `agent/tests/test_fence_contract.py`. `PROMPT_KEY == prompt_key` passes at once (characterization). The prompt half is `xfail(strict=True)`; with `--runxfail` it fails with `ModuleNotFoundError: No module named 'prompts'`. PR 3 task 3.4 removes the marker.
- 1.7: `tests/architecture` green; neither unit imports the other.
- 1.8 characterization against the UNCHANGED validator (observed, not assumed):
  - "entre el 2 y el 5 de octubre" -> {UNKNOWN_NUMBER} only
  - "vigente hasta el 5 de octubre de 2026" -> {UNKNOWN_NUMBER} only
  - city only in the title -> {CITY_MISSING} only
  - body obeying D49 -> no violations. First attempt of that body wrote "Aviso oficial del SENAMHI de lluvias intensas" and drew SENAMHI_ATTRIBUTION (the attribution rule wants the template's fact form or a verbatim title quote); fixture corrected to the template's own reason line. Not a validator surprise about the incident shapes, but a D49 prompt-writing hint for PR 3: SENAMHI must be attributed as a stated fact/quotation.
  - Teeth: swapped expectation CITY_MISSING -> UNKNOWN_NUMBER, red; reverted.
- 1.9: `git diff main -- message_validation.py test_message_validation.py` is empty.
- 1.10 gate: pytest 1361 passed; agent pytest 33 passed, 1 xfailed; ruff check, ruff format --check, mypy clean; tests/hygiene green.

## Batch 2 (PR 2, branch feat/agent-sp-2-live-script): Phase 2 tasks 2.1-2.9 done; 2.10 (security review) pending

Also: docs commit 6b07f6f records the owner-approved SENAMHI attribution rule in D49 (design.md bullet, Owner Decisions entry; engram design #2721 updated) and adds Phase 3 task 3.4a (RED test for the SENAMHI sentence), unimplemented.

TDD cycle evidence:
| Task | RED (observed) | GREEN | Notes |
|---|---|---|---|
| 2.1/2.2 tally + incident fixture | `FileNotFoundError` on scripts/agent_live_acceptance.py (script absent) | 5 passed | first green attempt also needed the test loader to register the module in sys.modules (dataclass) |
| 2.3/2.4/2.5 runner + secrecy | `AttributeError: module has no attribute 'run_gate'` (6 tests) | 11 passed | secrecy masks the given ARN, the per-run fence token and any `arn:aws...` string |
| 2.6 offline exclusion | passed at once (CHAR: client construction was already lazy) | 15 passed | teeth: temporarily added a module-level boto3.client call -> `AssertionError: a client was built at import time`; reverted |
| 2.7/2.8 docs | n/a | runbook section "Changing the system prompt"; evidence template + README index | placeholders only |
| 2.9 gate | n/a | pytest 1376 passed; agent 33 passed 1 xfailed; ruff, format, mypy clean; hygiene 61 passed; `--help` exits 0 offline | |

Design notes: run_gate uses real build_prompt, parse_candidate, validate_message (injectable for tests); region passed explicitly (default us-east-2, AWS_DEFAULT_REGION override); ARN from RAIN_ALERT_AGENT_RUNTIME_ARN or `--from-lambda` (get_function_configuration on ferrenafe-rain-alert-cycle). Imports private `_fence_token` from agent_prompt for the default token factory.

## Batch 2 review fixes (task 2.10 annotated "review fixes applied"; checkbox left open)

| Fix | RED (observed) | Commit |
|---|---|---|
| `_mask` also masks a bare 12-digit id as `<account>` | `AssertionError: the bare id still in the masked text` | 76d1c3d |
| `main()` masks lookup/invoker-construction failures, one stderr line, exit 2 | the RuntimeError (with role ARN + account id) escaped `main()` | c966f04 |
| `--runs` fixed at 10 (other value: usage error, exit 2); 7/10 exit 1, 8/10 exit 0 | `DID NOT RAISE SystemExit` for `--runs 20` | 03d1711 |
| `--from-lambda` tests with a fake Lambda client; runbook uses `read -rs` for the ARN | characterization: passed at once (path already existed) | 8940efe |

Gotcha: a doc comment containing a 12-digit example id tripped tests/hygiene (it scans history); fixed by rewriting the 4 unpushed commits (filter-branch) and dropping refs/original + gc. Never write a literal 12-digit id, even as an example.

## Batch 3 (PR 3 offline, branch feat/agent-sp-3-system-prompt): tasks 3.1-3.9 [x]; 3.10+ not run

| Task | Test | RED (observed) | GREEN |
|---|---|---|---|
| 3.1/3.2 | `agent/tests/test_prompts.py` digit-free | `ModuleNotFoundError: No module named 'prompts'` | stub `"x"` passed it trivially (noted), triangulated by the full text |
| 3.3/3.4/3.4a | one test per rule (14 tests incl. date/city/checklist/markers/SENAMHI) | against stub `"x"`: 14 failed, each on its own phrase (e.g. `'If you mention SENAMHI, do it in one of two ways only' in 'x'`) | `agent/prompts.py` filled with D49 incl. SENAMHI bullet: 15 passed |
| 3.4 | `agent/tests/test_fence_contract.py` | xfail(strict) marker removed once prompts.py existed | passes (real pass, not xpass) |
| 3.5/3.6 | `test_the_built_agent_carries_the_system_prompt` | `assert None == 'You write one community...'` (system_prompt None) | `build_agent` passes `system_prompt=SYSTEM_PROMPT`; docstring/PROMPT_KEY comment updated |
| 3.7 | transitional old-rules user turn | characterization: passed at once | |
| 3.8 | hostile title only in user turn | characterization: passed at once | |
| side effect | `tests/architecture/test_layer_boundaries.py` pinned agent file list `["app.py","models.py"]` -> added `prompts.py` (intended; failed first) | | |

SYSTEM_PROMPT == D49 block from design.md: verified by script (regex-extracted block + "\n" == SYSTEM_PROMPT -> True). Gate: pytest 1385 passed; agent 52 passed; ruff, format, mypy clean.
