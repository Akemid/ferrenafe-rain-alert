"""A hand-written S3 client fake (design D44). No `unittest.mock`.

It returns what `boto3`'s `get_object` returns (a dict with a streaming `Body`)
and raises **real** `botocore` exceptions, so the code under test is exercised
against the exception classes it will meet in production.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from botocore.exceptions import ClientError

_DEFAULT_MODIFIED = datetime(2026, 10, 2, 5, 0, tzinfo=UTC)


_STATUS_BY_CODE = {
    "NoSuchKey": 404,
    "NoSuchBucket": 404,
    "AccessDenied": 403,
    "403": 403,
    "SlowDown": 503,
    "InternalError": 500,
}

def client_error(code: str, operation: str = "GetObject") -> ClientError:
    """A real `ClientError` carrying `code`, shaped as botocore builds it."""
    status = _STATUS_BY_CODE.get(code, 400)
    return ClientError(
        {
            "Error": {"Code": code, "Message": f"fake {code}"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        operation,
    )


class TrackingBody:
    """A `StreamingBody` stand-in recording how it was used."""

    def __init__(self, data: bytes, error: Exception | None = None) -> None:
        self._data = data
        self._error = error
        self._offset = 0
        self.read_sizes: list[int | None] = []
        self.closed = False

    def read(self, amt: int | None = None) -> bytes:
        self.read_sizes.append(amt)
        if self._error is not None:
            raise self._error
        end = len(self._data) if amt is None else self._offset + amt
        chunk = self._data[self._offset : end]
        self._offset += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True


class FakeS3Client:
    """Returns one programmed object or raises one programmed error."""

    def __init__(
        self,
        *,
        body: bytes = b"",
        content_length: int | None = None,
        content_type: str = "text/html; charset=utf-8",
        last_modified: datetime = _DEFAULT_MODIFIED,
        version_id: str | None = "v1",
        metadata: dict[str, str] | None = None,
        error: Exception | None = None,
        body_error: Exception | None = None,
    ) -> None:
        self._body = body
        self._content_length = len(body) if content_length is None else content_length
        self._content_type = content_type
        self._last_modified = last_modified
        self._version_id = version_id
        self._metadata = {} if metadata is None else metadata
        self._error = error
        self._body_error = body_error
        self.body: TrackingBody | None = None
        self.get_calls: list[dict[str, Any]] = []
        self.put_calls: list[dict[str, Any]] = []

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        self.get_calls.append(kwargs)
        if self._error is not None:
            raise self._error
        self.body = TrackingBody(self._body, self._body_error)
        response: dict[str, Any] = {
            "Body": self.body,
            "ContentLength": self._content_length,
            "ContentType": self._content_type,
            "LastModified": self._last_modified,
            "Metadata": self._metadata,
        }
        if self._version_id is not None:
            response["VersionId"] = self._version_id
        return response

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.put_calls.append(kwargs)
        return {}
