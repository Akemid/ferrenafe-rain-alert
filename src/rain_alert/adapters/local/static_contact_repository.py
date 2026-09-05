"""`ContactRepository` holding a single synthetic local contact (design.md 8.3).

**No email address, no phone number, no `example.com`** — nothing a hygiene
grep would have to be taught to forgive (repo-hygiene spec). The one contact
is the local operator's own terminal, and its "handle" is literally the
output stream, which is also the honest description of where a community
alert goes in this build.

Real recipients arrive in change 3, from a store that is never committed.
"""

from __future__ import annotations

from rain_alert.domain.entities import Contact

CONSOLE_CHANNEL = "console"

LOCAL_OPERATOR = Contact(
    contact_id="operator-local",
    channel=CONSOLE_CHANNEL,
    handle="stdout",
    consent_at=None,
)


class StaticContactRepository:
    """`ContactRepository` returning the local operator for the console channel."""

    def list_active(self, channel: str) -> tuple[Contact, ...]:
        """Active contacts for `channel`.

        Any channel other than the console has no recipients here, which is
        what makes an accidental request for a real delivery channel come back
        empty rather than falling through to the operator.
        """
        return (LOCAL_OPERATOR,) if channel == CONSOLE_CHANNEL else ()
