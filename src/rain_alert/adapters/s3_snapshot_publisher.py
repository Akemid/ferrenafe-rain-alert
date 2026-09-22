"""`S3SnapshotPublisher` — the real `SnapshotPublisher` over `boto3`'s S3 client.

The client is injected and built on first `publish`, exactly as
`agentcore_invoker.py` does: constructing one eagerly raises without a
region configured, which would break `uv run pytest` on a clean machine.
"""

from __future__ import annotations

import json
from typing import Any

import boto3

#: Environment variables for the snapshot bucket and object key.
SNAPSHOT_BUCKET_ENV_VAR = "RAIN_ALERT_SNAPSHOT_BUCKET"
SNAPSHOT_KEY_ENV_VAR = "RAIN_ALERT_SNAPSHOT_KEY"


class S3SnapshotPublisher:
    """`SnapshotPublisher` over `boto3`'s S3 client.

    The client is injected and built on first `publish`, exactly as
    `agentcore_invoker.py` does: constructing one eagerly raises without a
    region configured, which would break `uv run pytest` on a clean machine.

    Args:
        bucket: The S3 bucket name where the snapshot is stored.
        key: The S3 object key (default: "status.json").
        client: Injected for testability. `None` (the default) defers
            construction to the first `publish` call, so the offline suite
            never builds a real client and never needs a region configured.
    """

    def __init__(self, bucket: str, key: str = "status.json", client: Any | None = None) -> None:
        self._bucket = bucket
        self._key = key
        self._client = client

    def _resolved_client(self) -> Any:
        """Lazily construct the S3 client on first use."""
        if self._client is None:
            self._client = boto3.client("s3")
        return self._client

    def publish(self, document: dict[str, Any]) -> None:
        """Publish the snapshot document to S3.

        The document is encoded as UTF-8 JSON with no ASCII escaping,
        with a trailing newline, and tagged with ContentType application/json
        so a browser fetch through Amplify's proxy can parse it.

        Args:
            document: The snapshot to publish (typically from
                `SnapshotBuilder.build()`).
        """
        body = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        self._resolved_client().put_object(
            Bucket=self._bucket,
            Key=self._key,
            Body=body,
            ContentType="application/json",
            CacheControl="max-age=60",
        )
