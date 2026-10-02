"""`RelayObjectReader` over `boto3`'s S3 client (design D39).

Structure copied from `s3_snapshot_publisher.py`: the client is injected and
otherwise built lazily with an explicit, bounded `Config`, so the offline suite
never needs a region and the SDK's minute-scale defaults never decide how long
a cycle may hang. `GetObject` is idempotent, so the single retry is safe.

Every failure of the `GetObject` call and of the body read leaves as a
`FetchError`; the reader does not rely on the provider's catch-all for those.
Validation and decoding failures are `FetchError` too. The one deliberately
tolerant spot is the informational `fetched-at` claim: an invalid value is
ignored rather than failing an otherwise valid object.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from rain_alert.adapters.http import FetchError
from rain_alert.adapters.senamhi_relay import MAX_OBJECT_BYTES, OBJECT_KEY, RelayObject
from rain_alert.domain.sanitize import sanitize_source_text
from rain_alert.domain.values import UnavailableReason

#: Presence of this variable (even empty) selects relay mode (design D43).
RELAY_BUCKET_ENV_VAR = "RAIN_ALERT_RELAY_BUCKET"

CONNECT_TIMEOUT = 3.0
READ_TIMEOUT = 5.0
TOTAL_MAX_ATTEMPTS = 2

#: Room inside the provider's 160-character detail cap for an S3-supplied token.
_MAX_TOKEN_LENGTH = 60


def _token(value: str) -> str:
    return sanitize_source_text(value)[:_MAX_TOKEN_LENGTH]


def _unreadable(detail: str) -> FetchError:
    return FetchError(UnavailableReason.RELAY_UNREADABLE, detail)


def _charset(content_type: str) -> str | None:
    """The declared charset of a `Content-Type` value, lowercased, or `None`."""
    for part in content_type.split(";")[1:]:
        name, _, value = part.partition("=")
        if name.strip().lower() == "charset":
            return value.strip().strip('"').lower()
    return None


def _parse_claim(raw: str | None) -> datetime | None:
    """The producer's claimed fetch time, or `None` unless it is a valid aware ISO-8601."""
    if not raw:
        return None
    try:
        claimed = datetime.fromisoformat(raw)
        if claimed.tzinfo is None or claimed.utcoffset() is None:
            return None
        # Near datetime.min/max the UTC conversion overflows; the claim is informational, so ignore it.
        return claimed.astimezone(UTC)
    except (ValueError, OverflowError):
        return None


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
            return self._decode(response, body)
        finally:
            body.close()

    @staticmethod
    def _decode(response: dict[str, Any], body: Any) -> RelayObject:
        """Validate and decode one response. The caller closes `body`.

        Order matters: the size is checked from `ContentLength` before a single
        byte is read, then the read itself is bounded to `cap + 1` bytes in
        case `ContentLength` lies, so a hostile object never reaches memory,
        the decoder or the parser.
        """
        if response.get("ContentLength", 0) > MAX_OBJECT_BYTES:
            raise _unreadable(f"relay object is over the {MAX_OBJECT_BYTES}-byte cap (ContentLength)")
        charset = _charset(str(response.get("ContentType", "")))
        if charset != "utf-8":
            raise _unreadable(f"relay object charset is not utf-8: {_token(charset or 'absent')}")
        try:
            data: bytes = body.read(MAX_OBJECT_BYTES + 1)
        except Exception as exc:  # noqa: BLE001 - urllib3 mid-read errors (SSL, protocol, decode) are not OSError
            raise _unreadable(f"relay object body read failed: {_token(type(exc).__name__)}") from exc
        if len(data) > MAX_OBJECT_BYTES:
            raise _unreadable(f"relay object is over the {MAX_OBJECT_BYTES}-byte cap (body)")
        if not data:
            raise _unreadable("relay object is empty")
        metadata = response.get("Metadata", {})
        expected = str(metadata.get("sha256", "")).lower()
        if not expected:
            raise _unreadable("relay object has no sha256 metadata")
        if not hmac.compare_digest(expected, hashlib.sha256(data).hexdigest()):
            raise _unreadable("relay object sha256 does not match its body")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _unreadable("relay object is not valid utf-8") from exc
        return RelayObject(
            text=text,
            last_modified=response["LastModified"],
            version_id=response.get("VersionId"),
            claimed_fetched_at=_parse_claim(metadata.get("fetched-at")),
        )


class UnconfiguredRelayReader:
    """The reader for a relay variable that is present but empty (design D43).

    A misconfigured relay must degrade the cycle, never fall back to scraping
    SENAMHI directly (R7), and raising at wiring time would abort the cycle
    before Open-Meteo runs. So the failure is deferred to `read`, where the
    provider turns it into `Unavailable(RELAY_UNREADABLE)`.
    """

    def read(self) -> RelayObject:
        raise _unreadable("relay bucket not configured")
