# 2026-10-02 — The agent composer switched on (task O.8)

Region: `us-east-2`. Profile: `ferrenafe`.

Task O.8 of `openspec/changes/scheduled-cycle/tasks.md`: flip `composer=agent`
in SSM with no deploy, and record the first agent-composed message for the
owner to judge. Real cycles only compose a message on an authorized send, and
no real cycle had one at the time of the flip. The message recorded here
therefore comes from a local dry run against the **production** runtime, with
fixtures that clear a threshold. Nothing reached a recipient: the local build
has no notifier that can deliver a message.

## 1. Preconditions, read from the account

The deployed function already points at the right runtime and may invoke it:

```
function:           ferrenafe-rain-alert-cycle
env keys:           RAIN_ALERT_AGENT_RUNTIME_ARN, RAIN_ALERT_AGENT_TIMEOUT_S, RAIN_ALERT_SNAPSHOT_BUCKET
agent ARN (env):    arn:aws:bedrock-agentcore:us-east-2:<account>:runtime/ferrenaferainalert_rainalert-upwZ4M5wl5
runtime in region:  arn:aws:bedrock-agentcore:us-east-2:<account>:runtime/ferrenaferainalert_rainalert-upwZ4M5wl5  (READY, version 2)
arn_matches_runtime: true
IAM grant:          bedrock-agentcore:InvokeAgentRuntime on
                    .../runtime/ferrenaferainalert_rainalert-upwZ4M5wl5 and .../runtime/ferrenaferainalert_rainalert-upwZ4M5wl5/*
timeout:            120
```

## 2. The baseline: the template, same inputs

Fixtures: `tests/fixtures/senamhi/precipitation_coast_current.html` as
`senamhi.html`, `tests/fixtures/open_meteo/forecast_72h.json` as
`open_meteo.json`; `--now 2026-09-04T12:00:00+00:00`; a throwaway
`--state-file`.

```
uv run rain-alert-cycle --composer template --now 2026-09-04T12:00:00+00:00 \
  --state-file <tmp>/state-t.json --offline-fixtures <tmp>/fx
```

```
Level      imminent
Window     2026-09-04T05:00:00+00:00 → 2026-09-07T05:00:00+00:00
Reasons    - official SENAMHI warning: orange — PRECIPITACIONES EN LA COSTA NORTE Y SIERRA
Decision   SENT — new level for window (1 recipient(s))
Message    SENT [composer: template]
           title: Alerta de lluvias — Ferreñafe — riesgo inminente
           body:
             Ciudad: Ferreñafe
             Nivel: riesgo inminente
             Ventana: del 04/09/2026 00:00 al 07/09/2026 00:00 (hora local, America/Lima)
             Motivos:
             - Aviso oficial del SENAMHI, nivel naranja: PRECIPITACIONES EN LA COSTA NORTE Y SIERRA.
             Recomendaciones:
             - Almacena agua potable para al menos dos días.
             - Protege documentos y aparatos eléctricos por encima del nivel del piso.
             - Limpia canaletas, techos y desagües cercanos.
             - Asegura objetos sueltos y calaminas.
             - Ten a mano una linterna, un botiquín y los teléfonos de emergencia.
Notices    0 emitted
```

## 3. First agent attempt — the wrong runtime

The ARN was resolved locally with `boto3.client("bedrock-agentcore-control")`
under `AWS_PROFILE=ferrenafe AWS_REGION=us-east-2`. boto3 ignored
`AWS_REGION` and used the profile's own region, then `us-east-1`, so the run
invoked the stale `us-east-1` runtime (`...-7G7SdaFzJX`, version 1), not
production. Elapsed 17 s; it fell back to the template:

```
[OPERATOR NOTICE] agent_fallback_used — nothing was transmitted: this build has no notifier that can deliver a message
  subject: Agent composer fell back to the template: bad_status
  body:
    The deterministic template's message is being sent in place of the agent's draft, as of 2026-09-04T12:00:00+00:00. Fault: bad_status. Detail: RuntimeClientError: An error occurred (RuntimeClientError) when calling the InvokeAgentRuntime operation: Received error (500) from runtime. Please check your CloudWatch logs for more information.
```

The fallback behaved as designed. Two corrections followed on the same day:
the profile's default region was set to `us-east-2` in `~/.aws/config`, and
the stale `us-east-1` runtime was deleted (`DeleteAgentRuntime` →
`DELETING`; no `us-east-1` function referenced it).

## 4. Second agent attempt — the production runtime

The ARN was read from the deployed function's own environment, with
`AWS_DEFAULT_REGION=us-east-2` and the client region asserted first:

```
target: us-east-2 ferrenaferainalert_rainalert-upwZ4M5wl5
exit=0 elapsed=9s
```

```
[DRY-RUN ALERT] imminent — nothing was transmitted: this build has no notifier that can deliver a message
  would reach: 1 recipient(s)
  valid until: 2026-09-07T00:00:00-05:00
  title: Ferreñafe - Alerta inminente
  body:
    Alerta inminente en Ferreñafe por precipitaciones en la costa norte y sierra.

    Motivos:
    Advertencia oficial de precipitaciones en la costa norte y sierra.

    Recomendaciones:
    Almacena agua potable para al menos dos días.
    Protege documentos y aparatos eléctricos por encima del nivel del piso.
    Limpia canaletas, techos y desagües cercanos.
    Asegura objetos sueltos y calaminas.
    Ten a mano una linterna, un botiquín y los teléfonos de emergencia.
...
Message    SENT [composer: agent]
Notices    0 emitted
```

The 9 s includes both sources and the local cycle, not only the invocation.

## 5. The owner's judgement

Compared with the template, the agent's draft:

- **drops the window** — no start or end time;
- **drops the source** — "Advertencia oficial" names neither SENAMHI nor the
  warning colour (orange);
- **renames the level** — "Alerta inminente", not the domain's
  "riesgo inminente";
- keeps all five recommendations verbatim.

`validate_message` accepted it. The owner was shown both messages side by
side and **accepted the agent's wording** on 2026-10-02. Tightening the
validator to require the window and the source is recorded as a follow-up,
not done here.

## 6. The flip

```
PutParameter /ferrenafe/rain-alert/composer  Value=agent  Type=String  Overwrite=true
before:       template
after:        agent
new_version:  2
modified:     2026-10-02T20:56:12.646000+00:00
```

Read back through the application's own adapter, not only the SSM API:

```
AWS_PROFILE=ferrenafe uv run python -c "from rain_alert.adapters.ssm_config_repository import SsmConfigRepository; c = SsmConfigRepository().load(); print('composer:', c.composer, '| city:', c.city)"
composer: agent | city: Ferreñafe
```

No deploy. The function reads the parameter every cycle. Rollback is the same
call with `Value=template`.

## Still open

- The first **real** agent-composed message, from a scheduled cycle, has not
  happened yet; it needs an authorized send.
- `build_cloud_deps` wires `ConsoleNotifier`, so in the cloud an
  `AGENT_FALLBACK_USED` notice lands only in CloudWatch Logs.
- `docs/runbooks/agentcore-deploy.md` says the region comes from
  `AWS_REGION`/`AWS_DEFAULT_REGION`; section 3 shows `AWS_REGION` alone was
  not honoured here.
