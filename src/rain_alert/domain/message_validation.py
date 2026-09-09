"""The rules this system is willing to say out loud (design.md D16 and
section 6; agent-message-composition spec).

**Why this is a domain module.** It decides what the system will and will not
put in front of a community, which is the same argument `domain/sanitize.py`
makes in its own docstring. It imports stdlib and domain types only: no HTTP,
no AWS, no model client, nothing that has to be faked to test it.

**Why it returns violations rather than a boolean.** A caller that only knows
"rejected" cannot tell an operator what the draft got wrong, and telling the
operator is the point of the notice that accompanies every fallback. Rules are
evaluated in full rather than short-circuited, so one notice names every
problem the draft had instead of the first one.

**Direction of error, chosen deliberately.** Wrong-strict costs one template
message and one operator notice. Wrong-lax puts an invented rainfall figure in
front of a community. Every ambiguity here resolves strict.

**Invariant V0 protects the rules from themselves.** For every request the
configuration can produce, the deterministic template's own output must pass
every rule below. If it does not, the validator is wrong, not the template —
the fallback would otherwise be rejected by the very rule that guards the
agent. `tests/unit/domain/test_message_validation.py` pins it.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from rain_alert.domain.messages import AlertMessage, MessageRequest
from rain_alert.domain.sanitize import has_unsafe_characters

#: Pinned by the spec with measured justification: the template's own body is
#: about 570 characters with the five-item Ferreñafe checklist, so this leaves
#: room for a checklist that roughly doubles. The number matters less than the
#: relationship — the cap must stay above the longest body the configuration
#: can produce, and invariant V0 is what enforces that.
MAX_BODY_LENGTH = 1500

#: The template's own title runs about 47 characters and the longest live
#: aviso title is 79. A title is a subject line, not prose, so 200 is generous
#: while still refusing a body smuggled into the title field.
MAX_TITLE_LENGTH = 200

#: The body's line grammar. A second occurrence of either, as a line of its
#: own, is a forged section: it is what the `- ` bullets underneath attach to,
#: and a reader in a flood cannot tell a forged instruction block from the
#: system's own.
SECTION_HEADERS = ("Motivos:", "Recomendaciones:")


class ValidationRule(StrEnum):
    """Why a candidate message was refused."""

    LEVEL_MISMATCH = "level_mismatch"
    VALID_UNTIL_MISMATCH = "valid_until_mismatch"
    EMPTY_TITLE = "empty_title"
    EMPTY_BODY = "empty_body"
    CITY_MISSING = "city_missing"
    TITLE_TOO_LONG = "title_too_long"
    BODY_TOO_LONG = "body_too_long"
    FORGED_SECTION_HEADER = "forged_section_header"
    UNSAFE_CHARACTER = "unsafe_character"


@dataclass(frozen=True, slots=True)
class Violation:
    """One broken rule, with enough detail for an operator notice to be read
    without opening the draft that caused it."""

    rule: ValidationRule
    detail: str


def _mentions_the_city(body: str, city: str) -> bool:
    """Casefolded containment after Unicode NFC normalization of both sides.

    A byte-wise `in` misses `Ferreñafe` written with `n` plus a combining
    tilde, which renders identically. Rejecting a correct message over an
    encoding choice is a defect, not strictness.
    """
    normalized_body = unicodedata.normalize("NFC", body).casefold()
    return unicodedata.normalize("NFC", city).casefold() in normalized_body


def _body_lines(body: str) -> list[str]:
    """The body split on its own separator, and only on that.

    `str.splitlines` also splits on a bare carriage return and on several
    Unicode separators, which would hide exactly the characters rule 7 exists
    to catch.
    """
    return body.split("\n")


def validate_message(request: MessageRequest, candidate: AlertMessage) -> tuple[Violation, ...]:
    """Every rule `candidate` breaks with respect to `request`.

    Args:
        request: The composer's input — the authority on level, window, city
            and every value the body is allowed to state.
        candidate: A composed message from any source, trusted or not.

    Returns:
        One `Violation` per broken rule, in rule order. An empty tuple means
        the message is accepted; that is the only accepting answer.

    Example:
        >>> validate_message(request, template.compose(request))
        ()
    """
    violations: list[Violation] = []

    if candidate.level != request.level:
        violations.append(
            Violation(
                ValidationRule.LEVEL_MISMATCH,
                f"level {candidate.level.value!r} disagrees with the evaluated level {request.level.value!r}",
            )
        )

    if candidate.valid_until != request.window.end:
        # Deliberately not overwritten: a draft that got `valid_until` wrong
        # got something else wrong too, and overwriting hides that.
        violations.append(
            Violation(
                ValidationRule.VALID_UNTIL_MISMATCH,
                f"valid_until {candidate.valid_until.isoformat()} disagrees with the window end "
                f"{request.window.end.isoformat()}",
            )
        )

    if not candidate.title.strip():
        violations.append(Violation(ValidationRule.EMPTY_TITLE, "title is empty"))
    if not candidate.body.strip():
        violations.append(Violation(ValidationRule.EMPTY_BODY, "body is empty"))

    if not _mentions_the_city(candidate.body, request.city):
        violations.append(Violation(ValidationRule.CITY_MISSING, f"body does not mention {request.city!r}"))

    if len(candidate.title) > MAX_TITLE_LENGTH:
        violations.append(
            Violation(
                ValidationRule.TITLE_TOO_LONG,
                f"title is {len(candidate.title)} characters, over the {MAX_TITLE_LENGTH} cap",
            )
        )
    if len(candidate.body) > MAX_BODY_LENGTH:
        violations.append(
            Violation(
                ValidationRule.BODY_TOO_LONG,
                f"body is {len(candidate.body)} characters, over the {MAX_BODY_LENGTH} cap",
            )
        )

    lines = [line.strip() for line in _body_lines(candidate.body)]
    for header in SECTION_HEADERS:
        count = lines.count(header)
        if count > 1:
            violations.append(
                Violation(ValidationRule.FORGED_SECTION_HEADER, f"{header!r} appears {count} times as a full line")
            )

    if has_unsafe_characters(candidate.title) or any(
        has_unsafe_characters(line) for line in _body_lines(candidate.body)
    ):
        # The body's newlines are legitimate; nothing else in that class is.
        violations.append(
            Violation(ValidationRule.UNSAFE_CHARACTER, "title or body carries a control or bidi character")
        )

    return tuple(violations)
