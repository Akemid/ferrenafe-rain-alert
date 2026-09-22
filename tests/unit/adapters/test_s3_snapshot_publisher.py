import json
from typing import Any

from rain_alert.adapters.s3_snapshot_publisher import S3SnapshotPublisher


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
    assert json.loads(call["Body"].decode("utf-8")) == {"city": "Ferreñafe"}


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
