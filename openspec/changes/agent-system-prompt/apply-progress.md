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
