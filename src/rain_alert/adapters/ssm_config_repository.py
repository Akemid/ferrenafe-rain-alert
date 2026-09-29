"""`SsmConfigRepository` — the `ConfigRepository` over SSM Parameter Store
(design.md D28, D32; `cloud-configuration` spec).

**Layout, one parameter per value object**, under one path prefix so the IAM
grant is one resource and the read is one `GetParametersByPath` call:

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

from rain_alert.domain.config import AlertConfig, CoordinatesSource, RiskThresholds
from rain_alert.domain.values import ComposerName, Coordinates, WarningLevel

#: A golden contract file (design.md D31, D32) also carries this same prefix
#: as a later phase's own deliverable: the Python constant here will be
#: asserted against it, and the CDK IAM grant's resource ARN will be asserted
#: to end with it, so a one-character disagreement between the two would be
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

#: The explicit allow-list the loader maps by. Never a wildcard merge over
#: whatever happens to live under the prefix — a stray parameter at, say,
#: `city_slug` must be silently ignored, not silently adopted (task 1.22).
_ALLOWED_PARAMETER_NAMES = frozenset(
    {
        _LOCATION_NAME,
        _THRESHOLDS_NAME,
        _CHECKLIST_NAME,
        _ACTIVE_CHANNEL_NAME,
        _COMPOSER_NAME,
        _FORECAST_HOURS_NAME,
        _DEDUP_LOOKBACK_HOURS_NAME,
    }
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
            f"{[member.value for member in CoordinatesSource]}, got {document['source']!r}"
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
            f"SSM parameter {_THRESHOLDS_NAME!r} is missing a field or holds an invalid value: {exc}"
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
            f"SSM parameter {_ACTIVE_CHANNEL_NAME!r} must be one of {_ALLOWED_ACTIVE_CHANNELS}, got {raw_value!r}"
        )
    return raw_value


def _parse_composer(raw_value: str) -> ComposerName:
    try:
        return ComposerName(raw_value)
    except ValueError as exc:
        raise ValueError(
            f"SSM parameter {_COMPOSER_NAME!r} must be one of {[member.value for member in ComposerName]}, "
            f"got {raw_value!r}"
        ) from exc


def _parse_positive_int(raw_value: str, name: str) -> int:
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"SSM parameter {name!r} must be an integer, got {raw_value!r}") from exc
    if value <= 0:
        raise ValueError(f"SSM parameter {name!r} must be positive, got {value}")
    return value


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
            self._client = boto3.client("ssm")
        return self._client

    def load(self) -> AlertConfig:
        if self._cached is not None:
            return self._cached
        raw = self._fetch_allowed_parameters()
        self._cached = self._parse(raw)
        return self._cached

    def _fetch_allowed_parameters(self) -> dict[str, str]:
        client = self._resolved_client()
        paginator = client.get_paginator("get_parameters_by_path")
        values: dict[str, str] = {}
        for page in paginator.paginate(Path=SSM_PATH_PREFIX, Recursive=True, WithDecryption=False):
            for parameter in page.get("Parameters", ()):
                name = parameter["Name"]
                if name in _ALLOWED_PARAMETER_NAMES:
                    values[name] = parameter["Value"]
        return values

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
