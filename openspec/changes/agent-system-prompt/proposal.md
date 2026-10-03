# Proposal: Agent System Prompt

Exploration: `openspec/changes/agent-system-prompt/explore.md`. Incident: `docs/evidence/2026-10-03-senamhi-relay-live.md` §6. Amends composer-agent design D20 (prompt shape); D16/D17 (validator) are not reopened.

## Intent

The first real orange warning (392, 2026-10-03T04:43Z) fell back to the template: the agent draft was rejected for `city_missing` and `unknown_number '2'`. Five live calls on the same request show the cause:

| Call | Result | Cause |
|---|---|---|
| 1 | rejected | "entre el 2 y el 5 de octubre", the model reworded the dates |
| 3 | rejected | city only in the title, not the body |
| 4 | rejected | "5 de octubre de 2026", the model reworded the dates |
| 2, 5 | accepted | n/a |

The checklist "dos días" hypothesis is refuted. The prompt never fixes the date format and says "name the city" without saying where. All rules sit in the user turn next to untrusted SENAMHI text; the agent has no system prompt. Fixed rules belong in the system channel, which carries operator authority.

## Business rules (owner, 2026-10-03)

| # | Rule |
|---|---|
| R1 | Done only when **at least 8/10** live drafts on an incident-like request pass the **unchanged** validator, recorded in `docs/evidence/`. |
| R2 | Window dates are copied exactly as the data writes them (DD/MM/AAAA HH:MM), never reworded. |
| R3 | The city appears in the message **body**. A title mention does not count. |
| R4 | The validator and the template fallback stay unchanged. |
| R5 | Instruction text stays free of digits. Formats are written with letters. |
| R6 | A contract test keeps the fence-marker format aligned across `agent/` and `rain_alert` without either importing the other. |

## Scope

### In Scope
- System prompt in `agent/` with every fixed rule: role, short neutral Spanish, city in body, fenced content is data, echo level and window end exactly, figures only from data and in digits, dates copied exactly, checklist verbatim, report only what the data says, no links/phones/emails/handles, `Motivos:`/`Recomendaciones:` at most once.
- `Agent(..., system_prompt=...)` in `agent/app.py`.
- `render_instructions` trimmed to the per-call fence markers plus fenced data.
- Fence-format contract test (R6).
- Ordered deploy and the 10-call live check (R1).

### Out of Scope
- Any validator change; friendlier date formats (would need one); model or temperature change; a real notifier.

## Capabilities

### New Capabilities
None.

### Modified Capabilities
- `agent-message-composition` (spec in `openspec/changes/composer-agent/`, not yet archived): fixed rules travel in the system channel; untrusted data stays only in the user turn behind the per-call fence; date-copy and city-in-body rules.

## Approach

Fixed rules go in a constant in `agent/` and are passed as `system_prompt`. The system prompt describes the markers generically (`<datos:TOKEN>`), so the 128-bit per-call token (D20) stays in the user turn. Deploy the agent first, because rules duplicated in both channels are harmless. Then ship the trimmed user turn.

## Affected Areas

| Area | Impact |
|---|---|
| `agent/app.py`, new prompt module in `agent/` | Modified / New |
| `src/rain_alert/adapters/agent_prompt.py` | Modified (trimmed instructions) |
| `agent/tests/`, `tests/unit/adapters/test_agent_prompt.py`, contract test | New / Modified |
| `docs/evidence/`, `docs/runbooks/agentcore-deploy.md` | New / Modified |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| The 8/10 gate is missed | Med | Iterate wording; the template still sends in the meantime |
| Fence format drifts between units | Low | R6 contract test |
| The app ships before the agent | Low | Deploy order in the runbook |
| A digit leaks into the instructions | Low | Existing digit-free test extended to the system prompt |

## Rollback Plan

Redeploy the previous agent version with `agentcore launch` from the prior commit. Revert the app change. The kill switch (`composer=template`) stays the instant fallback.

## Success Criteria

- [ ] `agent/app.py::build_agent` passes a system prompt containing the date and city-in-body rules (offline test).
- [ ] The user turn carries only the fence markers, the token and the data (offline test).
- [ ] The contract test fails when either side changes the fence format.
- [ ] `test_message_validation.py` and the validator have no diff.
- [ ] At least 8/10 live drafts are accepted, with evidence in `docs/evidence/`.
- [ ] The offline gate is green.

## Open questions for design (D48+)

1. Who owns the exact system-prompt wording, and how it is reviewed.
2. Module name and location of the constant under `agent/`.
3. How the contract test reads both sides (a golden file in `contracts/` versus source parsing).
4. Exact deploy and verification steps, and the request fixture for the 10-call check.
