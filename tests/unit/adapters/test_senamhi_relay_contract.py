"""`contracts/senamhi-relay.json` and the Python constants must agree (D37).

The CDK stack reads `object_key` for both grants. If the grant and the reader
disagree by one character, every cycle gets `AccessDenied` and nothing else
notices, which is the D32 prefix lesson.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from rain_alert.adapters.local.static_config_repository import REGION
from rain_alert.adapters.senamhi_relay import MAX_AGE, MAX_OBJECT_BYTES, OBJECT_KEY, SKEW_TOLERANCE
from rain_alert.adapters.senamhi_scraper import region_slug

CONTRACT_PATH = Path(__file__).resolve().parents[3] / "contracts" / "senamhi-relay.json"


def _contract() -> dict[str, object]:
    loaded: dict[str, object] = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    return loaded


def test_the_object_key_matches_the_contract_file() -> None:
    assert _contract()["object_key"] == OBJECT_KEY


def test_the_age_limit_matches_the_contract_file() -> None:
    assert timedelta(seconds=int(str(_contract()["max_age_seconds"]))) == MAX_AGE


def test_the_size_cap_matches_the_contract_file() -> None:
    assert _contract()["max_object_bytes"] == MAX_OBJECT_BYTES


def test_the_skew_tolerance_matches_the_contract_file() -> None:
    assert timedelta(seconds=int(str(_contract()["skew_tolerance_seconds"]))) == SKEW_TOLERANCE


def test_the_contract_pins_the_values_the_design_chose() -> None:
    """Guards the file itself: a test comparing two copies of a wrong number passes."""
    assert _contract() == {
        "schema_version": 1,
        "object_key": "senamhi/lambayeque/latest.html",
        "max_age_seconds": 10800,
        "max_object_bytes": 4194304,
        "skew_tolerance_seconds": 900,
    }


def test_the_key_names_the_region_the_scraper_reads() -> None:
    """A renamed region must not keep writing to the old key."""
    assert OBJECT_KEY.split("/")[1] == region_slug(REGION)
