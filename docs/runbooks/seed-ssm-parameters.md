# Seeding the seven SSM parameters

**Owner task O.2.** Run once, before the first `cdk deploy ScheduledCycleStack`.
Region `us-east-2`, profile `ferrenafe`.

## Why these are not in the CDK stack

Design decision D32. If CDK owned these parameters, an operator's calibration
would be silently restored to the committed default by the next unrelated
`cdk deploy` — a threshold raised after a false positive would revert without
anyone touching it, and nothing would report the change.

So `ScheduledCycleStack` grants `ssm:GetParameters` on these seven exact names
and creates none of them. `infra/test/scheduled-cycle-stack.test.ts` asserts
that absence: zero `AWS::SSM::Parameter` resources in the synthesized template.

The cost is this page. The seven names must match
`src/rain_alert/adapters/ssm_config_repository.py::_PARAMETER_NAMES` exactly —
a one-character difference is `AccessDenied` on every cycle and on no test.

## The values

Taken from `StaticConfigRepository`, the local adapter these replace, on
2026-09-30. `composer` is deliberately `template` for the first deploy: the
agent is switched on later, in O.8, by changing this one parameter with no
redeploy.

Four values are **not** here because they are code constants, never
operator-editable: `city` (`Ferreñafe`), `city_slug` (`ferrenafe`, the
DynamoDB partition key — a typo would strand the dedup history and re-alert a
community already warned), `region` (`Lambayeque`) and `timezone`
(`America/Lima`).

## The commands

```bash
export AWS_PROFILE=ferrenafe
export AWS_REGION=us-east-2
P=/ferrenafe/rain-alert

aws ssm put-parameter --name "$P/location" --type String --overwrite \
  --description "Coordinates and their provenance. The source must never be inferred." \
  --value '{"latitude": -6.636005, "longitude": -79.78986, "source": "public_reference"}'

aws ssm put-parameter --name "$P/thresholds" --type String --overwrite \
  --description "Risk thresholds for prepare and imminent." \
  --value '{"prepare_mm_48h": 9.5, "prepare_probability_pct": 60, "prepare_probability_pct_degraded": 70, "prepare_warning_levels": ["yellow", "orange", "red"], "imminent_mm_24h": 20.0, "imminent_probability_pct": 70, "imminent_warning_levels": ["orange", "red"]}'

aws ssm put-parameter --name "$P/checklist" --type String --overwrite \
  --description "Preparedness checklist, Spanish, reaches residents verbatim." \
  --value '["Almacena agua potable para al menos dos días.", "Protege documentos y aparatos eléctricos por encima del nivel del piso.", "Limpia canaletas, techos y desagües cercanos.", "Asegura objetos sueltos y calaminas.", "Ten a mano una linterna, un botiquín y los teléfonos de emergencia."]'

aws ssm put-parameter --name "$P/active-channel" --type String --overwrite \
  --description "The only channel a Notifier exists for today." \
  --value 'console'

aws ssm put-parameter --name "$P/composer" --type String --overwrite \
  --description "template or agent. The rollback switch: changing this needs no redeploy." \
  --value 'template'

aws ssm put-parameter --name "$P/forecast-hours" --type String --overwrite \
  --value '48'

aws ssm put-parameter --name "$P/dedup-lookback-hours" --type String --overwrite \
  --value '72'
```

### Why `--type String` and not `StringList`

`StringList` returns a comma-separated string with no spaces, so an item
containing a comma is indistinguishable from a separator. Two of the five
checklist items contain one:

- `Limpia canaletas, techos y desagües cercanos.`
- `Ten a mano una linterna, un botiquín y los teléfonos de emergencia.`

Under `StringList` a resident in a flood would read those as four separate
instructions. The checklist is a JSON array inside a `String`.

### Why not `SecureString`

`ssm_config_repository.py` calls `GetParameters` with `WithDecryption=False`,
which keeps `kms:Decrypt` off the Lambda's grant. A parameter created as
`SecureString` would return ciphertext, and the cycle would fail with
"is not valid JSON" rather than a permission error. None of these seven hold
a secret.

## Verifying before the deploy

The parameters are what the Lambda will read, so check them the way it does —
by name, not by listing the path:

```bash
aws ssm get-parameters --names \
  "$P/location" "$P/thresholds" "$P/checklist" "$P/active-channel" \
  "$P/composer" "$P/forecast-hours" "$P/dedup-lookback-hours" \
  --query '{found: Parameters[].Name, missing: InvalidParameters}'
```

`missing` must be empty. A name in that list is the `AccessDenied`-on-every-
cycle failure this page exists to prevent, and it surfaces here instead.

Then confirm the application's own parser accepts them, before any Lambda
exists. There is no CLI flag for this: `rain-alert-cycle` builds
`StaticConfigRepository`, and `SsmConfigRepository` is reached only through
`build_cloud_deps`, which only the Lambda handler calls. So exercise the
adapter directly:

```bash
uv run python -c "
from rain_alert.adapters.ssm_config_repository import SsmConfigRepository
c = SsmConfigRepository().load()
print('coordinates :', c.coordinates.latitude, c.coordinates.longitude, c.coordinates_source.value)
print('composer    :', c.composer.value)
print('channel     :', c.active_channel)
print('checklist   :', len(c.checklist), 'items')
"
```

This reads the seven parameters with the real credentials — it writes nothing.
It is the only step on this page that reaches AWS to read rather than to
write, and it is worth doing, because it is the difference between "the
parameters exist" and "the application can use them".

If `SsmConfigRepository` raises, it names the parameter and the reason and
refuses rather than guessing: a missing `source` on `location`, an absent
threshold field, a checklist that is not a JSON array of strings. That
strictness is deliberate — an operator relying on a seeding step that half
succeeded needs to hear about it now, not at the first real alert.
