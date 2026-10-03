"""`FakeS3Client` is shared by the reader and producer tests (design D44), so
it is pinned here: a fake that drifts from boto3's shapes would let both
suites pass against something S3 never returns."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError, ReadTimeoutError

from tests.support.fake_s3 import FakeS3Client, TrackingBody, client_error

MODIFIED = datetime(2026, 10, 2, 5, 0, tzinfo=UTC)


def test_get_object_returns_the_boto3_response_shape() -> None:
    client = FakeS3Client(
        body=b"<html/>",
        last_modified=MODIFIED,
        version_id="v1",
        metadata={"sha256": "abc"},
        content_type="text/html; charset=utf-8",
    )

    response = client.get_object(Bucket="b", Key="k")

    assert isinstance(response["Body"], TrackingBody)
    assert response["ContentLength"] == len(b"<html/>")
    assert response["ContentType"] == "text/html; charset=utf-8"
    assert response["LastModified"] == MODIFIED
    assert response["VersionId"] == "v1"
    assert response["Metadata"] == {"sha256": "abc"}
    assert client.get_calls == [{"Bucket": "b", "Key": "k"}]


def test_content_length_can_lie() -> None:
    client = FakeS3Client(body=b"x" * 10, content_length=1)

    assert client.get_object(Bucket="b", Key="k")["ContentLength"] == 1


def test_the_body_records_reads_and_close() -> None:
    body = TrackingBody(b"abcdef")

    assert body.read(3) == b"abc"
    assert body.read_sizes == [3]
    assert not body.closed
    body.close()
    assert body.closed


def test_a_programmed_client_error_is_a_real_client_error() -> None:
    client = FakeS3Client(error=client_error("NoSuchKey"))

    with pytest.raises(ClientError) as raised:
        client.get_object(Bucket="b", Key="k")

    assert raised.value.response["Error"]["Code"] == "NoSuchKey"


@pytest.mark.parametrize(
    ("code", "status"),
    [("NoSuchKey", 404), ("NoSuchBucket", 404), ("AccessDenied", 403), ("SlowDown", 503), ("InternalError", 500)],
)
def test_client_error_carries_the_http_status_botocore_would(code: str, status: int) -> None:
    assert client_error(code).response["ResponseMetadata"]["HTTPStatusCode"] == status


@pytest.mark.parametrize(
    "error",
    [EndpointConnectionError(endpoint_url="https://s3.invalid"), ReadTimeoutError(endpoint_url="https://s3.invalid")],
)
def test_botocore_errors_are_raised_unchanged(error: Exception) -> None:
    with pytest.raises(type(error)):
        FakeS3Client(error=error).get_object(Bucket="b", Key="k")


def test_put_object_records_its_kwargs() -> None:
    client = FakeS3Client()

    client.put_object(Bucket="b", Key="k", Body=b"x")

    assert client.put_calls == [{"Bucket": "b", "Key": "k", "Body": b"x"}]


def test_put_object_raises_the_programmed_put_error_and_still_records_the_attempt() -> None:
    client = FakeS3Client(put_error=client_error("AccessDenied", "PutObject"))

    with pytest.raises(ClientError):
        client.put_object(Bucket="b", Key="k", Body=b"x")

    assert client.put_calls == [{"Bucket": "b", "Key": "k", "Body": b"x"}]


def test_a_get_error_does_not_break_put_object() -> None:
    client = FakeS3Client(error=client_error("NoSuchKey"))

    client.put_object(Bucket="b", Key="k", Body=b"x")

    assert len(client.put_calls) == 1


def test_a_successful_put_becomes_what_get_returns() -> None:
    client = FakeS3Client(last_modified=MODIFIED)

    client.put_object(
        Bucket="b",
        Key="k",
        Body=b"<p>hi</p>",
        ContentType="text/html; charset=utf-8",
        Metadata={"sha256": "abc"},
    )
    response = client.get_object(Bucket="b", Key="k")

    assert response["Body"].read() == b"<p>hi</p>"
    assert response["ContentLength"] == len(b"<p>hi</p>")
    assert response["ContentType"] == "text/html; charset=utf-8"
    assert response["Metadata"] == {"sha256": "abc"}


def test_a_failed_put_leaves_the_previous_object_in_place() -> None:
    client = FakeS3Client(body=b"old", put_error=client_error("AccessDenied", "PutObject"))

    with pytest.raises(ClientError):
        client.put_object(Bucket="b", Key="k", Body=b"new")

    assert client.get_object(Bucket="b", Key="k")["Body"].read() == b"old"
