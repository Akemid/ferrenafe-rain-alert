"""The HTTP fetch seam shared by the outbound adapters (design.md D4, D12).

Two rules carry this module:

1. **No `httpx` exception ever escapes.** Every failure is translated into a
   `FetchError` carrying a `UnavailableReason` the domain already knows, so
   the provider adapters can turn it into `Unavailable` without importing
   `httpx`'s exception hierarchy or guessing at a reason string.
2. **No retries** (D12). A failed fetch yields `Unavailable` immediately: the
   degraded path is a first-class tested behaviour and the cycle repeats in
   six hours. Because nothing retries, every phase of the request must be
   bounded, which is what `DEFAULT_TIMEOUT` is for.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

import httpx

from rain_alert.domain.values import UnavailableReason

DEFAULT_TIMEOUT = httpx.Timeout(connect=3.0, read=5.0, write=3.0, pool=3.0)

QueryParams = Mapping[str, str | int | float]


class FetchError(Exception):
    """A fetch that failed, tagged with the domain reason to report.

    Deliberately not an `httpx` subclass: the provider adapters catch this and
    nothing else, so a new `httpx` error type can never slip past them
    untranslated.
    """

    def __init__(self, reason: UnavailableReason, detail: str) -> None:
        super().__init__(f"{reason.value}: {detail}")
        self.reason = reason
        self.detail = detail


class HtmlFetcher(Protocol):
    """The seam that keeps HTML parsing testable with zero HTTP (design.md 8.2)."""

    def fetch(self, url: str) -> str: ...


def fetch_text(client: httpx.Client, url: str, params: QueryParams | None = None) -> str:
    """GET `url` and return the response body as text.

    Raises:
        FetchError: on timeout (`TIMEOUT`), any other transport failure
            (`TRANSPORT_ERROR`), or a non-2xx status (`BAD_STATUS`).
    """
    try:
        response = client.get(url, params=dict(params) if params is not None else None)
    except httpx.TimeoutException as exc:
        # Checked before HTTPError: TimeoutException is a subclass of it.
        raise FetchError(UnavailableReason.TIMEOUT, f"{type(exc).__name__}: {exc}") from exc
    except httpx.HTTPError as exc:
        raise FetchError(UnavailableReason.TRANSPORT_ERROR, f"{type(exc).__name__}: {exc}") from exc

    if not response.is_success:
        raise FetchError(UnavailableReason.BAD_STATUS, f"HTTP {response.status_code} from {url}")
    return response.text


class HttpxHtmlFetcher:
    """`HtmlFetcher` over `httpx`. The transport is injectable so the same
    class is exercised offline through `httpx.MockTransport`."""

    def __init__(self, transport: httpx.BaseTransport | None = None, timeout: httpx.Timeout = DEFAULT_TIMEOUT) -> None:
        self._transport = transport
        self._timeout = timeout

    def fetch(self, url: str) -> str:
        with httpx.Client(transport=self._transport, timeout=self._timeout) as client:
            return fetch_text(client, url)
