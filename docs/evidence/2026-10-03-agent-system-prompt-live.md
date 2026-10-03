# 2026-10-03 — Agent system prompt: live acceptance gate

- **Date**: 2026-10-03
- **Region**: us-east-2
- **Commit** (branch the runtime was deployed from): `7ad5930` on `feat/agent-sp-3-system-prompt` (PR #43), after both fresh-context reviews returned READY
- **Runtime**: `arn:aws:bedrock-agentcore:us-east-2:<account>:runtime/ferrenaferainalert_rainalert-upwZ4M5wl5` (masked), updated in place to **version 3** (`GetAgentRuntime`: `READY`, `lastUpdatedAt` 2026-10-03T06:34:17Z)
- **Round**: 1

## Deploy

The deploy used `agentcore deploy` (Node CLI 0.30.0, CDK-managed, target `default` in `us-east-2`), not the
`agentcore launch` flow the runbook described at the time (corrected in `agent-system-prompt` PR 4). `agentcore deploy --diff -y` beforehand showed a single
in-place change, so the runtime ARN the cycle Lambda holds stayed valid:

```
[~] AWS::BedrockAgentCore::Runtime ... AgentRuntimeArtifact.CodeConfiguration.Code.S3.Prefix
    [-] 8d46d875….zip
    [+] 0096ceb8….zip
```

```
AWS_PROFILE=ferrenafe AWS_DEFAULT_REGION=us-east-2 agentcore deploy -y
✓ Deployed to 'default' (stack: AgentCore-ferrenaferainalert-default)
  RuntimeId: ferrenaferainalert_rainalert-upwZ4M5wl5
```

## Command

```bash
AWS_PROFILE=ferrenafe AWS_DEFAULT_REGION=us-east-2 \
  uv run python scripts/agent_live_acceptance.py --runs 10 --from-lambda
```

## Baseline (runtime version 2, no system prompt), same command, earlier the same day

`6/10 accepted (minimum 8): FAIL`. All 4 rejections were `city_missing`: the body said "en la zona" or
"en la región", and Ferreñafe was named only in the title. Two of the accepted drafts reworded the window as
"desde el 02 de octubre hasta el 05 de octubre". The validator accepted those, because each figure traces to
the data.

## Outcomes (runtime version 3, with the system prompt)

| Run | Rules | Accepted |
|---|---|---|
| 01 | - | yes |
| 02 | - | yes |
| 03 | - | yes |
| 04 | - | yes |
| 05 | - | yes |
| 06 | - | yes |
| 07 | - | yes |
| 08 | - | yes |
| 09 | - | yes |
| 10 | - | yes |

## Tally

`10/10 accepted (minimum 8)`: **PASS**.

## Raw output

The script masks the runtime ARN, any `arn:aws…` string, bare account ids and the fence tokens. A grep of this
output for `arn:aws` and for twelve-digit runs found none.

```text
run 01: ACCEPTED rules=[-] title='Ferreñafe - Nivel inminente' body='Alerta de precipitaciones en Ferreñafe desde 02/10/2026 00:00 hasta 05/10/2026 00:00.\n\nAlmacena agua potable para al menos dos días.\nProtege documentos y aparatos eléctricos por encima del nivel del piso.\nLimpia canaletas, techos y desagües cercanos.\nAsegura objetos sueltos y calaminas.\nTen a mano una linterna, un botiquín y los teléfonos de emergencia.'
run 02: ACCEPTED rules=[-] title='Ferreñafe - Alerta Inmediata' body='Alerta inmediata en Ferreñafe por precipitaciones en la sierra norte y costa norte.\n\nAlmacena agua potable para al menos dos días.\nProtege documentos y aparatos eléctricos por encima del nivel del piso.\nLimpia canaletas, techos y desagües cercanos.\nAsegura objetos sueltos y calaminas.\nTen a mano una linterna, un botiquín y los teléfonos de emergencia.'
run 03: ACCEPTED rules=[-] title='Ferreñafe - Alerta Inmediata' body='Alerta inmediata en Ferreñafe por precipitaciones en la sierra norte y costa norte, vigente del 02/10/2026 00:00 al 05/10/2026 00:00.\n\nAlmacena agua potable para al menos dos días.\nProtege documentos y aparatos eléctricos por encima del nivel del piso.\nLimpia canaletas, techos y desagües cercanos.\nAsegura objetos sueltos y calaminas.\nTen a mano una linterna, un botiquín y los teléfonos de emergencia.'
run 04: ACCEPTED rules=[-] title='Ferreñafe: Alerta Inminente' body='Alerta inminente en Ferreñafe por precipitaciones. PRECIPITACIONES EN LA SIERRA NORTE Y COSTA NORTE.\n\nAlmacena agua potable para al menos dos días. Protege documentos y aparatos eléctricos por encima del nivel del piso. Limpia canaletas, techos y desagües cercanos. Asegura objetos sueltos y calaminas. Ten a mano una linterna, un botiquín y los teléfonos de emergencia.'
run 05: ACCEPTED rules=[-] title='Ferreñafe - Nivel inminent' body='Alerta de precipitaciones en Ferreñafe del 02/10/2026 00:00 al 05/10/2026 00:00.\n\nMotivos:\nPRECIPITACIONES EN LA SIERRA NORTE Y COSTA NORTE\n\nRecomendaciones:\nAlmacena agua potable para al menos dos días.\nProtege documentos y aparatos eléctricos por encima del nivel del piso.\nLimpia canaletas, techos y desagües cercanos.\nAsegura objetos sueltos y calaminas.\nTen a mano una linterna, un botiquín y los teléfonos de emergencia.'
run 06: ACCEPTED rules=[-] title='Ferreñafe - Alerta Inmediata' body='Alerta inmediata en Ferreñafe por precipitaciones en la sierra norte y costa norte.\n\nMotivos:\nPRECIPITACIONES EN LA SIERRA NORTE Y COSTA NORTE\n\nRecomendaciones:\nAlmacena agua potable para al menos dos días.\nProtege documentos y aparatos eléctricos por encima del nivel del piso.\nLimpia canaletas, techos y desagües cercanos.\nAsegura objetos sueltos y calaminas.\nTen a mano una linterna, un botiquín y los teléfonos de emergencia.'
run 07: ACCEPTED rules=[-] title='Ferreñafe - Alerta Inminente' body='Alerta inminente en Ferreñafe por precipitaciones.\n\nMotivos:\nPRECIPITACIONES EN LA SIERRA NORTE Y COSTA NORTE\n\nRecomendaciones:\nAlmacena agua potable para al menos dos días.\nProtege documentos y aparatos eléctricos por encima del nivel del piso.\nLimpia canaletas, techos y desagües cercanos.\nAsegura objetos sueltos y calaminas.\nTen a mano una linterna, un botiquín y los teléfonos de emergencia.'
run 08: ACCEPTED rules=[-] title='Ferreñafe - Alerta inmediata' body='Alerta inmediata en Ferreñafe por precipitaciones en la sierra norte y costa norte.\n\nAlmacena agua potable para al menos dos días.\nProtege documentos y aparatos eléctricos por encima del nivel del piso.\nLimpia canaletas, techos y desagües cercanos.\nAsegura objetos sueltos y calaminas.\nTen a mano una linterna, un botiquín y los teléfonos de emergencia.'
run 09: ACCEPTED rules=[-] title='Ferreñafe - Alerta inmediata' body='Alerta inmediata en Ferreñafe por precipitaciones en la sierra norte y costa norte.\n\nAlmacena agua potable para al menos dos días.\nProtege documentos y aparatos eléctricos por encima del nivel del piso.\nLimpia canaletas, techos y desagües cercanos.\nAsegura objetos sueltos y calaminas.\nTen a mano una linterna, un botiquín y los teléfonos de emergencia.'
run 10: ACCEPTED rules=[-] title='Ferreñafe - Alerta inminent' body='Alerta inminent en Ferreñafe por precipitaciones en la sierra norte y costa norte, válida desde 02/10/2026 00:00 hasta 05/10/2026 00:00.\n\nAlmacena agua potable para al menos dos días.\nProtege documentos y aparatos eléctricos por encima del nivel del piso.\nLimpia canaletas, techos y desagües cercanos.\nAsegura objetos sueltos y calaminas.\nTen a mano una linterna, un botiquín y los teléfonos de emergencia.'
10/10 accepted (minimum 8): PASS
```

## Notes

- Every body names Ferreñafe. Every date that appears is copied exactly (`02/10/2026 00:00`). No draft
  attributes a paraphrase to SENAMHI: runs 04 to 07 quote the warning title verbatim, in capitals.
- **Quality issue not caught by the validator.** Runs 05 and 10 write the level as "inminent" ("Nivel
  inminent", "Alerta inminent") instead of "inminente". Several titles say "Alerta inmediata" rather than the
  level's label, "riesgo inminente". The validator does not check spelling or the level label, so these
  pass. They are not unsafe, but they cost credibility in a civil-protection message. Recorded as a
  follow-up: a rule (and gate) that copies the level label exactly as the data gives it.
- Checklist check: SSM was not edited, so not needed.

## Verdict

**The gate is met (10/10 ≥ 8/10).** PR #43 may merge after #40 and #41. The level-label follow-up is
tracked separately and does not block it.

## Round 1b — the trimmed user turn (PR 4), before merge

The gate above ran with the fixed rules duplicated in the user turn, because the app had not been trimmed
yet. PR 4 removes them, so the agent depends on its system prompt alone. The live script builds the prompt
with the repository's own `build_prompt`, so running it on `feat/agent-sp-4-app-trim` (commit `2dd6e46`)
measures exactly what production will send after PR 4. It ran against the same runtime, version 3. Nothing
was deployed.

`10/10 accepted (minimum 8)`: **PASS**. The level-label issue persists: runs 03, 06 and 10 write
"inminent". The follow-up stands.

```text
run 01: ACCEPTED rules=[-] title='Ferreñafe - Nivel inminente' body='Alerta de precipitaciones en Ferreñafe.\n\nSe espera lluvia entre 02/10/2026 00:00 y 05/10/2026 00:00.\n\nAlmacena agua potable para al menos dos días.\nProtege documentos y aparatos eléctricos por encima del nivel del piso.\nLimpia canaletas, techos y desagües cercanos.\nAsegura objetos sueltos y calaminas.\nTen a mano una linterna, un botiquín y los teléfonos de emergencia.'
run 02: ACCEPTED rules=[-] title='Ferreñafe: Alerta Inmediata' body='Alerta inmediata por precipitaciones en Ferreñafe.\n\nPRECIPITACIONES EN LA SIERRA NORTE Y COSTA NORTE\n\nVigencia: 02/10/2026 00:00 a 05/10/2026 00:00\n\nRecomendaciones:\nAlmacena agua potable para al menos dos días.\nProtege documentos y aparatos eléctricos por encima del nivel del piso.\nLimpia canaletas, techos y desagües cercanos.\nAsegura objetos sueltos y calaminas.\nTen a mano una linterna, un botiquín y los teléfonos de emergencia.'
run 03: ACCEPTED rules=[-] title='Ferreñafe - Nivel inminent' body='Alerta de precipitaciones en Ferreñafe del 02/10/2026 00:00 al 05/10/2026 00:00.\n\nPRECIPITACIONES EN LA SIERRA NORTE Y COSTA NORTE\n\nAlmacena agua potable para al menos dos días.\nProtege documentos y aparatos eléctricos por encima del nivel del piso.\nLimpia canaletas, techos y desagües cercanos.\nAsegura objetos sueltos y calaminas.\nTen a mano una linterna, un botiquín y los teléfonos de emergencia.'
run 04: ACCEPTED rules=[-] title='Ferreñafe: Alerta inminente' body='Ferreñafe está bajo alerta inminente por precipitaciones entre 02/10/2026 00:00 y 05/10/2026 00:00.\n\nMotivos:\nPRECIPITACIONES EN LA SIERRA NORTE Y COSTA NORTE\n\nRecomendaciones:\nAlmacena agua potable para al menos dos días.\nProtege documentos y aparatos eléctricos por encima del nivel del piso.\nLimpia canaletas, techos y desagües cercanos.\nAsegura objetos sueltos y calaminas.\nTen a mano una linterna, un botiquín y los teléfonos de emergencia.'
run 05: ACCEPTED rules=[-] title='Ferreñafe - Alerta inmediata' body='Alerta inmediata en Ferreñafe por PRECIPITACIONES EN LA SIERRA NORTE Y COSTA NORTE, válida desde 02/10/2026 00:00 hasta 05/10/2026 00:00.\n\nAlmacena agua potable para al menos dos días.\nProtege documentos y aparatos eléctricos por encima del nivel del piso.\nLimpia canaletas, techos y desagües cercanos.\nAsegura objetos sueltos y calaminas.\nTen a mano una linterna, un botiquín y los teléfonos de emergencia.'
run 06: ACCEPTED rules=[-] title='Ferreñafe - Alerta inminent' body='Alerta inminent en Ferreñafe por precipitaciones en la sierra norte y costa norte, válida desde 02/10/2026 00:00 hasta 05/10/2026 00:00.\n\nMotivos:\nPRECIPITACIONES EN LA SIERRA NORTE Y COSTA NORTE\n\nRecomendaciones:\nAlmacena agua potable para al menos dos días.\nProtege documentos y aparatos eléctricos por encima del nivel del piso.\nLimpia canaletas, techos y desagües cercanos.\nAsegura objetos sueltos y calaminas.\nTen a mano una linterna, un botiquín y los teléfonos de emergencia.'
run 07: ACCEPTED rules=[-] title='Ferreñafe - Alerta inmediata' body='Ferreñafe está bajo alerta inmediata por precipitaciones en la sierra norte y costa norte, válida desde 02/10/2026 00:00 hasta 05/10/2026 00:00.\n\nMotivos:\nPRECIPITACIONES EN LA SIERRA NORTE Y COSTA NORTE\n\nRecomendaciones:\nAlmacena agua potable para al menos dos días.\nProtege documentos y aparatos eléctricos por encima del nivel del piso.\nLimpia canaletas, techos y desagües cercanos.\nAsegura objetos sueltos y calaminas.\nTen a mano una linterna, un botiquín y los teléfonos de emergencia.'
run 08: ACCEPTED rules=[-] title='Ferreñafe: Alerta Inminente' body='Ferreñafe está bajo alerta inminente por precipitaciones. La alerta rige desde 02/10/2026 00:00 hasta 05/10/2026 00:00.\n\nMotivos:\nPRECIPITACIONES EN LA SIERRA NORTE Y COSTA NORTE\n\nRecomendaciones:\nAlmacena agua potable para al menos dos días.\nProtege documentos y aparatos eléctricos por encima del nivel del piso.\nLimpia canaletas, techos y desagües cercanos.\nAsegura objetos sueltos y calaminas.\nTen a mano una linterna, un botiquín y los teléfonos de emergencia.'
run 09: ACCEPTED rules=[-] title='Ferreñafe: Alerta Inmediata' body='Alerta inmediata en Ferreñafe por PRECIPITACIONES EN LA SIERRA NORTE Y COSTA NORTE.\n\nVigencia: 02/10/2026 00:00 a 05/10/2026 00:00\n\nRecomendaciones:\nAlmacena agua potable para al menos dos días.\nProtege documentos y aparatos eléctricos por encima del nivel del piso.\nLimpia canaletas, techos y desagües cercanos.\nAsegura objetos sueltos y calaminas.\nTen a mano una linterna, un botiquín y los teléfonos de emergencia.'
run 10: ACCEPTED rules=[-] title='Ferreñafe: Nivel inminent' body='Alerta por precipitaciones en Ferreñafe del 02/10/2026 00:00 al 05/10/2026 00:00.\n\nPRECIPITACIONES EN LA SIERRA NORTE Y COSTA NORTE\n\nAlmacena agua potable para al menos dos días.\nProtege documentos y aparatos eléctricos por encima del nivel del piso.\nLimpia canaletas, techos y desagües cercanos.\nAsegura objetos sueltos y calaminas.\nTen a mano una linterna, un botiquín y los teléfonos de emergencia.'
10/10 accepted (minimum 8): PASS
```

## App deploy (PR 4, after merge)

On `main` at `94a2140`, `infra/scripts/build-lambda.sh` rebuilt the asset ("no compiled artifact found"). The
rebuilt asset holds the trimmed user turn: its new sentence is present and the old rule text is absent. `cdk diff ScheduledCycleStack`
showed exactly one change, the `ScheduledCycleFunction` code (`[~] Code.S3Key`). It showed no schedule,
relay or IAM change, because both switches now live in `infra/cdk.json`. `cdk deploy` published the new code
in 19 s: `CodeSha256` prefix `I6nh2cDgzRIW`, LastModified 2026-10-03T06:50:00Z.

Manual cycle invoke (`InvocationType=Event`, 202) at 06:50:31Z, 2.8 s:

```
{"event":"senamhi_relay","degraded":0,"reason":null,"version_id":"ttPfHO0QZArAmSND9UGNB0EyzD_5R6eZ","last_modified":"2026-10-03T06:00:04Z","age_seconds":3029,"skew_seconds":-4}
senamhi_status: available   level: imminent   (warning level: orange)
decision: "not an escalation over prior level imminent", send: false
```

The alert for this window was recorded at 04:43Z, so deduplication correctly declined to resend, and the
agent was not invoked on this run. The agent path with the trimmed user turn is covered by Round 1b above
(10/10). The relay object the cycle read was pushed by the LaunchAgent at 06:00:04Z on its own. This is the
first observed scheduled `StartCalendarInterval {Minute: 0}` push, not the `RunAtLoad` one.

