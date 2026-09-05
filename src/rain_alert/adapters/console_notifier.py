"""The console notifier (local-alert-cli spec; design.md 8.3).

`Notifier` implementation that prints and nothing else. Two guarantees:

- **It cannot transmit.** This module imports no HTTP client, no mail
  library, no socket and no subprocess — only `sys` and domain types. There
  is nothing here that *could* deliver a message, which is a stronger
  statement than a flag or a configuration default, and a test asserts it by
  AST so it stays true as the module changes.
- **It prints no personal data.** Community alerts report the recipient
  *count* only, never a handle or a contact identifier (repo-hygiene spec).
  A handle written to stdout ends up in a terminal scrollback, a CI log and
  eventually a pasted bug report.

The two `Notifier` methods print under distinct prefixes because they serve
distinct audiences (D3): `[DRY-RUN ALERT]` is text that was composed for the
community, `[OPERATOR NOTICE]` is technical information for whoever runs the
system. Confusing the two in a calibration log is how an operator ends up
believing a heat warning went out to a village.
"""

from __future__ import annotations

import sys
from typing import TextIO

from rain_alert.domain.entities import Contact
from rain_alert.domain.messages import AlertMessage, OperatorNotice

ALERT_PREFIX = "[DRY-RUN ALERT]"
NOTICE_PREFIX = "[OPERATOR NOTICE]"
_NOT_SENT = "nothing was transmitted: this build has no notifier that can deliver a message"
_RULE = "-" * 72


class ConsoleNotifier:
    """`Notifier` that writes to a stream, defaulting to standard output."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream if stream is not None else sys.stdout

    def send_alert(self, message: AlertMessage, recipients: tuple[Contact, ...]) -> None:
        """Print the community alert exactly as it would have been delivered.

        The body is printed verbatim — it is the composed Spanish message, and
        calibration depends on reading the real text rather than a summary.
        Recipients are reported as a count only.
        """
        self._write(
            f"{ALERT_PREFIX} {message.level.value} — {_NOT_SENT}",
            f"  would reach: {len(recipients)} recipient(s)",
            f"  valid until: {message.valid_until.isoformat()}",
            f"  title: {message.title}",
            "  body:",
            *(f"    {line}" for line in message.body.splitlines()),
        )

    def send_operator_notice(self, notice: OperatorNotice) -> None:
        """Print an operator technical notice under its own prefix."""
        self._write(
            f"{NOTICE_PREFIX} {notice.kind.value} — {_NOT_SENT}",
            f"  sources: {', '.join(sorted(source.value for source in notice.sources))}",
            f"  occurred at: {notice.occurred_at.isoformat()}",
            f"  subject: {notice.subject}",
            "  body:",
            *(f"    {line}" for line in notice.body.splitlines()),
        )

    def _write(self, *lines: str) -> None:
        self._stream.write("\n".join([_RULE, *lines, _RULE, ""]) + "\n")
