"""`RelayObjectReader` over `boto3`'s S3 client (design D39).

Structure copied from `s3_snapshot_publisher.py`: the client is injected and
otherwise built lazily with an explicit, bounded `Config`, so the offline suite
never needs a region and the SDK's minute-scale defaults never decide how long
a cycle may hang. `GetObject` is idempotent, so the single retry is safe.

Every failure leaves as a `FetchError`; nothing else escapes (the provider
above also guards, but the reader does not rely on it).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from rain_alert.adapters.http import FetchError
from rain_alert.adapters.senamhi_relay import OBJECT_KEY, RelayObject
from rain_alert.domain.sanitize import sanitize_source_text
from rain_alert.domain.values import UnavailableReason

CONNECT_TIMEOUT = 3.0
READ_TIMEOUT = 5.0
TOTAL_MAX_ATTEMPTS = 2

#: Room inside the provider's 160-character detail cap for an S3-supplied token.
_MAX_TOKEN_LENGTH = 60


def _token(value: str) -> str:
    return sanitize_source_text(value)[:_MAX_TOKEN_LENGTH]


def _parse_claim(raw: str | None) -> datetime | None:
    """The producer's claimed fetch time, or `None` unless it is a valid aware ISO-8601."""
    if not raw:
        return None
    try:
        claimed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if claimed.tzinfo is None or claimed.utcoffset() is None:
        return None
    return claimed.astimezone(UTC)


class S3RelayReader:
    """Reads the one relay object. The key is fixed: it is not an env override (D37).

    Args:
        bucket: The relay bucket name.
        client: Injected for testability. `None` defers construction to the
            first `read`.
    """

    def __init__(self, bucket: str, client: Any | None = None) -> None:
        self._bucket = bucket
        self._client = client

    def _resolved_client(self) -> Any:
        if self._client is None:
            self._client = boto3.client(
                "s3",
                config=Config(
                    connect_timeout=CONNECT_TIMEOUT,
                    read_timeout=READ_TIMEOUT,
                    retries={"total_max_attempts": TOTAL_MAX_ATTEMPTS},
                ),
            )
        return self._client

    def read(self) -> RelayObject:
        """Fetch and decode the relay object.

        Raises:
            FetchError: `RELAY_MISSING` for a missing key, `RELAY_UNREADABLE`
                for everything else that goes wrong.
        """
        try:
            response = self._resolved_client().get_object(Bucket=self._bucket, Key=OBJECT_KEY)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", "unknown"))
            reason = UnavailableReason.RELAY_MISSING if code == "NoSuchKey" else UnavailableReason.RELAY_UNREADABLE
            raise FetchError(reason, f"S3 GetObject failed: {_token(code)}") from exc
        except BotoCoreError as exc:
            raise FetchError(UnavailableReason.RELAY_UNREADABLE, f"S3 GetObject failed: {type(exc).__name__}") from exc

        body = response["Body"]
        try:
            data: bytes = body.read()
        finally:
            body.close()
        metadata = response.get("Metadata", {})
        return RelayObject(
            text=data.decode("utf-8"),
            last_modified=response["LastModified"],
            version_id=response.get("VersionId"),
            claimed_fetched_at=_parse_claim(metadata.get("fetched-at")),
        )
