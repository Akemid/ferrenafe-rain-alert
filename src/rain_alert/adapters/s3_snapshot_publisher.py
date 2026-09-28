from __future__ import annotations

import json
from typing import Any

import boto3
from botocore.config import Config

#: Environment variables for the snapshot bucket and object key.
SNAPSHOT_BUCKET_ENV_VAR = "RAIN_ALERT_SNAPSHOT_BUCKET"
SNAPSHOT_KEY_ENV_VAR = "RAIN_ALERT_SNAPSHOT_KEY"

#: The bound on one publish, chosen for what this call actually is: a single
#: `PutObject` of a few kilobytes to a regional S3 endpoint, in the same
#: region as the caller. It is not a cold-starting model invocation, so it
#: gets nothing like `agentcore_invoker.py`'s 35 s read timeout.
#:
#: **3 s to connect** is the same number that module uses, for the same
#: reason: a TCP and TLS handshake to a regional AWS endpoint either happens
#: promptly or is not going to.
#:
#: **5 s to read** is roughly two orders of magnitude above a healthy small
#: `PutObject`, which answers in tens of milliseconds. The headroom absorbs a
#: slow path without pretending this call can legitimately take seconds.
#:
#: **Two attempts, not one.** `agentcore_invoker.py` allows exactly one
#: because a retried model invocation costs money and its fallback message is
#: already correct. The trade here is the opposite way round: a transient S3
#: 5xx is genuinely retriable, and losing the publish means the page shows a
#: stale cycle until the next run six hours later. One retry buys that back
#: for at most another eight seconds.
#:
#: The bound is what matters. A bare `boto3.client("s3")` inherits botocore's
#: defaults — 60 s connect, 60 s read, legacy retries with `max_attempts: 5` —
#: which is roughly five minutes of silent waiting against a blackholed
#: endpoint. Today that hangs a CLI *after* the alert has already gone out;
#: change 3 puts this call in a Lambda with a hard wall-clock timeout.
CONNECT_TIMEOUT = 3.0
READ_TIMEOUT = 5.0

#: `total_max_attempts`, never `max_attempts`: the two mean opposite things.
#: `total_max_attempts` counts *every* attempt including the first, so 2 is
#: one retry; `max_attempts` counts retries excluding the first, so the same
#: 2 would be two retries. `agentcore_invoker.py`'s module docstring quotes
#: `botocore.config.Config` on this at length.
TOTAL_MAX_ATTEMPTS = 2


class S3SnapshotPublisher:
    """`SnapshotPublisher` over `boto3`'s S3 client.

    The client is injected and built on first `publish`, following
    `agentcore_invoker.py` in both halves of that pattern: construction is
    deferred because building one eagerly raises without a region configured,
    which would break `uv run pytest` on a clean machine, *and* the client is
    built with an explicit `Config` so the SDK's own minute-scale defaults
    never decide how long a publish may hang. The timeouts and the attempt
    count are this module's, and the constants above say why each is what it
    is.

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

    def publish(self, document: dict[str, Any]) -> None:
        """Publish the snapshot document to S3.

        The document is encoded as UTF-8 JSON with no ASCII escaping,
        with a trailing newline, and tagged with ContentType application/json
        so a browser fetch through Amplify's proxy can parse it.

        Args:
            document: The snapshot to publish (typically from
                `domain/snapshot.py::build_snapshot`).
        """
        body = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        self._resolved_client().put_object(
            Bucket=self._bucket,
            Key=self._key,
            Body=body,
            ContentType="application/json",
            CacheControl="max-age=60",
        )
