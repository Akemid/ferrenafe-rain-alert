"""`S3RelayReader` (design D39): one test group per row of the outcome table."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

import pytest
from botocore.exceptions import EndpointConnectionError, ReadTimeoutError

from rain_alert.adapters import s3_relay_reader
from rain_alert.adapters.http import FetchError
from rain_alert.adapters.senamhi_relay import OBJECT_KEY, RelayObject
from rain_alert.domain.values import UnavailableReason
from tests.support.fake_s3 import FakeS3Client, client_error

PAGE = "<html><body>Aviso de lluvia en Ferreñafe</body></html>"
PAGE_BYTES = PAGE.encode("utf-8")
SHA = hashlib.sha256(PAGE_BYTES).hexdigest()
MODIFIED = datetime(2026, 10, 2, 5, 0, tzinfo=UTC)
CLAIMED = "2026-10-02T04:59:30Z"


def _client(**overrides: Any) -> FakeS3Client:
    options: dict[str, Any] = {
        "body": PAGE_BYTES,
        "last_modified": MODIFIED,
        "metadata": {"sha256": SHA, "fetched-at": CLAIMED},
        "version_id": "ver-1",
    }
    options.update(overrides)
    return FakeS3Client(**options)


def _reader(client: FakeS3Client) -> s3_relay_reader.S3RelayReader:
    return s3_relay_reader.S3RelayReader("a-bucket", client=client)


def test_the_real_client_is_built_lazily_with_bounded_config(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[dict[str, Any]] = []

    def factory(service: str, **kwargs: Any) -> FakeS3Client:
        built.append({"service": service, **kwargs})
        return _client()

    monkeypatch.setattr(s3_relay_reader.boto3, "client", factory)
    reader = s3_relay_reader.S3RelayReader("a-bucket")
    assert built == []

    reader.read()

    config = built[0]["config"]
    assert built[0]["service"] == "s3"
    assert config.connect_timeout == 3
    assert config.read_timeout == 5
    assert config.retries["total_max_attempts"] == 2


def test_the_happy_path_returns_a_decoded_aware_object() -> None:
    client = _client()

    obj = _reader(client).read()

    assert obj == RelayObject(
        text=PAGE,
        last_modified=MODIFIED,
        version_id="ver-1",
        claimed_fetched_at=datetime(2026, 10, 2, 4, 59, 30, tzinfo=UTC),
    )
    assert obj.last_modified.tzinfo is not None
    assert client.body is not None and client.body.closed


def test_only_the_fixed_key_is_requested() -> None:
    client = _client()

    _reader(client).read()

    assert client.get_calls == [{"Bucket": "a-bucket", "Key": OBJECT_KEY}]


def test_a_missing_version_id_and_claim_are_none() -> None:
    client = _client(version_id=None, metadata={"sha256": SHA})

    obj = _reader(client).read()

    assert obj.version_id is None
    assert obj.claimed_fetched_at is None


@pytest.mark.parametrize("claim", ["not a timestamp", "2026-10-02T04:59:30", ""])
def test_an_unparseable_or_naive_claim_is_ignored(claim: str) -> None:
    obj = _reader(_client(metadata={"sha256": SHA, "fetched-at": claim})).read()

    assert obj.claimed_fetched_at is None


def test_a_missing_key_is_relay_missing() -> None:
    with pytest.raises(FetchError) as raised:
        _reader(FakeS3Client(error=client_error("NoSuchKey"))).read()

    assert raised.value.reason is UnavailableReason.RELAY_MISSING


@pytest.mark.parametrize("code", ["AccessDenied", "403", "SlowDown", "InternalError"])
def test_any_other_client_error_is_unreadable_and_names_the_code(code: str) -> None:
    with pytest.raises(FetchError) as raised:
        _reader(FakeS3Client(error=client_error(code))).read()

    assert raised.value.reason is UnavailableReason.RELAY_UNREADABLE
    assert code in raised.value.detail


@pytest.mark.parametrize(
    "error",
    [EndpointConnectionError(endpoint_url="https://s3.invalid"), ReadTimeoutError(endpoint_url="https://s3.invalid")],
)
def test_botocore_transport_errors_are_unreadable(error: Exception) -> None:
    with pytest.raises(FetchError) as raised:
        _reader(FakeS3Client(error=error)).read()

    assert raised.value.reason is UnavailableReason.RELAY_UNREADABLE
    assert type(error).__name__ in raised.value.detail


def test_a_hostile_error_code_is_sanitized_and_capped_in_the_detail() -> None:
    hostile = "Evil\n[FORGED] " + "x" * 500

    with pytest.raises(FetchError) as raised:
        _reader(FakeS3Client(error=client_error(hostile))).read()

    assert "\n" not in raised.value.detail
    assert len(raised.value.detail) <= 160
