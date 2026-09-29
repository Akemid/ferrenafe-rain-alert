"""Shared pytest fixtures for the rain_alert test suite.

The `force_dummy_aws_credentials` fixture below is the suite's offline
guarantee (design.md D28): a `moto`-driven test asserts `boto3` behaviour by
running against `moto`'s in-memory backend, and that only stays a *test* of
the code — rather than an accidental live call — if `boto3` can never resolve
a real session's credentials while any test runs. Without it, a developer
with a live `ferrenafe` `aws login` session in their shared AWS config and a
mis-scoped `moto` decorator would run part of the suite against the real
account. `tests/unit/test_conftest_fixtures.py` asserts this fixture's effect
directly, rather than only relying on every future test remembering to opt
in.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

#: `AWS_DEFAULT_REGION` rather than `AWS_REGION`: this is the variable every
#: `boto3` client actually falls back to when no explicit `region_name` is
#: given, and it is also the one this project's adapters read nowhere near
#: (agentcore_invoker.py's own docstring notes `boto3` resolves the region
#: from the environment/shared config, never from a `RAIN_ALERT_*` var).
_DUMMY_AWS_ENVIRON = {
    "AWS_ACCESS_KEY_ID": "testing",
    "AWS_SECRET_ACCESS_KEY": "testing",
    "AWS_SESSION_TOKEN": "testing",
    "AWS_DEFAULT_REGION": "us-east-2",
}


@pytest.fixture(scope="session", autouse=True)
def _force_dummy_aws_credentials() -> Iterator[None]:
    """Force every AWS credential/region environment variable to a dummy
    value for the whole test session.

    `pytest`'s built-in `monkeypatch` fixture is function-scoped and cannot be
    depended on by a session-scoped fixture (a scope mismatch pytest itself
    refuses), so this uses `pytest.MonkeyPatch()` directly — the same
    underlying mechanism, held open for the session and undone at teardown.
    """
    patcher = pytest.MonkeyPatch()
    for name, value in _DUMMY_AWS_ENVIRON.items():
        patcher.setenv(name, value)
    try:
        yield
    finally:
        patcher.undo()
