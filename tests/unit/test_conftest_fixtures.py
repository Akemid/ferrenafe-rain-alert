"""The suite's offline guarantee is a property of a fixture, not of good
intentions (design.md D28). This test asserts the fixture itself, not merely
that some other test happens to pass.
"""

from __future__ import annotations

import boto3


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
