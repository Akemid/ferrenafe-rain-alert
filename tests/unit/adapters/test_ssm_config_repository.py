"""`SsmConfigRepository` — the `ConfigRepository` over SSM Parameter Store
(design.md D28, D32 as amended by fix round 1; `cloud-configuration` spec).

Every case here runs against `moto`'s in-memory SSM backend (D28): a
hand-written fake would have to re-implement `GetParameters`' request/
response shape (including `InvalidParameters`), and the point of this suite
is to prove the adapter's own request-construction and validation logic, not
a fake's guess at it.
"""

from __future__ import annotations

import json
from typing import Any

import boto3
import pytest
from moto import mock_aws

from rain_alert.adapters import ssm_config_repository
from rain_alert.adapters.ssm_config_repository import (
    CONNECT_TIMEOUT,
    READ_TIMEOUT,
    SSM_PATH_PREFIX,
    TOTAL_MAX_ATTEMPTS,
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
        map must never be adopted. Fix round 1, MINOR item: the mechanism
        changed from "filter a wildcard response by an allow-list" to
        "request only the seven exact names in the first place" (MUST item
        2 -- `GetParameters`, never `GetParametersByPath`), which is
        structurally stronger -- a stray parameter is never even requested,
        so there is no filter step left to get wrong. This test still
        demonstrates the outcome the old docstring claimed; see
        `test_only_the_seven_exact_names_are_requested` below for the
        mechanism itself."""
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
    """A hand-written spy counting `get_parameters` calls around a real
    `moto`-backed client. Not `unittest.mock`: this wraps the genuine client
    and delegates every call, only counting one method — the same pattern
    `tests/support/fakes.py` uses for its recording spies."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self.get_parameters_calls = 0

    def get_parameters(self, **kwargs: Any) -> Any:
        self.get_parameters_calls += 1
        return self._client.get_parameters(**kwargs)


class TestMemoization:
    def test_calling_load_twice_issues_exactly_one_get_parameters_call(self, ssm_client: Any) -> None:
        _seed(ssm_client)
        counting_client = _CallCountingClient(ssm_client)
        repository = SsmConfigRepository(client=counting_client)

        first = repository.load()
        second = repository.load()

        assert first == second
        assert counting_client.get_parameters_calls == 1


# --- exact-name request, never a wildcard (fix round 1, MUST item 2) ---


class TestGetParametersRequestsExactNamesOnly:
    """Supersedes the old `moto`-backed pagination coverage (tasks 1.32/1.33):
    `GetParameters` accepts up to ten names per call, and this adapter sends
    exactly seven, so there is nothing left to paginate. What replaces it is
    the actual point of the fix -- the request never asks for anything
    beyond the seven names an IAM grant can enumerate exactly."""

    def test_only_the_seven_exact_names_are_requested(self, ssm_client: Any) -> None:
        """A stray parameter under the same prefix, even with a name that
        collides suggestively (`city_slug`), must never appear in the
        request `GetParameters` sends -- proven by a kwarg-recording spy,
        not by inference from the loaded result."""
        _seed(ssm_client, extra={f"{SSM_PATH_PREFIX}city_slug": "some-other-slug"})
        requests: list[list[str]] = []

        class _RequestRecordingClient:
            def get_parameters(self, **kwargs: Any) -> Any:
                requests.append(kwargs["Names"])
                return ssm_client.get_parameters(**kwargs)

        SsmConfigRepository(client=_RequestRecordingClient()).load()

        assert len(requests) == 1
        assert set(requests[0]) == {
            f"{SSM_PATH_PREFIX}location",
            f"{SSM_PATH_PREFIX}thresholds",
            f"{SSM_PATH_PREFIX}checklist",
            f"{SSM_PATH_PREFIX}active-channel",
            f"{SSM_PATH_PREFIX}composer",
            f"{SSM_PATH_PREFIX}forecast-hours",
            f"{SSM_PATH_PREFIX}dedup-lookback-hours",
        }

    def test_a_parameter_absent_from_ssm_entirely_is_named_in_invalid_parameters(self, ssm_client: Any) -> None:
        """`GetParameters` reports a name it could not find in
        `InvalidParameters`, distinct from a name it found with an empty or
        malformed value. This adapter raises on that list directly, naming
        the parameter, rather than only ever seeing a generic "missing or
        empty" from `_require` once the value never made it into the parsed
        dict."""
        _drop(ssm_client, f"{SSM_PATH_PREFIX}composer")

        with pytest.raises(ValueError, match="not found.*composer") as excinfo:
            SsmConfigRepository(client=ssm_client).load()
        assert "InvalidParameters" not in str(excinfo.value)  # the raw AWS field name never leaks to the message


# --- bounded client config (fix round 1, SHOULD item 4) ---


class TestTheRealClientIsBuiltWithAnExplicitBoundedConfig:
    """`_resolved_client` is the one method every other test bypasses by
    injecting `client=`, so nothing checked what it builds. Mirrors
    `dynamodb_alert_repository.py`'s own test and rationale: a bare
    `boto3.client("ssm")` inherits botocore's minute-scale defaults, and this
    read runs before anything else in the scheduled cycle."""

    def test_the_real_client_carries_the_module_bound(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def fake_boto3_client(service_name: str, config: Any) -> object:
            captured["service_name"] = service_name
            captured["config"] = config
            return object()

        monkeypatch.setattr(ssm_config_repository.boto3, "client", fake_boto3_client)

        SsmConfigRepository()._resolved_client()

        assert captured["service_name"] == "ssm"
        config = captured["config"]
        assert config.connect_timeout == CONNECT_TIMEOUT
        assert config.read_timeout == READ_TIMEOUT
        assert config.retries == {"total_max_attempts": TOTAL_MAX_ATTEMPTS}

    def test_the_bound_stays_small_enough_for_a_scheduled_lambda(self) -> None:
        assert TOTAL_MAX_ATTEMPTS * (CONNECT_TIMEOUT + READ_TIMEOUT) <= 20.0


# --- operator-supplied values are bounded and sanitized in messages (fix round 1, SHOULD item 7) ---


class TestExceptionMessagesNeverEchoAnOperatorValueInFull:
    """An operator who pastes a credential or a large document into the
    wrong parameter by mistake must not have that mistake durably logged in
    full -- up to several kilobytes, verbatim, in CloudWatch Logs. Each case
    below plants an oversized value at the exact field whose raise used to
    interpolate it raw."""

    def test_an_oversized_active_channel_value_is_truncated_in_the_message(self, ssm_client: Any) -> None:
        oversized = "x" * 5000
        _seed(ssm_client, overrides={f"{SSM_PATH_PREFIX}active-channel": oversized})

        with pytest.raises(ValueError) as excinfo:
            SsmConfigRepository(client=ssm_client).load()

        assert oversized not in str(excinfo.value)
        assert len(str(excinfo.value)) < 300

    def test_an_oversized_composer_value_is_truncated_in_the_message(self, ssm_client: Any) -> None:
        oversized = "y" * 5000
        _seed(ssm_client, overrides={f"{SSM_PATH_PREFIX}composer": oversized})

        with pytest.raises(ValueError) as excinfo:
            SsmConfigRepository(client=ssm_client).load()

        assert oversized not in str(excinfo.value)
        assert len(str(excinfo.value)) < 300

    def test_an_oversized_integer_field_value_is_truncated_in_the_message(self, ssm_client: Any) -> None:
        oversized = "9" * 5000
        _seed(ssm_client, overrides={f"{SSM_PATH_PREFIX}forecast-hours": oversized})

        with pytest.raises(ValueError) as excinfo:
            SsmConfigRepository(client=ssm_client).load()

        assert oversized not in str(excinfo.value)
        assert len(str(excinfo.value)) < 300

    def test_an_oversized_negative_integer_value_is_truncated_in_the_message(self, ssm_client: Any) -> None:
        """Fix round 2, item 4. `int()` itself refuses more than ~4300
        digits (Python's own integer-string-conversion limit), which is why
        the all-`9`s case above never reaches `_parse_positive_int`'s
        `value <= 0` branch at all -- it fails inside `int()` and goes
        through `_echo` via the `except ValueError` path. A **3000**-digit
        negative value stays under that limit, parses to a valid (negative)
        `int`, and used to reach the `value <= 0` raise, which interpolated
        the parsed integer directly with no `_echo` -- a 3076-character
        message into CloudWatch Logs, confirmed by a fresh-context probe."""
        oversized_negative = "-" + "9" * 3000
        _seed(ssm_client, overrides={f"{SSM_PATH_PREFIX}forecast-hours": oversized_negative})

        with pytest.raises(ValueError) as excinfo:
            SsmConfigRepository(client=ssm_client).load()

        assert oversized_negative not in str(excinfo.value)
        assert len(str(excinfo.value)) < 300

    def test_an_oversized_coordinates_source_value_is_truncated_in_the_message(self, ssm_client: Any) -> None:
        oversized = json.dumps({"latitude": 1.0, "longitude": 1.0, "source": "z" * 5000})
        _seed(ssm_client, overrides={f"{SSM_PATH_PREFIX}location": oversized})

        with pytest.raises(ValueError) as excinfo:
            SsmConfigRepository(client=ssm_client).load()

        assert "z" * 5000 not in str(excinfo.value)
        assert len(str(excinfo.value)) < 300

    def test_control_characters_in_an_operator_value_do_not_reach_the_message(self, ssm_client: Any) -> None:
        """The same control/bidi-character concern `sanitize_source_text`
        exists for anywhere else it is applied -- a raw ANSI escape or a
        right-to-left override in an operator value must not reach a
        terminal or a log viewer unsanitized."""
        hostile = "console\x1b[31m\ndrop-tables"
        _seed(ssm_client, overrides={f"{SSM_PATH_PREFIX}active-channel": hostile})

        with pytest.raises(ValueError) as excinfo:
            SsmConfigRepository(client=ssm_client).load()

        assert "\x1b" not in str(excinfo.value)
        assert "\n" not in str(excinfo.value)
