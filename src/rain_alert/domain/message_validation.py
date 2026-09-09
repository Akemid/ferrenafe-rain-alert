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

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from zoneinfo import ZoneInfo

from rain_alert.domain.messages import AlertMessage, MessageRequest
from rain_alert.domain.reasons import ForecastThresholdReason, WarningReason
from rain_alert.domain.sanitize import has_unsafe_characters, sanitize_source_text

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
    UNKNOWN_NUMBER = "unknown_number"
    WORD_NUMBER = "word_number"


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


#: Dates and times are matched *whole*, and before bare decimals, so
#: `12/03/2027` is never read as the three independent numbers `12`, `03` and
#: `2027` — a day that happens to match some unrelated field would otherwise
#: launder the rest of the date past the rule.
_CANDIDATE_NUMBER = re.compile(r"\d{1,2}/\d{1,2}/\d{4}|\d{1,2}:\d{2}|\d+(?:[.,]\d+)?")

_LOCAL_DATE_FORMAT = "%d/%m/%Y"
_LOCAL_TIME_FORMAT = "%H:%M"


def _verbatim_spans(request: MessageRequest) -> tuple[str, ...]:
    """The strings the system itself supplied, which a body may reproduce.

    Only a full, exact reproduction counts. A paraphrase is no longer a match,
    so any number inside it faces the full rule — which is deliberate: a
    fragment-tolerant exemption is how an invented figure gets laundered
    through a title the model half-quoted.
    """
    spans = [*request.checklist, request.city, request.timezone]
    if request.warning is not None:
        spans.append(sanitize_source_text(request.warning.title))
    spans.extend(sanitize_source_text(reason.title) for reason in request.reasons if isinstance(reason, WarningReason))
    return tuple(span for span in spans if span)


def _residue(request: MessageRequest, candidate: AlertMessage) -> str:
    """Title and body with every verbatim span removed — what the model wrote.

    Longest first, so a short span nested inside a longer one cannot break the
    longer one apart. Removed spans become a space rather than nothing: welding
    the neighbouring characters together would invent numbers that were never
    written.
    """
    text = f"{candidate.title}\n{candidate.body}"
    for span in sorted(_verbatim_spans(request), key=len, reverse=True):
        text = text.replace(span, " ")
    return text


def _canonical(token: str) -> Decimal | None:
    """`12,4`, `12.4`, `12.40` and `012.4` are one quantity, and `None` is a
    token that is not a quantity at all."""
    try:
        return Decimal(token.replace(",", "."))
    except InvalidOperation:  # pragma: no cover - the tokenizer cannot emit one
        return None


def _allowed_numbers(request: MessageRequest) -> set[Decimal]:
    """Every quantity the request itself carries.

    Millimetre amounts are added twice, raw and at one decimal place, because
    `domain/template.py` prints `{value:.1f}` and a model told "12.4 mm" may
    legitimately write `12,4`.
    """
    allowed: set[Decimal] = set()

    def add_mm(value: float) -> None:
        allowed.update({Decimal(str(value)), Decimal(f"{value:.1f}")})

    forecast = request.forecast
    if forecast is not None:
        add_mm(forecast.mm_24h)
        add_mm(forecast.mm_48h)
        allowed.add(Decimal(forecast.peak_probability_pct))
        # The forecast's own fixed horizons, which its summary is computed over.
        allowed.update({Decimal(24), Decimal(48)})
    for reason in request.reasons:
        if isinstance(reason, ForecastThresholdReason):
            add_mm(reason.accumulated_mm)
            allowed.update({Decimal(reason.hours), Decimal(reason.probability_pct)})
            # `probability_threshold_pct` is deliberately absent. The Spanish
            # template never renders it, only the English operator view does,
            # so it never reaches the prompt and a model cannot legitimately
            # know it. A recipient body carrying it has no honest reason to.
    return allowed


def _allowed_moments(request: MessageRequest) -> tuple[set[str], set[str]]:
    """The local dates and local times the request's own instants render as."""
    zone = ZoneInfo(request.timezone)
    moments: list[datetime] = [request.window.start, request.window.end]
    if request.warning is not None:
        moments.extend((request.warning.window.start, request.warning.window.end))
    if request.forecast is not None:
        moments.append(request.forecast.peak_at)
    local = [moment.astimezone(zone) for moment in moments]
    return (
        {moment.strftime(_LOCAL_DATE_FORMAT) for moment in local},
        {moment.strftime(_LOCAL_TIME_FORMAT) for moment in local},
    )


def _normalized_date(token: str) -> str:
    """`3/9/2026` written the way `strftime` would have written it."""
    day, month, year = token.split("/")
    return f"{int(day):02d}/{int(month):02d}/{year}"


def _normalized_time(token: str) -> str:
    """`7:00` written the way `strftime` would have written it."""
    hour, minute = token.split(":")
    return f"{int(hour):02d}:{minute}"


def _untraceable_numbers(request: MessageRequest, residue: str) -> tuple[str, ...]:
    """Every candidate number in `residue` that the request cannot account for."""
    allowed_numbers = _allowed_numbers(request)
    allowed_dates, allowed_times = _allowed_moments(request)
    unknown: list[str] = []
    for token in _CANDIDATE_NUMBER.findall(residue):
        if "/" in token:
            traceable = _normalized_date(token) in allowed_dates
        elif ":" in token:
            traceable = _normalized_time(token) in allowed_times
        else:
            traceable = _canonical(token) in allowed_numbers
        if not traceable:
            unknown.append(token)
    return tuple(unknown)


#: Spanish numerals, accent-stripped: `veintidós` and `veintidos` are the same
#: word to a reader. The lexicon is never parsed into an integer — parsing
#: `veintidós mil cuatrocientos` is a grammar, and every bug in that grammar
#: fails lax. A word-number here already means the draft disobeyed the prompt's
#: instruction to render quantities in digits, which is enough to refuse it.
_NUMERAL_WORDS = frozenset({
    "cero", "un", "uno", "una", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve",
    "diez", "once", "doce", "trece", "catorce", "quince", "dieciseis", "diecisiete", "dieciocho", "diecinueve",
    "veinte", "veintiun", "veintiuno", "veintiuna", "veintidos", "veintitres", "veinticuatro", "veinticinco",
    "veintiseis", "veintisiete", "veintiocho", "veintinueve",
    "treinta", "cuarenta", "cincuenta", "sesenta", "setenta", "ochenta", "noventa",
    "cien", "ciento", "doscientos", "doscientas", "trescientos", "trescientas", "cuatrocientos", "cuatrocientas",
    "quinientos", "quinientas", "seiscientos", "seiscientas", "setecientos", "setecientas",
    "ochocientos", "ochocientas", "novecientos", "novecientas", "mil", "millon", "millones",
})  # fmt: skip

#: The units this system actually reports. The rule is deliberately narrower
#: than the digit rule: a word-number quantifying anything else — days, or
#: objects in the operator's checklist — is not this rule's concern, and an
#: unconditioned version would reject the template's own body.
_REPORTED_UNITS = frozenset({"mm", "milimetro", "milimetros", "hora", "horas", "h", "porciento"})

#: Words that may sit between a numeral and its unit without breaking the link.
_NUMERAL_CONNECTORS = frozenset({"y", "de", "con", "coma", "punto"})

_WORD_OR_NUMBER = re.compile(r"[a-z]+|\d+|%")


def _fold(text: str) -> str:
    """Accent-stripped and casefolded, so one spelling is one word."""
    decomposed = unicodedata.normalize("NFD", text.casefold())
    return "".join(character for character in decomposed if not unicodedata.combining(character))


def _quantifies_a_reported_unit(tokens: list[str], start: int) -> str | None:
    """The unit `tokens[start]` quantifies, or `None` when it quantifies none.

    Intervening numerals and connectors are skipped, so `treinta y cinco
    milimetros` links `treinta` to `milimetros`. `por ciento` is matched as a
    phrase because `ciento` is itself a numeral.
    """
    index = start + 1
    while index < len(tokens) and (tokens[index] in _NUMERAL_WORDS or tokens[index] in _NUMERAL_CONNECTORS):
        index += 1
    if index >= len(tokens):
        return None
    if tokens[index] in _REPORTED_UNITS or tokens[index] == "%":
        return tokens[index]
    if tokens[index] == "por" and index + 1 < len(tokens) and tokens[index + 1] == "ciento":
        return "por ciento"
    return None


def _word_numbers_quantifying_a_unit(residue: str) -> tuple[tuple[str, str], ...]:
    """Every `(numeral, unit)` pair the residue states in words."""
    tokens = _WORD_OR_NUMBER.findall(_fold(residue))
    found: list[tuple[str, str]] = []
    for index, token in enumerate(tokens):
        if token not in _NUMERAL_WORDS:
            continue
        unit = _quantifies_a_reported_unit(tokens, index)
        if unit is not None:
            found.append((token, unit))
    return tuple(found)


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

    residue = _residue(request, candidate)
    violations.extend(
        Violation(ValidationRule.UNKNOWN_NUMBER, f"{token!r} traces to no value on the request")
        for token in _untraceable_numbers(request, residue)
    )
    violations.extend(
        Violation(ValidationRule.WORD_NUMBER, f"{word!r} quantifies {unit!r} in words instead of digits")
        for word, unit in _word_numbers_quantifying_a_unit(residue)
    )

    return tuple(violations)
