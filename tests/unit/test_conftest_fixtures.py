"""The suite's offline guarantee is a property of a fixture, not of good
intentions (design.md D28). This test asserts the fixture itself, not merely
that some other test happens to pass.
"""

from __future__ import annotations

import socket

import boto3
import pytest


def test_aws_credentials_are_forced_to_dummy_values() -> None:
    """Inside the whole session's scope, `boto3` resolves the dummy
    credentials `tests/conftest.py`'s `autouse` fixture forces — never a
    developer's real `ferrenafe` session, no matter what the ambient
    environment or shared AWS config file holds.
    """
    credentials = boto3.Session().get_credentials()
    assert credentials is not None
    assert credentials.access_key == "testing"
    assert credentials.secret_key == "testing"


class TestTheSocketGuard:
    """Fix round 1, MUST item 3. Dummy credentials alone only change how a
    real request is rejected, not whether it is sent -- a fresh-context
    review reached `dynamodb.us-east-2.amazonaws.com` over real HTTPS with
    them in place and got `UnrecognizedClientException` back. This guard is
    what actually stops the connection, locally, before any of that."""

    def test_a_non_loopback_connection_attempt_is_blocked_before_any_socket_is_opened(self) -> None:
        """`192.0.2.1` is TEST-NET-1 (RFC 5737): guaranteed non-routable, and
        an IP literal, so resolving it performs no real DNS lookup either --
        the guard must fire before any network activity at all, including
        the address resolution a hostname would need."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with pytest.raises(RuntimeError, match="blocked a real network connection"):
                sock.connect(("192.0.2.1", 443))
        finally:
            sock.close()

    def test_a_loopback_connection_attempt_reaches_the_real_socket_layer(self) -> None:
        """The guard's job is to distinguish loopback from everything else,
        not to block all networking outright. Nothing listens on this port,
        so the *real* connection-refused error (not the guard's `RuntimeError`)
        proves the attempt reached the actual socket layer rather than being
        short-circuited."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        try:
            with pytest.raises(ConnectionRefusedError):
                sock.connect(("127.0.0.1", 1))
        finally:
            sock.close()
