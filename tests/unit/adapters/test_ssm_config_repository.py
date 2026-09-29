"""`SsmConfigRepository` — the `ConfigRepository` over SSM Parameter Store
(design.md D28, D32; `cloud-configuration` spec).

Every case here runs against `moto`'s in-memory SSM backend (D28): a
hand-written fake would have to re-implement `GetParametersByPath`'s
pagination and prefix matching, and the point of this suite is to prove the
adapter's own pagination and allow-list logic, not a fake's guess at it.
"""

from __future__ import annotations

import json
from typing import Any

import boto3
import pytest
from moto import mock_aws

from rain_alert.adapters.ssm_config_repository import (
    SSM_PATH_PREFIX,
    SsmConfigRepository,
)
from rain_alert.domain.config import CoordinatesSource
from rain_alert.domain.values import ComposerName, WarningLevel

VALID_PARAMETERS: dict[str, str] = {
    f"{SSM_PATH_PREFIX}location": json.dumps(
        {"latitude": -6.636005, "longitude": -79.789860, "source": "operator_supplied"}
    ),
    f"{SSM_PATH_PREFIX}thresholds": json.dumps(
        {
            "prepare_mm_48h": 9.5,
            "prepare_probability_pct": 60,
            "prepare_probability_pct_degraded": 70,
            "prepare_warning_levels": ["yellow", "orange", "red"],
            "imminent_mm_24h": 20.0,
            "imminent_probability_pct": 70,
            "imminent_warning_levels": ["orange", "red"],
        }
    ),
    f"{SSM_PATH_PREFIX}checklist": json.dumps(
        [
            "Almacena agua potable para al menos dos días.",
            "Limpia canaletas, techos y desagües cercanos.",
        ]
    ),
    f"{SSM_PATH_PREFIX}active-channel": "console",
    f"{SSM_PATH_PREFIX}composer": "template",
    f"{SSM_PATH_PREFIX}forecast-hours": "48",
    f"{SSM_PATH_PREFIX}dedup-lookback-hours": "72",
}


def _seed(client: Any, overrides: dict[str, str] | None = None, extra: dict[str, str] | None = None) -> None:
    parameters = dict(VALID_PARAMETERS)
    if overrides is not None:
        parameters.update(overrides)
    if extra is not None:
        parameters.update(extra)
    for name, value in parameters.items():
        client.put_parameter(Name=name, Value=value, Type="String", Overwrite=True)


def _drop(client: Any, name: str) -> None:
    """Seed every valid parameter except `name`, so `.load()` sees it absent."""
    parameters = {key: value for key, value in VALID_PARAMETERS.items() if key != name}
    for parameter_name, value in parameters.items():
        client.put_parameter(Name=parameter_name, Value=value, Type="String", Overwrite=True)


@pytest.fixture
def ssm_client() -> Any:
    with mock_aws():
        yield boto3.client("ssm", region_name="us-east-2")


# --- full load (1.19/1.20) ---


class TestFullLoad:
    def test_full_load_from_valid_parameters(self, ssm_client: Any) -> None:
        _seed(ssm_client)

        config = SsmConfigRepository(client=ssm_client).load()

        assert config.city == "Ferreñafe"
        assert config.city_slug == "ferrenafe"
        assert config.region == "Lambayeque"
        assert config.timezone == "America/Lima"
        assert config.coordinates.latitude == -6.636005
        assert config.coordinates_source == CoordinatesSource.OPERATOR_SUPPLIED
        assert config.thresholds.prepare_mm_48h == 9.5
        assert config.thresholds.prepare_warning_levels == {WarningLevel.YELLOW, WarningLevel.ORANGE, WarningLevel.RED}
        assert len(config.checklist) == 2
        assert config.active_channel == "console"
        assert config.composer == ComposerName.TEMPLATE
        assert config.forecast_hours == 48
        assert config.dedup_lookback_hours == 72


# --- city_slug is never SSM-sourced, typo-tolerant (1.22/1.23) ---


class TestCitySlugIsNeverSsmSourced:
    def test_city_slug_is_the_code_constant_regardless_of_a_stray_parameter(self, ssm_client: Any) -> None:
        """A stray parameter under the prefix at a name the loader does not
        map must be silently ignored, not silently adopted -- the loader maps
        by an explicit allow-list, never a wildcard merge."""
        _seed(ssm_client, extra={f"{SSM_PATH_PREFIX}city_slug": "some-other-slug"})

        config = SsmConfigRepository(client=ssm_client).load()

        assert config.city_slug == "ferrenafe"


# --- coordinates_source required, never inferred (1.24/1.25) ---


class TestCoordinatesProvenance:
    def test_missing_source_raises_rather_than_defaulting(self, ssm_client: Any) -> None:
        location_without_source = json.dumps({"latitude": -6.636005, "longitude": -79.789860})
        _seed(ssm_client, overrides={f"{SSM_PATH_PREFIX}location": location_without_source})

        with pytest.raises(ValueError, match="source"):
            SsmConfigRepository(client=ssm_client).load()

    def test_provenance_is_preserved_end_to_end(self, ssm_client: Any) -> None:
        _seed(ssm_client)

        config = SsmConfigRepository(client=ssm_client).load()

        assert config.coordinates_source == CoordinatesSource.OPERATOR_SUPPLIED


# --- per-field strict validation (1.26/1.27) ---

FIELD_FAULTS: tuple[tuple[str, str, str | None], ...] = (
    ("location", f"{SSM_PATH_PREFIX}location", None),
    ("location_malformed_json", f"{SSM_PATH_PREFIX}location", "{not json"),
    (
        "location_missing_latitude",
        f"{SSM_PATH_PREFIX}location",
        json.dumps({"longitude": 1.0, "source": "operator_supplied"}),
    ),
    (
        "location_out_of_range",
        f"{SSM_PATH_PREFIX}location",
        json.dumps({"latitude": 999.0, "longitude": 1.0, "source": "operator_supplied"}),
    ),
    ("thresholds", f"{SSM_PATH_PREFIX}thresholds", None),
    ("thresholds_malformed_json", f"{SSM_PATH_PREFIX}thresholds", "not-json-at-all"),
    (
        "thresholds_missing_field",
        f"{SSM_PATH_PREFIX}thresholds",
        json.dumps({"prepare_mm_48h": 9.5}),
    ),
    (
        "thresholds_unknown_warning_level",
        f"{SSM_PATH_PREFIX}thresholds",
        json.dumps(
            {
                "prepare_mm_48h": 9.5,
                "prepare_probability_pct": 60,
                "prepare_probability_pct_degraded": 70,
                "prepare_warning_levels": ["purple"],
                "imminent_mm_24h": 20.0,
                "imminent_probability_pct": 70,
                "imminent_warning_levels": ["orange", "red"],
            }
        ),
    ),
    ("checklist", f"{SSM_PATH_PREFIX}checklist", None),
    ("checklist_not_an_array", f"{SSM_PATH_PREFIX}checklist", json.dumps({"not": "an array"})),
    ("checklist_empty", f"{SSM_PATH_PREFIX}checklist", json.dumps([])),
    ("active-channel", f"{SSM_PATH_PREFIX}active-channel", None),
    ("active-channel_unrecognized", f"{SSM_PATH_PREFIX}active-channel", "sms"),
    ("composer", f"{SSM_PATH_PREFIX}composer", None),
    ("composer_unrecognized", f"{SSM_PATH_PREFIX}composer", "carrier_pigeon"),
    ("forecast-hours", f"{SSM_PATH_PREFIX}forecast-hours", None),
    ("forecast-hours_not_a_number", f"{SSM_PATH_PREFIX}forecast-hours", "forty-eight"),
    ("dedup-lookback-hours", f"{SSM_PATH_PREFIX}dedup-lookback-hours", None),
    ("dedup-lookback-hours_not_a_number", f"{SSM_PATH_PREFIX}dedup-lookback-hours", "seventy-two"),
)


class TestPerFieldStrictValidation:
    @pytest.mark.parametrize("label,name,bad_value", FIELD_FAULTS, ids=[case[0] for case in FIELD_FAULTS])
    def test_missing_or_malformed_field_raises(
        self, ssm_client: Any, label: str, name: str, bad_value: str | None
    ) -> None:
        if bad_value is None:
            # Missing entirely: seed every parameter except this one.
            _drop(ssm_client, name)
        else:
            _seed(ssm_client, overrides={name: bad_value})

        with pytest.raises(ValueError):
            SsmConfigRepository(client=ssm_client).load()

    # A truly *empty-string* parameter value is not a reachable SSM state:
    # `PutParameter` itself rejects `Value=""` with a `ValidationException`
    # ("Member must have length greater than or equal to 1"), confirmed
    # against `moto`'s emulation of the real service. So "empty" here means
    # "absent" (covered by the missing-parameter cases above and by
    # `_require`'s `not value` guard, which still protects the one
    # reachable falsy case: `Value` present but `None`, which the low-level
    # client's own response shape never actually returns either -- the guard
    # is retained as defense in depth, not because a test can force it.


# --- active_channel allow-list (1.28/1.29) ---


class TestActiveChannelAllowList:
    def test_console_is_accepted(self, ssm_client: Any) -> None:
        _seed(ssm_client)
        config = SsmConfigRepository(client=ssm_client).load()
        assert config.active_channel == "console"

    def test_any_other_value_raises(self, ssm_client: Any) -> None:
        _seed(ssm_client, overrides={f"{SSM_PATH_PREFIX}active-channel": "whatsapp"})
        with pytest.raises(ValueError, match="active-channel"):
            SsmConfigRepository(client=ssm_client).load()


# --- instance-level memoization (1.30/1.31) ---


class _CallCountingClient:
    """A hand-written spy counting `get_paginator` calls around a real
    `moto`-backed client. Not `unittest.mock`: this wraps the genuine client
    and delegates every call, only counting one method — the same pattern
    `tests/support/fakes.py` uses for its recording spies."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self.get_paginator_calls = 0

    def get_paginator(self, operation_name: str) -> Any:
        self.get_paginator_calls += 1
        return self._client.get_paginator(operation_name)


class TestMemoization:
    def test_calling_load_twice_issues_exactly_one_get_parameters_by_path_call(self, ssm_client: Any) -> None:
        _seed(ssm_client)
        counting_client = _CallCountingClient(ssm_client)
        repository = SsmConfigRepository(client=counting_client)

        first = repository.load()
        second = repository.load()

        assert first == second
        assert counting_client.get_paginator_calls == 1


# --- moto-backed pagination (1.32/1.33) ---


class TestPagination:
    def test_pagination_finds_every_allowed_parameter_across_more_than_one_page(self, ssm_client: Any) -> None:
        """`GetParametersByPath` pages at (at most) 10 parameters per call,
        in the order parameters were created (`moto`'s emulation of it, at
        least — confirmed empirically, not assumed). So the 10 stray
        parameters are seeded **first**, pushing the 7 real ones onto a
        second page. A missing `NextToken` continuation loop would silently
        drop whichever real parameter landed there, and the load below would
        raise on a "missing" parameter that was never actually missing from
        SSM — only from the first page this adapter read."""
        for i in range(10):
            ssm_client.put_parameter(Name=f"{SSM_PATH_PREFIX}stray-{i}", Value="ignored", Type="String")
        _seed(ssm_client)

        config = SsmConfigRepository(client=ssm_client).load()

        assert config.forecast_hours == 48
        assert config.dedup_lookback_hours == 72
