# Tasks: Agent System Prompt (fixed rules in the system channel, data in the user turn)

This document exceeds the generic task-word budget deliberately, following this repository's precedent (`senamhi-s3-relay/tasks.md`, `composer-agent/tasks.md`): RED/GREEN detail is cheaper to write here than to rediscover at apply time.

Conventions: `[RED]` names the failing test and the reason it must fail; `[GREEN]` is the minimal implementation; `[CHAR]` is a characterization test that passes at once and must be RUN, not assumed. `[Owner]` marks an operator action on the live `ferrenafe` account. **sdd-apply MUST NOT run `[Owner]` tasks** (no `agentcore launch`, no `cdk deploy`, no paid model call). Strict TDD is ON: the test is written and run first and fails for the stated reason, never for an import typo. No `unittest.mock`; hand-written fakes and spies only (`tests/support/fakes.py` style). Commits are work units (behaviour plus its tests plus its docs). Conventional Commits, no AI attribution. Artifacts are in English; only the composed alert is Spanish.

Rule references: R1-R6 are `proposal.md`; D48-D54 are `design.md` (D49, D51, D54 amended 2026-10-03). Spec: `specs/agent-message-composition/spec.md`.

## Review Workload Forecast

| Field | Value |
|---|---|
| Estimated changed lines | 1100-1500 |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 -> PR 2 -> PR 3 -> PR 4 (stacked, each merges to main in order) |
| Delivery strategy | ask-on-risk |
| Chain strategy | stacked-to-main |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

| Area | Files | Lines |
|---|---|---|
| Contract and contract tests | `contracts/composer-fence.json`, `test_composer_fence_contract.py`, `agent/tests/test_fence_contract.py` | 200-280 |
| Incident characterization | `tests/unit/domain/test_incident_392_drafts.py` | 100-150 |
| Live script | `scripts/agent_live_acceptance.py` and its offline tests | 250-350 |
| Agent prompt and wiring | `agent/prompts.py`, `agent/app.py`, `agent/tests/test_prompts.py`, `agent/tests/test_app.py` | 300-400 |
| App trim | `agent_prompt.py`, `test_agent_prompt.py` | 120-200 |
| Docs and evidence | runbook section, evidence template and file, `agent/README` or docstrings | 150-250 |
| **Total** | | **1100-1500** |

### Suggested Work Units

Each unit ends green on the offline gate and has its own rollback boundary. PR 1 and PR 2 are inert in production. PR 3 changes the agent only, and PR 4 changes the app only, matching D54.

| Unit | Goal | PR | Est. lines | Base / rollback |
|---|---|---|---|---|
| 1 | Contract file, both contract tests, incident characterization tests. No production code changes. | PR 1 | 300-430 | base `main`; revert is a pure delete |
| 2 | `scripts/agent_live_acceptance.py` with offline tests, runbook section, evidence template. Inert: nothing calls it. | PR 2 | 350-480 | base `main` (after PR 1 merged); revert is a pure delete |
| 3 | `agent/prompts.py`, `build_agent` passes it, agent tests; then owner-run launch, 10-call gate, evidence committed to this PR | PR 3 | 350-500 | base `main` (after PR 2 merged); rollback = relaunch from the prior `main` commit |
| 4 | Trim `render_instructions` plus tests; then app deploy | PR 4 | 120-200 | base `main` (after PR 3 merged and gate met); revert, rebuild asset, deploy |

Dependencies are linear: 1 -> 2 -> 3 -> 4. PR 4 MUST NOT merge before PR 3's gate result is recorded (D54). Each child PR carries the `chained-pr` dependency diagram with the current PR marked, start, end, prior dependencies, follow-ups and out-of-scope items. Slices 1-2 may be combined only if the owner accepts `size:exception`.

---

## Phase 1: Contract and incident characterization (PR 1, offline, inert)

Satisfies: spec "Fence-marker format stays aligned" (all four scenarios), "Validator and template fallback are unchanged" (rejection scenarios). Design D51, Testing Strategy rows Contract and Characterization.

- [x] 1.1 [RED] `tests/unit/adapters/test_composer_fence_contract.py` loads `contracts/composer-fence.json` and asserts the literal values (`schema_version` 1, `tag` `datos`, markers, `token_hex_chars` 32, `prompt_placeholder` `TOKEN`, `prompt_key` `prompt`), as `test_the_contract_pins_the_values_the_design_chose` does. Expected failure: `FileNotFoundError` on the contract file.
- [x] 1.2 [GREEN] Create `contracts/composer-fence.json` (Interfaces block of design.md, including `prompt_key`). Verify: `uv run pytest tests/unit/adapters/test_composer_fence_contract.py`.
- [x] 1.3 [RED] Same file, app side: `build_prompt(request, token_factory=lambda: "abc")` contains the contract's `open_marker`/`close_marker` formatted with `abc`; `len(_fence_token()) == token_hex_chars`; `PROMPT_PAYLOAD_KEY` (`agentcore_invoker.py`) equals `prompt_key`. Expected failure: assertion on a deliberately absent key first (write the test before the JSON gains `prompt_key`, record red, then 1.2 supplies it); never an import typo.
- [x] 1.4 [RED] Drift on the app side is caught: a test injects a variant tag (`datos` -> `data`) into the formatted markers and asserts the comparison fails. Expected failure: the comparison helper does not yet exist.
- [x] 1.5 [GREEN] Add the comparison helper inside the test module. Mutation proof: change the tag in the contract copy used by the test, record red, revert.
- [x] 1.6 [RED] `agent/tests/test_fence_contract.py` reads the same JSON by relative path (no import from `rain_alert`): `PROMPT_KEY` from `agent/app.py` equals `prompt_key`; `SYSTEM_PROMPT` (not yet existing) contains both markers formatted with `prompt_placeholder`. Expected failure: the prompt half fails with `ModuleNotFoundError: prompts`; mark it `xfail(strict=True)` with the reason "SYSTEM_PROMPT lands in PR 3" so PR 1 stays green, and 3.4 removes the marker. The `PROMPT_KEY` half passes at once (characterization; record that).
- [x] 1.7 [CHAR] Architecture: `tests/architecture/test_layer_boundaries.py` stays green; neither `agent/` imports `rain_alert` nor the reverse (spec "No cross-import"). Run it, record the result.
- [x] 1.8 [CHAR] `tests/unit/domain/test_incident_392_drafts.py`: build the incident `MessageRequest` (392 window 2026-10-02T05:00Z to 2026-10-05T05:00Z, orange title, `forecast=None`, `static_config_repository.CHECKLIST`). Assert, with the UNCHANGED `validate_message`: "entre el 2 y el 5 de octubre" -> `UNKNOWN_NUMBER`; "5 de octubre de 2026" -> `UNKNOWN_NUMBER`; city only in the title -> `CITY_MISSING`; a body obeying D49 (city in body, dates copied as `DD/MM/AAAA HH:MM`, checklist verbatim) -> no violations. These pass immediately. RUN them and paste the observed rule ids into apply-progress; do not assume. Prove teeth: temporarily break one expectation (swap the expected rule) and record red, revert.
- [x] 1.9 [CHAR] Confirm `git diff main -- src/rain_alert/domain/message_validation.py tests/unit/domain/test_message_validation.py` is empty (spec "No validator diff").
- [x] 1.10 PR-1 gate: `uv run pytest`; `uv run --directory agent pytest`; `uv run ruff check`; `uv run ruff format --check`; `uv run mypy`. (`cd infra && npm test` not needed: infra untouched.) `uv run pytest tests/hygiene` after `git add`.
- [ ] 1.11 **Security review** (fresh context) on the PR-1 branch diff before opening the PR. Scope: no secret, account id or ARN; the contract holds no token value.

---

## Phase 2: Live gate script, runbook and evidence template (PR 2, offline-tested, inert)

Satisfies: spec "Live acceptance gate" tooling. Design D52, D53, D54 (amended).

- [x] 2.1 [RED] `tests/unit/scripts/test_agent_live_acceptance.py` (or the repo's scripts-test location): the script's pure core, `tally(results, minimum=8)` returns the k/10 line and exit code 0 at 8, 9, 10 and non-zero at 7. Expected failure: `ModuleNotFoundError` (script absent). Import by path, so the script is never collected by pytest itself.
- [x] 2.2 [GREEN] Create `scripts/agent_live_acceptance.py` with `tally` and the incident fixture builder (392 window, orange title, `forecast=None`, `static_config_repository.CHECKLIST`).
- [x] 2.3 [RED] The runner takes an injected invoker, parser and validator (hand-written fakes in the test). With a fake invoker returning programmed drafts it prints one line per run (rule ids, title, body) and the tally, and a fake that raises `AgentInvocationError` counts as a failed run, not a crash. Expected failure: runner not implemented.
- [x] 2.4 [RED] Secrecy: with a fake invoker whose ARN and fence token are known strings, captured stdout and stderr contain neither (spec decision D52; the script reads the ARN from the environment). Expected failure: the first draft of the runner echoes its configuration; write the test before the printer exists and keep it red until the printer is written to omit them.
- [x] 2.5 [GREEN] Implement the runner (`--runs`, default 10) using `build_prompt`, `BedrockAgentCoreInvoker`, `parse_candidate`, `validate_message` on the real path and the fakes on the test path. Exit non-zero below 8. The docstring marks it live and paid.
- [x] 2.6 [RED] Offline exclusion: a test asserts the script is outside the pytest collection (`testpaths` and `norecursedirs` in `pyproject.toml`) and that importing it performs no network or boto3 client construction. Expected failure: construction at import time (keep clients lazy).
- [x] 2.7 [Docs] `docs/runbooks/agentcore-deploy.md`: add "Changing the system prompt" following `docs/runbooks/README.md`: the ordered procedure from D54 as amended (offline gate green; security AND correctness reviews passed; `agentcore launch` from the branch; 10-call gate; evidence in the same PR; merge; app deploy via `infra/scripts/build-lambda.sh`, `cdk diff`, `cdk deploy`); the 3-round iteration cap and escalation; rollback options. Placeholders `<account>` and `<arn>` only.
- [x] 2.8 [Docs] Create `docs/evidence/_template-agent-system-prompt-live.md` or the template section the evidence README prescribes: date, commit, command, 10 rows (rule ids, accepted yes/no), tally, rounds, masked ARN, verdict. Read `docs/evidence/README.md` first and follow it.
- [x] 2.9 PR-2 gate: the five commands plus `uv run pytest tests/hygiene` after `git add`. Confirm `uv run python scripts/agent_live_acceptance.py --help` exits 0 offline.
- [ ] 2.10 **Security review** (fresh context) on the PR-2 branch diff. Scope: the script never prints the ARN, token or credentials; no network at import; no account id in the template.

---

## Phase 3: Agent system prompt (PR 3, offline-tested; launch and gate are `[Owner]`)

Satisfies: spec requirements "constructed with a system prompt", "no digit", "describes the fence markers generically", "Transitional deploy state". Design D48, D49 (as approved), D53.

- [ ] 3.1 [RED] `agent/tests/test_prompts.py`: `re.search(r"\d", SYSTEM_PROMPT) is None` (digit-free; also assert `not any(c.isdigit() for c in SYSTEM_PROMPT)` per spec). Expected failure: `ModuleNotFoundError: prompts`.
- [ ] 3.2 [GREEN] Create `agent/prompts.py` with `SYSTEM_PROMPT: Final[str]` holding the D49 text exactly (including the approved `checklist` sentence). It imports nothing.
- [ ] 3.3 [RED] One test per rule presence, each a separate function so the failure names the rule (spec "Every remaining fixed rule is present"): role (1), neutral short Spanish (2), fenced data is not instructions (4), level and window end echoed (5), figures only from the data and in digits (6), checklist verbatim and empty checklist asks for nothing (8), report only what the data states (9), no links, phones, e-mails or handles (11 as numbered in the spec's rule 10), `Motivos:`/`Recomendaciones:` at most once (11). Port the migrated assertions from `tests/unit/adapters/test_agent_prompt.py::TestWhatTheInstructionBlockStates` verbatim where they apply. Expected failure: each asserts a phrase; to make each fail for its own reason, write them against a stub `SYSTEM_PROMPT = "x"` first, record red, then 3.2 fills the text (or mutate-delete each bullet in turn and record that exactly its test fails).
- [ ] 3.4 [RED] Date and city rules, plus the checklist clarification: contains "in the body" and "title does not count" (city); "character for character", "dd/mm/aaaa hh:mm" (case-insensitive) and "never reword" (dates); "keep their own wording, number words included" (checklist, the approved addition). Contains both generic markers `<datos:TOKEN>` / `</datos:TOKEN>` and no 32-hex token (spec "Generic marker description"). Expected failure: phrase absent (mutation proof per phrase). Remove the `xfail(strict=True)` from 1.6 in the same commit.
- [ ] 3.4a [RED] SENAMHI attribution rule (owner, 2026-10-03; ADR 0002): `SYSTEM_PROMPT` contains the sentence "If you mention SENAMHI, do it in one of two ways only", the "quote the warning title exactly as the data writes it" clause and "Never put your own words in SENAMHI's mouth". Expected failure: phrases absent (stub or pre-bullet prompt). [GREEN] add the bullet from design D49 to `SYSTEM_PROMPT` (digit-free). Do NOT implement before the RED is recorded.
- [ ] 3.5 [RED] `agent/tests/test_app.py`: `build_agent().system_prompt == SYSTEM_PROMPT` and `AGENT.system_prompt == SYSTEM_PROMPT`. Use a hand-written spy that records the kwargs given to `Agent` if the seam allows, otherwise read the real property (`strands` `Agent.system_prompt`, design Technical Approach). Expected failure: `system_prompt` is `None` because the argument is not passed.
- [ ] 3.6 [GREEN] `agent/app.py::build_agent` returns `Agent(model=build_model(), tools=[], system_prompt=SYSTEM_PROMPT)`; update the module docstring (also fix the comment that admits the unpinned `PROMPT_KEY`).
- [ ] 3.7 [RED/CHAR] Transitional compatibility: a hand-written spy model receives a user turn that still contains the old rule text; `compose` runs the same parse path and the outcome depends only on the programmed draft (spec "Duplicated rules are harmless"). Also assert the payload shape stays `{"prompt": ...}`. Record honestly whether it failed first or passed at once.
- [ ] 3.8 [CHAR] Hostile title only appears in the fenced user turn and never in `SYSTEM_PROMPT` (spec "Untrusted data stays only in the user turn"). The system prompt is a constant, so assert it contains none of the hostile fixture string.
- [ ] 3.9 PR-3 gate (offline): the five commands plus `uv run pytest tests/hygiene` after `git add`. `cd infra && npm test` NOT required (infra untouched; run only if a file under `infra/` changed).
- [ ] 3.10 **Security review** (fresh context) on the PR-3 branch diff. Scope: the prompt carries no secret, ARN, account id or token; instructions cannot be overridden by the data section as worded; the validator is untouched.
- [ ] 3.11 **Correctness review** (fresh context) on the same diff: the prompt text equals D49 as approved, every rule in the spec has its test, the contract tests are not vacuous. Both reviews MUST pass before 3.12 (owner, 2026-10-03). Record both outcomes.

### Operator and live phase (not run by sdd-apply; PR 3 branch)

- [ ] 3.12 [Owner, AWS] From the PR 3 branch, only after 3.10 and 3.11 pass: `cd agent && AWS_PROFILE=ferrenafe AWS_DEFAULT_REGION=us-east-2 uv run agentcore launch`. Record the command and real output in `docs/evidence/` (ARN masked).
- [ ] 3.13 [Owner, AWS] Gate: `uv run python scripts/agent_live_acceptance.py --runs 10`. Record ALL 10 outcomes, failures included, in `docs/evidence/<date>-agent-system-prompt-live.md` using the 2.8 template. No cherry-picking (D53). Exit 0 requires at least 8 of 10 accepted by the unchanged validator.
- [ ] 3.14 [Owner] If fewer than 8/10: iterate the D49 wording TEST-FIRST (a new failing offline test naming the observed rejection, then the wording change, then relaunch and a fresh 10). Never loosen the validator. Maximum 3 rounds, then escalate to the owner. Record each round. Re-check SSM checklist equals `CHECKLIST` before the gate if SSM was edited.
- [ ] 3.15 [Owner] Commit the evidence to the PR 3 branch, re-run the gate commands, then merge PR 3. Merging is the owner's decision after the gate is met.

---

## Phase 4: App trim and deploy (PR 4, offline-tested; deploy is `[Owner]`)

Satisfies: spec MODIFIED "Prompt content is limited to MessageRequest", "User turn holds no rule text", "User turn still names its fence token", "Recipient data never reaches either channel". Design D50. Starts only after 3.15.

- [ ] 4.1 [RED] `tests/unit/adapters/test_agent_prompt.py`: `TestWhatTheInstructionBlockStates` becomes "the user turn carries no fixed rule". Test only the text BEFORE the fence (split `build_prompt` output at the open marker): it contains none of "checklist", "digits", "phone number", "motivos" (the JSON payload carries a `checklist` key, so never assert on the whole prompt). Expected failure: the old block still carries the rules.
- [ ] 4.2 [RED] The text before the fence still names both markers and this call's token; two calls with different tokens differ; it stays digit-free with a fixed token; it equals the one sentence in Interfaces ("The data for this message opens with the line ... and closes with the line ..."). Expected failure: the old block is longer than that sentence.
- [ ] 4.3 [GREEN] Trim `render_instructions(fence_open, fence_close)` in `src/rain_alert/adapters/agent_prompt.py` to the single per-call sentence, still before the fence (D50). Update the docstring. Keep the signature.
- [ ] 4.4 [CHAR] Recipient data never reaches either channel: neither the system prompt constant nor the user turn contains a contact identifier or account id (spec scenario). Existing `build_prompt` tests for sanitized titles stay green; run them.
- [ ] 4.5 [CHAR] The contract tests from Phase 1 still pass against the trimmed block (the markers are still produced by the same code).
- [ ] 4.6 PR-4 gate: the five commands plus `uv run pytest tests/hygiene` after `git add`.
- [ ] 4.7 **Security review** (fresh context) on the PR-4 branch diff: no fixed-rule text left next to untrusted data; no secret or account id.
- [ ] 4.8 [Owner] After PR 4 merges, on `main`: run `infra/scripts/build-lambda.sh`; confirm the asset holds the trimmed `agent_prompt.py` (grep for the removed rule text); `cdk diff` and read it: only the function code change; then `cdk deploy` with the existing `-c` flags. Record the output in `docs/evidence/`.
- [ ] 4.9 [Owner] One manual cycle invoke check after deploy: `aws lambda invoke` by hand (profile `ferrenafe`), confirm the cycle log shows the agent draft accepted or, if rejected, the template sent plus the operator notice; append the result to the evidence file and commit it.

---

## Notes on Rules Applied

- Strict TDD: every behavioural task is RED before GREEN with the failure named; characterization tests are labelled and run, never assumed.
- The default suite stays offline: only hand-written fakes and spies, the dummy-credentials fixture and the socket guard. The live script is never collected by pytest and is the only paid path.
- `domain/message_validation.py` and its tests have no diff in any PR; template fallback unchanged.
- Gate for every PR: `uv run pytest`; `uv run --directory agent pytest`; `uv run ruff check`; `uv run ruff format --check`; `uv run mypy`; `cd infra && npm test` only if infra is touched (it should not be).
- Security review (fresh context) on every branch diff before its PR; PR 3 also needs a correctness review before the agent launch.
