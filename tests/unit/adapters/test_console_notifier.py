"""The console notifier (local-alert-cli spec "CLI never sends externally";
source-outage-notices spec "Distinct notice type"; design.md 8.3).

The guarantee under test is not "it happens not to send" but "it cannot
send": the module imports no client of any kind, which the last test asserts
structurally rather than trusting a review to notice.
"""

from __future__ import annotations

import ast
import io
from datetime import UTC, datetime
from pathlib import Path

import rain_alert.adapters.console_notifier as notifier_module
from rain_alert.adapters.console_notifier import ConsoleNotifier
from rain_alert.domain.entities import Contact
from rain_alert.domain.messages import AlertMessage, OperatorNotice
from rain_alert.domain.values import Level, NoticeKind, SourceName

NOW = datetime(2026, 9, 4, 12, tzinfo=UTC)

MESSAGE = AlertMessage(
    title="Alerta de lluvias — Ferreñafe — prepárate",
    body="Ciudad: Ferreñafe\nNivel: prepárate",
    level=Level.PREPARE,
    valid_until=NOW,
)

NOTICE = OperatorNotice(
    kind=NoticeKind.SOURCE_UNAVAILABLE,
    subject="Source(s) unavailable: senamhi",
    body="senamhi became unavailable as of 2026-09-04T12:00:00+00:00.",
    sources=frozenset({SourceName.SENAMHI}),
    occurred_at=NOW,
)

# Nothing a recipient could be contacted through may reach stdout.
RECIPIENTS = (
    Contact("contact-1", "console", "handle-must-never-be-printed", None),
    Contact("contact-2", "console", "second-handle-must-never-be-printed", None),
)


def _notify_alert(recipients: tuple[Contact, ...] = RECIPIENTS) -> str:
    stream = io.StringIO()
    ConsoleNotifier(stream=stream).send_alert(MESSAGE, recipients)
    return stream.getvalue()


def _notify_notice() -> str:
    stream = io.StringIO()
    ConsoleNotifier(stream=stream).send_operator_notice(NOTICE)
    return stream.getvalue()


class TestCommunityAlerts:
    def test_the_alert_title_and_body_are_printed_verbatim(self) -> None:
        """local-alert-cli: Console-only delivery. The body is the composed
        Spanish message, printed exactly as it would have been delivered."""
        output = _notify_alert()

        assert MESSAGE.title in output
        assert "Ciudad: Ferreñafe" in output
        assert "Nivel: prepárate" in output

    def test_the_output_says_nothing_was_transmitted(self) -> None:
        output = _notify_alert()

        assert "[DRY-RUN ALERT]" in output
        assert "nothing was transmitted" in output.lower()

    def test_only_the_recipient_count_is_printed_never_a_handle(self) -> None:
        """repo-hygiene: no personal data. A handle on stdout ends up in a
        terminal scrollback, a CI log and a pasted bug report."""
        output = _notify_alert()

        assert "2 recipient" in output
        assert "handle-must-never-be-printed" not in output
        assert "second-handle-must-never-be-printed" not in output
        assert "contact-1" not in output

    def test_the_count_reflects_the_recipients_it_was_given(self) -> None:
        """Triangulation: a hardcoded "2 recipients" would pass the test above."""
        output = _notify_alert(recipients=(RECIPIENTS[0],))

        assert "1 recipient" in output
        assert "2 recipient" not in output

    def test_an_empty_recipient_list_is_reported_rather_than_looking_like_a_send(self) -> None:
        output = _notify_alert(recipients=())

        assert "0 recipient" in output


class TestOperatorNotices:
    def test_a_notice_is_printed_under_its_own_distinct_prefix(self) -> None:
        """source-outage-notices: Distinct notice type. An operator notice and
        a community alert must not be confusable in the output."""
        output = _notify_notice()

        assert "[OPERATOR NOTICE]" in output
        assert "[DRY-RUN ALERT]" not in output

    def test_the_notice_kind_subject_and_body_are_printed(self) -> None:
        output = _notify_notice()

        assert NoticeKind.SOURCE_UNAVAILABLE.value in output
        assert NOTICE.subject in output
        assert "senamhi became unavailable" in output

    def test_the_two_audiences_are_distinguishable_in_one_stream(self) -> None:
        stream = io.StringIO()
        console = ConsoleNotifier(stream=stream)

        console.send_operator_notice(NOTICE)
        console.send_alert(MESSAGE, RECIPIENTS)

        output = stream.getvalue()
        assert output.index("[OPERATOR NOTICE]") < output.index("[DRY-RUN ALERT]")


def test_the_module_imports_no_client_that_could_deliver_anything() -> None:
    """design.md 8.3: "never sends" is structural, not a promise. Asserted by
    AST rather than by reading, so it stays true as the module changes."""
    forbidden = {"httpx", "requests", "urllib", "urllib3", "http", "smtplib", "socket", "boto3", "subprocess"}
    tree = ast.parse(Path(notifier_module.__file__).read_text(encoding="utf-8"))

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    assert imported & forbidden == set()
    assert imported, "the scan found no imports at all, so it is not proving anything"
