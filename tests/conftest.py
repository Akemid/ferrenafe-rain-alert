"""Shared pytest fixtures for the rain_alert test suite.

Two `autouse` fixtures below make the suite's offline guarantee (design.md
D28, amended by scheduled-cycle fix round 1) a property of the suite, not of
good intentions:

- `_force_dummy_aws_credentials` — no test can authenticate as a real AWS
  identity, even if it forgets its `moto` decorator.
- `_block_non_loopback_network_egress` — no test can *reach* a real network
  destination at all, even if the first guard's dummy credentials would have
  been rejected only after a live round trip.

Both are necessary, and neither alone is sufficient — see each fixture's own
docstring for the specific failure it closes.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from typing import Any

import pytest

#: `AWS_DEFAULT_REGION` is deliberately **not** forced here (fix round 1 —
#: this amends design.md D28, which mandated it verbatim). Forcing a
#: syntactically valid region gave every AWS client constructed with no
#: explicit `region_name` enough configuration to attempt a real connection:
#: a fresh-context review found that a test with a forgotten `mock_aws()`
#: decorator reached `dynamodb.us-east-2.amazonaws.com` over real HTTPS and
#: received `UnrecognizedClientException` back — DNS resolved, TLS
#: completed, AWS answered. Confirmed by removing the variable entirely: on
#: a clean AWS config, the same client construction now raises
#: `NoRegionError`, locally, with no network attempted at all. Neither
#: `DynamoDbAlertRepository` nor `SsmConfigRepository` needs it — both
#: `moto` fixtures in this suite pass `region_name` explicitly — and the
#: socket guard below closes the gap for any future adapter that forgets to.
_DUMMY_AWS_ENVIRON = {
    "AWS_ACCESS_KEY_ID": "testing",
    "AWS_SECRET_ACCESS_KEY": "testing",
    "AWS_SESSION_TOKEN": "testing",
}


@pytest.fixture(scope="session", autouse=True)
def _force_dummy_aws_credentials() -> Iterator[None]:
    """Force every AWS credential environment variable to a dummy value for
    the whole test session.

    `pytest`'s built-in `monkeypatch` fixture is function-scoped and cannot be
    depended on by a session-scoped fixture (a scope mismatch pytest itself
    refuses), so this uses `pytest.MonkeyPatch()` directly — the same
    underlying mechanism, held open for the session and undone at teardown.

    This alone does not make the suite offline — see
    `_block_non_loopback_network_egress` below. Dummy credentials only change
    *how* a real request would be rejected (an authentication error instead
    of succeeding); they do nothing to stop the request from being sent.
    """
    patcher = pytest.MonkeyPatch()
    for name, value in _DUMMY_AWS_ENVIRON.items():
        patcher.setenv(name, value)
    try:
        yield
    finally:
        patcher.undo()


#: Real DNS names are deliberately excluded: this project's own `CLAUDE.md`
#: says the default suite must stay offline with no network at all, so
#: nothing here should ever need to resolve a hostname, real or not.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

_real_socket_connect = socket.socket.connect


def _guarded_connect(sock: socket.socket, address: Any) -> Any:
    """`socket.socket.connect`, refusing anything that is not loopback.

    Raised locally, from the exact call that attempted it — the failure this
    guard exists to prevent is silent by nature (a real request that
    completes and returns an *answer*, just not the one a passing test
    expects), so the guard has to fail loudly rather than merely differently.
    """
    host = address[0] if isinstance(address, tuple) else address
    if host not in _LOOPBACK_HOSTS:
        raise RuntimeError(
            f"tests/conftest.py's socket guard blocked a real network connection attempt to {address!r}. "
            "The default suite must stay fully offline (CLAUDE.md) -- if this is a moto-backed AWS test, "
            "check for a missing `mock_aws()`/fixture; if this is a source adapter, it should be using an "
            "offline fixture (FileHtmlFetcher, OfflineOpenMeteoProvider) or an injected client, not a real one."
        )
    return _real_socket_connect(sock, address)


@pytest.fixture(scope="session", autouse=True)
def _block_non_loopback_network_egress() -> Iterator[None]:
    """Refuse any real (non-loopback) socket connection for the whole test
    session.

    This is what actually makes the offline guarantee true, rather than
    merely making a real request fail with an AWS error instead of
    succeeding. `_force_dummy_aws_credentials` changes *how* an escaped
    request is rejected by AWS; this stops the request from leaving the
    process at all, and attributes the failure to the call that caused it
    rather than to whatever timeout or DNS behaviour the CI host happens to
    have.
    """
    patcher = pytest.MonkeyPatch()
    patcher.setattr(socket.socket, "connect", _guarded_connect)
    try:
        yield
    finally:
        patcher.undo()
