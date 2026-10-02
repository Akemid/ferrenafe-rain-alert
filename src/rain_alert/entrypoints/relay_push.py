"""`rain-alert-relay-push`: the off-AWS producer of the SENAMHI relay (design D42).

SENAMHI refuses AWS egress, so this runs on an operator machine, fetches the
warnings page, proves it parses, and only then replaces the one S3 object the
Lambda reads. The old object is replaced by a page that parsed, never by
anything else: every failure path before the PUT returns without writing.

Exit codes: 0 pushed, 1 not configured or bad usage, 2 fetch or parse failed,
3 page over the size cap, 4 the S3 client or write failed.

Credentials are the default boto3 chain (the operator configures a
`credential_process` profile, D36). Nothing here reads, prints or logs a key,
and no message carries page content or a raw exception message.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, NoReturn

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from rain_alert.adapters.http import FetchError, HtmlFetcher, HttpxHtmlFetcher
from rain_alert.adapters.local.static_config_repository import REGION
from rain_alert.adapters.s3_relay_reader import RELAY_BUCKET_ENV_VAR
from rain_alert.adapters.senamhi_relay import MAX_OBJECT_BYTES, OBJECT_KEY
from rain_alert.adapters.senamhi_scraper import parse_warnings_page, warnings_url_for
from rain_alert.domain.sanitize import sanitize_source_text

__all__ = ["main"]

EXIT_OK = 0
EXIT_NOT_CONFIGURED = 1
EXIT_GATE_FAILED = 2
EXIT_TOO_LARGE = 3
EXIT_WRITE_FAILED = 4

#: Recorded in the object metadata so an operator can tell which machine pushed.
PRODUCER_ID = "rain-alert-relay-push"
CONTENT_TYPE = "text/html; charset=utf-8"

#: Same bounds as the reader and the snapshot publisher: one `PutObject` of a
#: page under 4 MiB to a regional endpoint. `PutObject` of the same bytes to the
#: same key is idempotent, so the single retry is safe.
CONNECT_TIMEOUT = 3.0
READ_TIMEOUT = 10.0
TOTAL_MAX_ATTEMPTS = 2

_MAX_TOKEN_LENGTH = 60


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # argparse exits 2, which here means "the gate refused the page".
        self.print_usage(sys.stderr)
        self.exit(EXIT_NOT_CONFIGURED, "relay push: bad usage\n")


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="rain-alert-relay-push", description="Push the SENAMHI warnings page to the relay bucket.")
    parser.add_argument("--profile", help="AWS profile name (default: the default credential chain)")
    parser.add_argument("--region", help="AWS region (default: the default region chain)")
    return parser


def _token(value: str) -> str:
    return sanitize_source_text(value)[:_MAX_TOKEN_LENGTH]


def _build_client(profile: str | None, region: str | None) -> Any:
    session = boto3.Session(profile_name=profile, region_name=region)
    return session.client(
        "s3",
        config=Config(
            connect_timeout=CONNECT_TIMEOUT,
            read_timeout=READ_TIMEOUT,
            retries={"total_max_attempts": TOTAL_MAX_ATTEMPTS},
        ),
    )


def _write_failure(exc: Exception) -> str:
    """A one-line, content-free description of a client or write failure."""
    if isinstance(exc, ClientError):
        code = _token(str(exc.response.get("Error", {}).get("Code", "unknown")))
        return f"S3 write failed: {code}"
    return f"S3 write failed: {_token(type(exc).__name__)}"


def main(
    argv: Sequence[str] | None = None,
    *,
    fetcher: HtmlFetcher | None = None,
    client: Any | None = None,
    now: datetime | None = None,
) -> int:
    """Fetch, gate, and push the warnings page. Never raises for a runtime failure."""
    args = _parser().parse_args(argv)
    bucket = os.environ.get(RELAY_BUCKET_ENV_VAR, "").strip()
    if not bucket:
        print(f"relay push: {RELAY_BUCKET_ENV_VAR} is not set", file=sys.stderr)
        return EXIT_NOT_CONFIGURED

    moment = now if now is not None else datetime.now(UTC)
    try:
        html = (fetcher if fetcher is not None else HttpxHtmlFetcher()).fetch(warnings_url_for(REGION))
        outcome = parse_warnings_page(html, REGION, moment)
    except FetchError as exc:
        # Only the reason enum is printed: the detail may quote the page.
        print(f"relay push: refused to push, {exc.reason.value}", file=sys.stderr)
        return EXIT_GATE_FAILED

    body = html.encode("utf-8")
    if len(body) > MAX_OBJECT_BYTES:
        print(f"relay push: refused to push, page is over the {MAX_OBJECT_BYTES}-byte cap", file=sys.stderr)
        return EXIT_TOO_LARGE

    try:
        s3 = client if client is not None else _build_client(args.profile, args.region)
        response = s3.put_object(
            Bucket=bucket,
            Key=OBJECT_KEY,
            Body=body,
            ContentType=CONTENT_TYPE,
            ChecksumAlgorithm="SHA256",
            Metadata={
                "sha256": hashlib.sha256(body).hexdigest(),
                "fetched-at": moment.astimezone(UTC).isoformat(timespec="seconds"),
                "producer": PRODUCER_ID,
            },
        )
    except Exception as exc:  # noqa: BLE001 - any client or write failure is exit 4, never a traceback
        print(f"relay push: {_write_failure(exc)}", file=sys.stderr)
        return EXIT_WRITE_FAILED

    version = _token(str(response.get("VersionId", "none")))
    print(f"relay push: ok version={version} bytes={len(body)} rows_seen={outcome.rows_seen}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
