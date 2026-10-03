"""The shared HTTP fetch seam (design.md D4, D12; weather-sources spec
"Network failure").

`httpx.MockTransport` is the offline seam D4 was chosen for: no extra test
dependency and no network access.
"""

from __future__ import annotations

import httpx
import pytest

from rain_alert.adapters.http import DEFAULT_TIMEOUT, FetchError, HttpxHtmlFetcher, fetch_text
from rain_alert.domain.values import UnavailableReason

URL = "https://example.test/page"


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), timeout=DEFAULT_TIMEOUT)


def test_fetch_text_returns_the_body_of_a_successful_response() -> None:
    with _client(lambda request: httpx.Response(200, text="<html>hello</html>")) as client:
        assert fetch_text(client, URL) == "<html>hello</html>"


def test_fetch_text_sends_the_query_parameters_it_was_given() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, text="ok")

    with _client(handler) as client:
        fetch_text(client, URL, params={"latitude": -6.636005, "hourly": "precipitation"})

    assert seen == ["https://example.test/page?latitude=-6.636005&hourly=precipitation"]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (404, UnavailableReason.BAD_STATUS),
        (500, UnavailableReason.BAD_STATUS),
        (301, UnavailableReason.BAD_STATUS),
    ],
    ids=["not found", "server error", "unfollowed redirect"],
)
def test_fetch_text_maps_a_non_success_status_to_bad_status(status: int, expected: UnavailableReason) -> None:
    with _client(lambda request: httpx.Response(status, text="nope")) as client, pytest.raises(FetchError) as caught:
        fetch_text(client, URL)

    assert caught.value.reason is expected
    assert str(status) in caught.value.detail


def test_fetch_text_maps_a_timeout_to_the_timeout_reason() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    with _client(handler) as client, pytest.raises(FetchError) as caught:
        fetch_text(client, URL)

    assert caught.value.reason is UnavailableReason.TIMEOUT


def test_fetch_text_maps_a_connection_failure_to_a_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    with _client(handler) as client, pytest.raises(FetchError) as caught:
        fetch_text(client, URL)

    assert caught.value.reason is UnavailableReason.TRANSPORT_ERROR
    assert "no route to host" in caught.value.detail


def test_the_default_timeout_bounds_every_phase_of_the_request() -> None:
    """D12: no retries, so every phase must be bounded or a hung socket hangs
    the whole cycle."""
    assert (
        DEFAULT_TIMEOUT.connect,
        DEFAULT_TIMEOUT.read,
        DEFAULT_TIMEOUT.write,
        DEFAULT_TIMEOUT.pool,
    ) == (3.0, 5.0, 3.0, 3.0)


def test_the_html_fetcher_returns_page_text_over_an_injected_transport() -> None:
    fetcher = HttpxHtmlFetcher(transport=httpx.MockTransport(lambda request: httpx.Response(200, text="<table/>")))

    assert fetcher.fetch(URL) == "<table/>"


def test_the_html_fetcher_raises_a_fetch_error_instead_of_leaking_httpx() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("dns", request=request)

    fetcher = HttpxHtmlFetcher(transport=httpx.MockTransport(handler))

    with pytest.raises(FetchError) as caught:
        fetcher.fetch(URL)

    assert caught.value.reason is UnavailableReason.TIMEOUT


class _CountingChunks(httpx.SyncByteStream):
    """A response body that records how many chunks the client actually pulled."""

    def __init__(self, chunk: bytes, count: int) -> None:
        self._chunk = chunk
        self._count = count
        self.pulled = 0

    def __iter__(self):
        for _ in range(self._count):
            self.pulled += 1
            yield self._chunk


def _streaming_fetcher(stream: httpx.SyncByteStream, **kwargs) -> HttpxHtmlFetcher:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=stream))
    return HttpxHtmlFetcher(transport=transport, **kwargs)


def test_a_byte_cap_aborts_the_download_without_buffering_the_whole_body() -> None:
    stream = _CountingChunks(b"x" * 1000, count=1_000_000)
    fetcher = _streaming_fetcher(stream, max_bytes=5000)

    with pytest.raises(FetchError) as caught:
        fetcher.fetch(URL)

    assert caught.value.reason is UnavailableReason.TRANSPORT_ERROR
    assert "5000" in caught.value.detail
    assert stream.pulled <= 10


def test_a_body_exactly_at_the_byte_cap_is_returned() -> None:
    stream = _CountingChunks(b"x" * 1000, count=5)

    assert _streaming_fetcher(stream, max_bytes=5000).fetch(URL) == "x" * 5000


def test_a_body_under_the_byte_cap_is_decoded_as_text() -> None:
    fetcher = HttpxHtmlFetcher(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text="<p>Ferreñafe</p>")),
        max_bytes=1000,
    )

    assert fetcher.fetch(URL) == "<p>Ferreñafe</p>"


def test_a_non_success_status_is_still_bad_status_under_a_cap() -> None:
    fetcher = HttpxHtmlFetcher(
        transport=httpx.MockTransport(lambda request: httpx.Response(503, text="down")), max_bytes=1000
    )

    with pytest.raises(FetchError) as caught:
        fetcher.fetch(URL)

    assert caught.value.reason is UnavailableReason.BAD_STATUS


def test_an_overall_deadline_aborts_a_slow_drip_that_never_trips_the_per_chunk_timeout() -> None:
    ticks = iter([0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0])
    stream = _CountingChunks(b"x", count=1_000_000)
    fetcher = _streaming_fetcher(stream, deadline_seconds=3.0, clock=lambda: next(ticks))

    with pytest.raises(FetchError) as caught:
        fetcher.fetch(URL)

    assert caught.value.reason is UnavailableReason.TIMEOUT
    assert stream.pulled <= 10
