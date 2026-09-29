"""`SsmConfigRepository` — the `ConfigRepository` over SSM Parameter Store
(design.md D28, D32 as amended by fix round 1; `cloud-configuration` spec).

**Fix round 1, MUST item 2 — `GetParameters` by exact name, never
`GetParametersByPath`.** Design D32 originally chose `GetParametersByPath`
over one shared prefix specifically so the IAM grant could be a single
resource. The security reviewer's point is the one that matters more: a
single resource that is a **wildcard over the whole prefix** grants
`ssm:GetParametersByPath` on `parameter/ferrenafe/rain-alert/*` forever,
including anything an operator parks there later — the grant is written once
in Phase 3 and a later adapter change would mean a later, separate policy
change. There are exactly seven parameters and `GetParameters` accepts up to
ten names per call, so this adapter now requests the seven exact names and
Phase 3's IAM grant will list seven exact ARNs instead of one prefix. A
missing parameter now shows up explicitly in the response's
`InvalidParameters` list, named, rather than as a silent absence `_require`
would otherwise have to infer later from a plain "not present" check.

**Layout, one parameter per value object**, all seven read by exact name in
a single `GetParameters` call:

| Name | Shape | Who writes it |
|---|---|---|
| `location` | `{"latitude": …, "longitude": …, "source": "operator_supplied"}` | operator |
| `thresholds` | the seven `RiskThresholds` fields as JSON | operator |
| `checklist` | a JSON **array** of strings | operator |
| `active-channel` | `"console"` | operator |
| `composer` | `"template"` \\| `"agent"` | operator |
| `forecast-hours` | an integer | operator |
| `dedup-lookback-hours` | an integer | operator |

`city`, `city_slug`, `region` and `timezone` stay code constants, identical
in shape to `StaticConfigRepository`'s split — `city_slug` above all,
because it is the DynamoDB partition-key input and an operator editing it
strands the entire dedup history under the old key (`alert-persistence`
spec).

**Why `checklist` is a JSON array in a `String` parameter, and never a
`StringList`.** `StringList` is comma-delimited with no escaping. Two of the
five shipped checklist items contain commas — *"Limpia canaletas, techos y
desagües cercanos."* and *"Ten a mano una linterna, un botiquín y los
teléfonos de emergencia."* — so as a `StringList` those two items would each
arrive split into two fragments, and the fragments would render into a
resident's message body as if they were separate instructions. Nothing would
raise; the community would simply receive a subtly wrong safety checklist. A
JSON array in a `String` parameter has an escaping rule (JSON string
escaping), so it does not have this failure. This must be readable here, not
only in `openspec/`, so a later "simplification" back to `StringList` is
caught by whoever reads this adapter.

**Every field raises rather than defaulting.** Matching
`StaticConfigRepository`'s existing strictness on `RAIN_ALERT_LAT`/`LON` and
`RAIN_ALERT_COMPOSER`: a missing, empty or unparseable operator-editable
field must not silently proceed with a built-in default, because a default
forecasts for a point or a threshold nobody chose while claiming to be
configured.

**`coordinates_source` is never inferred.** It must be present in `location`
alongside `latitude`/`longitude`, every time — there is no default-coordinate
path in SSM (unlike the local CLI's env-var fallback), so a missing `source`
always raises.

**No credentials offline, exactly like `BedrockAgentCoreInvoker` and
`DynamoDbAlertRepository` (D28).** The client is injected and built lazily on
first `load`, so wiring never constructs a client and the default test suite
needs no region and no credentials.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import boto3
from botocore.config import Config

from rain_alert.domain.config import AlertConfig, CoordinatesSource, RiskThresholds
from rain_alert.domain.sanitize import sanitize_source_text
from rain_alert.domain.values import ComposerName, Coordinates, WarningLevel

#: A golden contract file (design.md D31, D32) also carries this same prefix
#: as a later phase's own deliverable: the Python constant here will be
#: asserted against it, and the seven CDK IAM grant ARNs (fix round 1 — no
#: longer one prefix-wildcard resource) will each be asserted to start with
#: it, so a one-character disagreement between the two would be
#: `AccessDenied` on every cycle and on no test in *this* file. Not cited by
#: path here because that file does not exist in this slice yet.
SSM_PATH_PREFIX = "/ferrenafe/rain-alert/"

_LOCATION_NAME = f"{SSM_PATH_PREFIX}location"
_THRESHOLDS_NAME = f"{SSM_PATH_PREFIX}thresholds"
_CHECKLIST_NAME = f"{SSM_PATH_PREFIX}checklist"
_ACTIVE_CHANNEL_NAME = f"{SSM_PATH_PREFIX}active-channel"
_COMPOSER_NAME = f"{SSM_PATH_PREFIX}composer"
_FORECAST_HOURS_NAME = f"{SSM_PATH_PREFIX}forecast-hours"
_DEDUP_LOOKBACK_HOURS_NAME = f"{SSM_PATH_PREFIX}dedup-lookback-hours"

#: The exact seven names this adapter requests, by `GetParameters` (fix
#: round 1) — never a wildcard read over the prefix. `GetParameters` accepts
#: up to ten names per call, well above these seven, so no pagination is
#: needed either. A stray parameter parked under the same prefix at, say,
#: `city_slug` is never requested at all, which is strictly stronger than
#: filtering it out of a wildcard response after the fact (task 1.22's
#: original mechanism, superseded here).
_PARAMETER_NAMES: tuple[str, ...] = (
    _LOCATION_NAME,
    _THRESHOLDS_NAME,
    _CHECKLIST_NAME,
    _ACTIVE_CHANNEL_NAME,
    _COMPOSER_NAME,
    _FORECAST_HOURS_NAME,
    _DEDUP_LOOKBACK_HOURS_NAME,
)

#: Code constants — never SSM-sourced (`cloud-configuration` spec).
CITY = "Ferreñafe"
CITY_SLUG = "ferrenafe"
REGION = "Lambayeque"
TIMEZONE = "America/Lima"

#: The one channel a `Notifier` exists for today (design.md's open question
#: on whether `active_channel` should be operator-editable at all keeps it,
#: strictly validated against this one-member list).
_ALLOWED_ACTIVE_CHANNELS = ("console",)


def _require(raw: Mapping[str, str], name: str) -> str:
    value = raw.get(name)
    if not value:
        raise ValueError(f"SSM parameter {name!r} is missing or empty")
    return value


def _echo(value: Any) -> str:
    """A bounded, sanitized rendering of an operator-supplied value, safe to
    interpolate into an exception message that reaches CloudWatch Logs (fix
    round 1, SHOULD item 7).

    Without this, an operator who pastes a credential into the wrong
    parameter by mistake would have that mistake durably logged in full —
    up to several kilobytes, verbatim. `_require` already gets this right
    for the missing/empty case by naming only the parameter, never the
    value; every other raise below that would otherwise echo a raw value
    goes through this instead.
    """
    return sanitize_source_text(str(value))[:80]


def _parse_json_document(raw_value: str, name: str) -> Any:
    try:
        return json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"SSM parameter {name!r} is not valid JSON: {exc}") from exc


def _parse_location(raw_value: str) -> tuple[Coordinates, CoordinatesSource]:
    document = _parse_json_document(raw_value, _LOCATION_NAME)
    if not isinstance(document, dict):
        raise ValueError(f"SSM parameter {_LOCATION_NAME!r} must be a JSON object")
    try:
        latitude = float(document["latitude"])
        longitude = float(document["longitude"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"SSM parameter {_LOCATION_NAME!r} must carry numeric latitude/longitude") from exc
    if "source" not in document:
        # MUST NOT be inferred (e.g. defaulted to `PUBLIC_REFERENCE`) --
        # `cloud-configuration` spec, "Coordinates and their provenance
        # travel together". Verified by mutation: temporarily replacing this
        # raise with `document = {**document, "source": "public_reference"}`
        # makes `test_missing_source_raises_rather_than_defaulting` fail with
        # "DID NOT RAISE ValueError" (recorded in apply-progress).
        raise ValueError(
            f"SSM parameter {_LOCATION_NAME!r} must carry an explicit 'source'; "
            "coordinates_source must never be inferred"
        )
    try:
        source = CoordinatesSource(document["source"])
    except ValueError as exc:
        raise ValueError(
            f"SSM parameter {_LOCATION_NAME!r}.source must be one of "
            f"{[member.value for member in CoordinatesSource]}, got {_echo(document['source'])!r}"
        ) from exc
    coordinates = Coordinates(latitude=latitude, longitude=longitude)  # range-checked by __post_init__
    return coordinates, source


def _parse_warning_levels(raw_levels: Any, field: str) -> frozenset[WarningLevel]:
    if not isinstance(raw_levels, list):
        raise ValueError(f"SSM parameter {_THRESHOLDS_NAME!r}.{field} must be a JSON array")
    try:
        return frozenset(WarningLevel(level) for level in raw_levels)
    except ValueError as exc:
        raise ValueError(f"SSM parameter {_THRESHOLDS_NAME!r}.{field} holds an unrecognized warning level") from exc


def _parse_thresholds(raw_value: str) -> RiskThresholds:
    document = _parse_json_document(raw_value, _THRESHOLDS_NAME)
    if not isinstance(document, dict):
        raise ValueError(f"SSM parameter {_THRESHOLDS_NAME!r} must be a JSON object")
    try:
        return RiskThresholds(
            prepare_mm_48h=float(document["prepare_mm_48h"]),
            prepare_probability_pct=int(document["prepare_probability_pct"]),
            prepare_probability_pct_degraded=int(document["prepare_probability_pct_degraded"]),
            prepare_warning_levels=_parse_warning_levels(document["prepare_warning_levels"], "prepare_warning_levels"),
            imminent_mm_24h=float(document["imminent_mm_24h"]),
            imminent_probability_pct=int(document["imminent_probability_pct"]),
            imminent_warning_levels=_parse_warning_levels(
                document["imminent_warning_levels"], "imminent_warning_levels"
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"SSM parameter {_THRESHOLDS_NAME!r} is missing a field or holds an invalid value: {_echo(exc)}"
        ) from exc


def _parse_checklist(raw_value: str) -> tuple[str, ...]:
    document = _parse_json_document(raw_value, _CHECKLIST_NAME)
    if not isinstance(document, list) or not all(isinstance(item, str) for item in document):
        raise ValueError(f"SSM parameter {_CHECKLIST_NAME!r} must be a JSON array of strings")
    if len(document) == 0:
        raise ValueError(f"SSM parameter {_CHECKLIST_NAME!r} must not be empty")
    return tuple(document)


def _parse_active_channel(raw_value: str) -> str:
    if raw_value not in _ALLOWED_ACTIVE_CHANNELS:
        raise ValueError(
            f"SSM parameter {_ACTIVE_CHANNEL_NAME!r} must be one of {_ALLOWED_ACTIVE_CHANNELS}, "
            f"got {_echo(raw_value)!r}"
        )
    return raw_value


def _parse_composer(raw_value: str) -> ComposerName:
    try:
        return ComposerName(raw_value)
    except ValueError as exc:
        raise ValueError(
            f"SSM parameter {_COMPOSER_NAME!r} must be one of {[member.value for member in ComposerName]}, "
            f"got {_echo(raw_value)!r}"
        ) from exc


def _parse_positive_int(raw_value: str, name: str) -> int:
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"SSM parameter {name!r} must be an integer, got {_echo(raw_value)!r}") from exc
    if value <= 0:
        raise ValueError(f"SSM parameter {name!r} must be positive, got {value}")
    return value


#: Fix round 1, SHOULD item 4. Mirrors `s3_snapshot_publisher.py`'s and
#: `dynamodb_alert_repository.py`'s own bound and rationale: a bare
#: `boto3.client("ssm")` inherits botocore's defaults -- 60 s connect, 60 s
#: read, legacy retries with `max_attempts: 5` -- and a single small
#: `GetParameters` call over seven names is exactly the kind of operation
#: that either answers in tens of milliseconds or is not going to. This read
#: runs at the very start of `build_cloud_deps` (change 3), before anything
#: else in the cycle, so a hang here is a hang before the cycle can even
#: begin.
CONNECT_TIMEOUT = 3.0
READ_TIMEOUT = 5.0

#: `total_max_attempts`, never `max_attempts`: the two mean opposite things.
#: See `agentcore_invoker.py`'s module docstring, which quotes
#: `botocore.config.Config` on this at length.
TOTAL_MAX_ATTEMPTS = 2


class SsmConfigRepository:
    """`ConfigRepository` over `boto3`'s low-level `ssm` client.

    Args:
        client: Injected for testability (D28). `None` (the default) defers
            construction to the first `load` call, so the offline suite
            never builds a real client and never needs a region configured.

    Memoizes the parsed result on the instance after the first successful
    load (design.md D29's "one cycle, one configuration" correctness
    argument, not merely a cost optimization): an operator editing SSM
    mid-cycle must not have half of it applied to the same cycle.
    """

    def __init__(self, *, client: Any | None = None) -> None:
        self._client = client
        self._cached: AlertConfig | None = None

    def _resolved_client(self) -> Any:
        if self._client is None:
            self._client = boto3.client(
                "ssm",
                config=Config(
                    connect_timeout=CONNECT_TIMEOUT,
                    read_timeout=READ_TIMEOUT,
                    retries={"total_max_attempts": TOTAL_MAX_ATTEMPTS},
                ),
            )
        return self._client

    def load(self) -> AlertConfig:
        if self._cached is not None:
            return self._cached
        raw = self._fetch_parameters()
        self._cached = self._parse(raw)
        return self._cached

    def _fetch_parameters(self) -> dict[str, str]:
        """One `GetParameters` call over the seven exact names (fix round 1
        — never `GetParametersByPath` over the prefix). `InvalidParameters`
        is checked and raised on explicitly, named, rather than left for
        `_require` to infer later from a plain absence in the returned
        dict."""
        client = self._resolved_client()
        response = client.get_parameters(Names=list(_PARAMETER_NAMES), WithDecryption=False)
        invalid = response.get("InvalidParameters", ())
        if invalid:
            raise ValueError(f"SSM parameter(s) not found: {sorted(invalid)}")
        return {parameter["Name"]: parameter["Value"] for parameter in response.get("Parameters", ())}

    def _parse(self, raw: dict[str, str]) -> AlertConfig:
        coordinates, coordinates_source = _parse_location(_require(raw, _LOCATION_NAME))
        thresholds = _parse_thresholds(_require(raw, _THRESHOLDS_NAME))
        checklist = _parse_checklist(_require(raw, _CHECKLIST_NAME))
        active_channel = _parse_active_channel(_require(raw, _ACTIVE_CHANNEL_NAME))
        composer = _parse_composer(_require(raw, _COMPOSER_NAME))
        forecast_hours = _parse_positive_int(_require(raw, _FORECAST_HOURS_NAME), _FORECAST_HOURS_NAME)
        dedup_lookback_hours = _parse_positive_int(
            _require(raw, _DEDUP_LOOKBACK_HOURS_NAME), _DEDUP_LOOKBACK_HOURS_NAME
        )

        return AlertConfig(
            city=CITY,
            city_slug=CITY_SLUG,
            coordinates=coordinates,
            coordinates_source=coordinates_source,
            timezone=TIMEZONE,
            region=REGION,
            thresholds=thresholds,
            checklist=checklist,
            active_channel=active_channel,
            forecast_hours=forecast_hours,
            dedup_lookback_hours=dedup_lookback_hours,
            composer=composer,
        )
