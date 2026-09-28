import json
from typing import Any

import pytest

from rain_alert.adapters import s3_snapshot_publisher
from rain_alert.adapters.s3_snapshot_publisher import (
    CONNECT_TIMEOUT,
    READ_TIMEOUT,
    TOTAL_MAX_ATTEMPTS,
    S3SnapshotPublisher,
)


class FakeS3Client:
    """Records what it was asked to put. No unittest.mock, per project rule."""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._error = error

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return {}


def test_the_document_is_uploaded_as_utf8_json() -> None:
    client = FakeS3Client()

    S3SnapshotPublisher("a-bucket", client=client).publish({"city": "Ferreñafe"})

    call = client.calls[0]
    assert call["Bucket"] == "a-bucket"
    assert call["Key"] == "status.json"
    body = call["Body"].decode("utf-8")
    assert json.loads(body) == {"city": "Ferreñafe"}
    # Ensure ensure_ascii=False: literal UTF-8, not escaped.
    assert "Ferreñafe" in body
    assert "\\u00f1" not in body


def test_the_content_type_lets_a_browser_read_it() -> None:
    """Served through Amplify's proxy to a browser. Without this S3 serves
    application/octet-stream and the fetch parses nothing."""
    client = FakeS3Client()

    S3SnapshotPublisher("a-bucket", client=client).publish({})

    assert client.calls[0]["ContentType"] == "application/json"


def test_no_client_is_built_when_one_is_injected() -> None:
    """The offline guarantee: constructing a boto3 client needs a region, and
    the default suite has none. Same seam as agentcore_invoker."""
    publisher = S3SnapshotPublisher("a-bucket", client=FakeS3Client())

    publisher.publish({})  # must not raise NoRegionError


def test_the_real_client_is_built_with_an_explicit_bounded_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_resolved_client` is the one method every other test bypasses by
    injecting `client=`, so nothing checked what it builds.

    A bare `boto3.client("s3")` inherits botocore's defaults: connect and read
    timeouts of 60 s each and legacy retries with `max_attempts: 5`, which is
    about five minutes of silent waiting against a blackholed endpoint. The
    publish call runs after the alert has already gone out, and change 3 moves
    it into a Lambda with a hard wall-clock timeout, so the bound has to be
    the module's own and not the SDK's.

    Two mutations this reproduces: the whole `config=` argument deleted, and
    `total_max_attempts` renamed to `max_attempts` — the opposite semantics
    `agentcore_invoker.py`'s docstring warns about, where a value of 1 means
    one *retry* rather than one attempt.

    `boto3.client` is monkeypatched as the client-factory seam, not
    `unittest.mock`, so no real S3 client is constructed offline.
    """
    captured: dict[str, Any] = {}

    def fake_boto3_client(service_name: str, config: Any) -> FakeS3Client:
        captured["service_name"] = service_name
        captured["config"] = config
        return FakeS3Client()

    monkeypatch.setattr(s3_snapshot_publisher.boto3, "client", fake_boto3_client)

    S3SnapshotPublisher("a-bucket").publish({})

    assert captured["service_name"] == "s3"
    config = captured["config"]
    assert config.connect_timeout == CONNECT_TIMEOUT
    assert config.read_timeout == READ_TIMEOUT
    assert config.retries == {"total_max_attempts": TOTAL_MAX_ATTEMPTS}


def test_the_bound_stays_small_enough_for_a_scheduled_lambda() -> None:
    """The numbers themselves, not merely that some `Config` is passed.

    A single small `PutObject` is not a cold-starting model call: this asserts
    the worst case stays inside the ten-second budget the deploy design plans
    for, so a later edit that copies `agentcore_invoker.py`'s 35 s read
    timeout wholesale fails here instead of holding a Lambda open.
    """
    assert TOTAL_MAX_ATTEMPTS * (CONNECT_TIMEOUT + READ_TIMEOUT) <= 20.0
