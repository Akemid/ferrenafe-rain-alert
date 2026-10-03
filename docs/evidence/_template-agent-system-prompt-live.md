# Agent system prompt: live acceptance gate (TEMPLATE)

Copy to `docs/evidence/<date>-agent-system-prompt-live.md` and fill in. Replace
every `<...>`. Paste real output only; record failures too (design D53).

- **Date**: <YYYY-MM-DD>
- **Region**: us-east-2
- **Commit** (branch the runtime was launched from): <sha>
- **Runtime**: `arn:aws:bedrock-agentcore:us-east-2:<account>:runtime/<id>` (masked)
- **Round**: <1, 2 or 3; a fourth means escalate to the owner>

## Command

```bash
AWS_PROFILE=ferrenafe AWS_DEFAULT_REGION=us-east-2 \
RAIN_ALERT_AGENT_RUNTIME_ARN=<arn> \
  uv run python scripts/agent_live_acceptance.py --runs 10
```

## Outcomes

One row per run, all ten, in order. `Rules` are the validator rule ids the
script printed (`-` when accepted).

| Run | Rules | Accepted |
|---|---|---|
| 01 | <ids or -> | <yes/no> |
| 02 | | |
| 03 | | |
| 04 | | |
| 05 | | |
| 06 | | |
| 07 | | |
| 08 | | |
| 09 | | |
| 10 | | |

## Tally

`<k>/10 accepted (minimum 8)`: <PASS/FAIL>. Exit code: <0 or non-zero>.

## Raw output

```text
<paste the script's complete stdout, unedited>
```

## Notes

- What each rejection shows and which wording change it led to (test first):
  <none, or the failing test and the change>
- Checklist check (SSM equals `CHECKLIST`, if SSM was edited): <done / not needed>

## Verdict

<gate met: merge may proceed | gate not met: next round or escalate>
