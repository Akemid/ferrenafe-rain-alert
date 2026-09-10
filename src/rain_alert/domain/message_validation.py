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
from decimal import Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo

from rain_alert.domain.messages import AlertMessage, MessageRequest
from rain_alert.domain.reasons import ForecastThresholdReason, WarningReason
from rain_alert.domain.sanitize import has_unsafe_characters, sanitize_source_text

#: Pinned by the spec. The number matters less than the relationship: the cap
#: must stay above the longest body the configuration can produce, or the
#: fallback is rejected by the rule that guards the agent.
#:
#: Measured, after an adversarial review corrected the estimate this comment
#: used to carry ("about 570 characters", "room for a checklist that roughly
#: doubles"): the shipped five-item body is **511** characters and the
#: largest request fixture is **648**. The margin that decides whether anyone
#: notices was the one against the test guarding this, which fired at 750 —
#: 102 characters, about one and a half checklist items.
#:
#: The relationship no longer rests on that margin. `domain/template.py`
#: bounds its own checklist against this constant, so the template cannot
#: emit a body over the cap whatever the operator configures. Invariant V0
#: still enforces the rest, now over checklist fixtures as well.
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


def _legitimate_lines(body: str) -> list[str]:
    """The body split on its own separator, and only on that.

    Rule 7 asks "does this body carry a character it has no business
    carrying", and the body's one legitimate break is `"\\n"`. Splitting with
    `str.splitlines` here would consume a bare carriage return and every
    Unicode separator as if it were a line break, hiding exactly the
    characters the rule exists to catch.
    """
    return body.split("\n")


def _rendered_lines(body: str) -> list[str]:
    """The body split the way whatever prints it will split it.

    Rule 6 asks a different question from rule 7 — "how many section headers
    will a reader see" — and only the renderer can answer it.
    `adapters/console_notifier.py` prints
    `for line in message.body.splitlines()`, so `str.splitlines` is the
    authority here and any narrower split is a disagreement the reader pays
    for. An adversarial review reproduced the disagreement: a body carrying
    U+2028 showed the operator two `Recomendaciones:` sections while this
    module, splitting on `"\\n"` alone, counted one and accepted it.

    Rule 7 rejects U+2028 outright now, so in practice a body reaching this
    function with one is already refused. Both rules are kept correct on
    their own terms anyway: a validator whose two halves disagree about what
    a line is has a hole waiting for the next separator someone adds.
    """
    return body.splitlines()


#: Dates and times are matched *whole*, and before bare decimals, so
#: `12/03/2027` is never read as the three independent numbers `12`, `03` and
#: `2027` — a day that happens to match some unrelated field would otherwise
#: launder the rest of the date past the rule.
#:
#: The decimal alternative takes every separator it can reach in one token
#: (`\d+(?:[.,]\d+)*`) rather than stopping at the first group, so `1.234,5`
#: arrives here as one ambiguous token and is refused as one, instead of
#: silently becoming the two unrelated numbers `1.234` and `5`.
#:
#: A leading sign is part of the figure. Without it `-12,4 mm` matched the
#: allowed `12.4` and reached a reader as a negative rainfall reading. The
#: sign is only taken when it touches the digits, so the body's own `- `
#: bullet prefix is unaffected.
#:
#: Superscript and subscript digits are matched as their own alternative,
#: U+2070/U+00B9/U+00B2/U+00B3, U+2074-U+2079 and U+2080-U+2089, and never
#: read as a quantity. Before this they escaped extraction entirely, so a
#: body could write a rainfall figure in them and face no rule at all. Cost,
#: accepted: a body writing `km` + U+00B2 outside a quoted title is refused
#: too. Wrong-strict, and narrow.
_CANDIDATE_NUMBER = re.compile(
    r"\d{1,2}/\d{1,2}/\d{4}"
    r"|\d{1,2}:\d{2}"
    r"|[-\u2212]?\d+(?:[.,]\d+)*"
    r"|[\u00b2\u00b3\u00b9\u2070\u2074-\u2079\u2080-\u2089]+"
)

_LOCAL_DATE_FORMAT = "%d/%m/%Y"
_LOCAL_TIME_FORMAT = "%H:%M"


#: Below this, a span is not a quotation — it is a coincidence, and treating
#: it as one hands a body a blanket numeric exemption. Twelve characters is
#: shorter than every shipped checklist item and every real aviso title, and
#: longer than the fragments that made the exemption dangerous: a scraped
#: title of `AVISO 80` exempted `80` everywhere it appeared in the body.
#:
#: Cost, recorded: `request.city` is nine characters, so `Ferreñafe` no
#: longer earns an exemption. It carries no digit and no Spanish numeral, so
#: nothing depends on it. A checklist item shorter than this that contains a
#: digit *would* fail invariant V0 — the shipped five contain none, and V0 is
#: the test that would say so.
_MIN_VERBATIM_SPAN_LENGTH = 12


def _verbatim_spans(request: MessageRequest) -> tuple[str, ...]:
    """The strings the system itself supplied, which a body may reproduce.

    Only a full, exact reproduction counts. A paraphrase is no longer a match,
    so any number inside it faces the full rule — which is deliberate: a
    fragment-tolerant exemption is how an invented figure gets laundered
    through a title the model half-quoted.

    One entry per string supplied, duplicates included, because the count is
    the budget: a title the request carries once buys one quotation.

    Checklist items are sanitized here for the same reason the template
    sanitizes them on the way out: the span has to be the string a body can
    actually contain, and `domain/template.py` renders the sanitized form.
    Comparing against the raw item would mean a checklist item with a tab in
    it never matched the line the template itself wrote.
    """
    spans = [sanitize_source_text(item) for item in request.checklist]
    spans += [request.city, request.timezone]
    if request.warning is not None:
        spans.append(sanitize_source_text(request.warning.title))
    spans.extend(sanitize_source_text(reason.title) for reason in request.reasons if isinstance(reason, WarningReason))
    return tuple(span for span in spans if len(span) >= _MIN_VERBATIM_SPAN_LENGTH)


def _residue(request: MessageRequest, candidate: AlertMessage) -> str:
    """Title and body with every verbatim span removed — what the model wrote.

    Longest first, so a short span nested inside a longer one cannot break the
    longer one apart. Removed spans become a space rather than nothing: welding
    the neighbouring characters together would invent numbers that were never
    written.

    **Anchored, and counted once per supplied string.** The removal used to
    be an unanchored global replace, and an adversarial review found what
    that buys. Unanchored, a span can be cut out of the middle of a longer
    number and leave a remainder the rule then approves: with a scraped title
    of `PRONOSTICO REGIONAL 1`, the body `PRONOSTICO REGIONAL 124 horas`
    became ` 24 horas`, and 24 is an hour count the request carries. Global,
    one supplied string exempted every occurrence of itself, so a body could
    quote a title once and reuse its digits as often as it liked.
    """
    text = f"{candidate.title}\n{candidate.body}"
    for span in sorted(_verbatim_spans(request), key=len, reverse=True):
        text = re.sub(rf"(?<!\w){re.escape(span)}(?!\w)", " ", text, count=1)
    return text


#: One integer part, at most one separator, one fraction, **ASCII digits
#: only**. Anything the tokenizer can emit that this does not match — a sign,
#: a second separator, a superscript run, a non-ASCII digit family — is not a
#: quantity this system will read.
#:
#: `[0-9]` rather than `\d` is load-bearing and was found by writing the test
#: that assumed the opposite. `\d` matches every Unicode decimal digit and
#: `Decimal` parses every one of them, so `१२.४`, `١٢.٤` and `１２.４` all
#: canonicalized to 12.4 and matched a millimetre reading — putting a
#: rainfall figure a Ferreñafe reader cannot read in front of them, with the
#: validator's approval. The tokenizer above still *extracts* with `\d`, so
#: those figures reach this check and are refused rather than going unseen.
_PLAIN_DECIMAL = re.compile(r"[0-9]+(?:[.,][0-9]+)?")

#: A separator followed by exactly three digits is the Spanish thousands
#: group, not a decimal, and no honest token needs that shape here.
_THOUSANDS_GROUP_LENGTH = 3


def _canonical(token: str) -> Decimal | None:
    """`12,4`, `12.4` and `12,40` are one quantity; `None` is a token this
    system refuses to read as a quantity at all.

    **Why the form is judged and not only the value.** Canonicalizing first
    threw the writing away: `Decimal("12.400") == Decimal("12.4")`, so with
    `accumulated_mm = 12.4` a body saying `12.400 mm` was accepted, and in
    Peruvian Spanish that reads as twelve thousand four hundred millimetres.
    `12,400 mm` and `0012,4 mm` passed the same way.

    **The alternative, and why not.** Comparing against the strings the
    template would render would close the same hole, but it would reject
    `12,40` — which the spec accepts on purpose, since it is the same
    quantity at the same precision — and it would need one rendered form per
    separator convention, which is a table that will drift. Refusing the
    ambiguous *form* keeps the equivalences the template can actually
    produce and refuses the ones no honest draft needs:

    | Token | Read as | Why |
    |---|---|---|
    | `12,4`, `12.4`, `12,40` | 12.4 | forms the template's `{:.1f}` can produce |
    | `12.400`, `12,400` | nothing | a thousands group is what a reader sees |
    | `1.234,5` | nothing | two separators cannot both be decimal |
    | `0012,4` | nothing | zero padding is not a form this system emits |
    | `-12,4` | nothing | no value on a request is negative |
    | `१२.४` | nothing | not a digit family a Ferreñafe reader can read |
    """
    if _PLAIN_DECIMAL.fullmatch(token) is None:
        return None
    integer, _, fraction = token.replace(",", ".").partition(".")
    if len(integer) > 1 and integer.startswith("0"):
        return None
    if len(fraction) == _THOUSANDS_GROUP_LENGTH:
        return None
    return Decimal(f"{integer}.{fraction}" if fraction else integer)


class NumberUnit(StrEnum):
    """What a figure in a body claims to measure.

    Rule 8 was unit-blind before an adversarial review: the allowed set was a
    flat `set[Decimal]`, so membership could only ask "is this number
    present" and never "present as what". With a 3.0 mm forecast, a 70 %
    probability and a 24-hour horizon, `Se esperan 70 mm de lluvia` was
    accepted, because 70 was in the set — as the probability.

    That is the most probable hallucination this validator will ever see. A
    model inventing a rainfall figure draws from the numbers in front of it,
    and those numbers are exactly the set's members, so every such invention
    passed cleanly with no trickery at all.
    """

    MILLIMETRES = "mm"
    PERCENTAGE = "%"
    HOURS = "h"


#: How a token's unit is read: from the text immediately after it. Order
#: matters only in that the percentage phrase is tried before the units whose
#: spellings could otherwise shadow it.
#:
#: `(?!\w)` rather than `\b` after the alphabetic units, so `3 hasta` does not
#: read as three hours. The percentage sign needs no such guard, since it is
#: not a word character and cannot be the head of a longer word.
_UNIT_AFTER_A_NUMBER: tuple[tuple[re.Pattern[str], NumberUnit], ...] = (
    (re.compile(r"\s*(?:%|por\s+ciento(?!\w))", re.IGNORECASE), NumberUnit.PERCENTAGE),
    (re.compile(r"\s*(?:mm|mil[ií]metros?)(?!\w)", re.IGNORECASE), NumberUnit.MILLIMETRES),
    (re.compile(r"\s*(?:horas?|h)(?!\w)", re.IGNORECASE), NumberUnit.HOURS),
)


def _unit_after(residue: str, position: int) -> NumberUnit | None:
    """The unit written immediately after `position`, or `None` for a bare
    figure that says nothing about what it measures."""
    for pattern, unit in _UNIT_AFTER_A_NUMBER:
        if pattern.match(residue, position) is not None:
            return unit
    return None


def _allowed_numbers(request: MessageRequest) -> dict[NumberUnit, set[Decimal]]:
    """Every quantity the request carries, keyed by what it measures.

    Millimetre amounts are added twice, raw and at one decimal place, because
    `domain/template.py` prints `{value:.1f}` and a model told "12.4 mm" may
    legitimately write `12,4`.
    """
    allowed: dict[NumberUnit, set[Decimal]] = {unit: set() for unit in NumberUnit}

    def add_mm(value: float) -> None:
        allowed[NumberUnit.MILLIMETRES].update({Decimal(str(value)), Decimal(f"{value:.1f}")})

    forecast = request.forecast
    if forecast is not None:
        add_mm(forecast.mm_24h)
        add_mm(forecast.mm_48h)
        allowed[NumberUnit.PERCENTAGE].add(Decimal(forecast.peak_probability_pct))
        # The forecast's own fixed horizons, which its summary is computed over.
        allowed[NumberUnit.HOURS].update({Decimal(24), Decimal(48)})
    for reason in request.reasons:
        if isinstance(reason, ForecastThresholdReason):
            add_mm(reason.accumulated_mm)
            allowed[NumberUnit.HOURS].add(Decimal(reason.hours))
            allowed[NumberUnit.PERCENTAGE].add(Decimal(reason.probability_pct))
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
    """Every candidate number in `residue` the request cannot account for,
    already worded for an operator notice.

    A bare figure — one with no unit written after it — is checked against
    the union of every unit's values. That is deliberate and it is what keeps
    the template's own output passing: a body may refer to a number the
    request carries without restating what it measures, and refusing that
    would refuse the fallback. A figure that *does* name its unit is held to
    that unit alone.
    """
    allowed_numbers = _allowed_numbers(request)
    any_unit = set[Decimal]().union(*allowed_numbers.values())
    allowed_dates, allowed_times = _allowed_moments(request)
    unknown: list[str] = []
    for match in _CANDIDATE_NUMBER.finditer(residue):
        token = match.group()
        if "/" in token:
            if _normalized_date(token) not in allowed_dates:
                unknown.append(f"{token!r} traces to no date on the request")
        elif ":" in token:
            if _normalized_time(token) not in allowed_times:
                unknown.append(f"{token!r} traces to no time on the request")
        else:
            unit = _unit_after(residue, match.end())
            permitted = any_unit if unit is None else allowed_numbers[unit]
            if _canonical(token) not in permitted:
                unknown.append(
                    f"{token!r} traces to no value on the request"
                    if unit is None
                    else f"{token!r} is stated in {unit.value!r} and traces to no such value on the request"
                )
    return tuple(unknown)


#: Spanish numerals, accent-stripped: `veintidós` and `veintidos` are the same
#: word to a reader. The lexicon is never parsed into an integer — parsing
#: `veintidós mil cuatrocientos` is a grammar, and every bug in that grammar
#: fails lax. A word-number here already means the draft disobeyed the prompt's
#: instruction to render quantities in digits, which is enough to refuse it.
#:
#: `un`, `uno` and `una` are deliberately **absent**, and they are connectors
#: below instead. They are the Spanish indefinite articles before they are
#: numerals, so with them here the rule refused `Espera una hora después de
#: que pare la lluvia` and `Puede caer un mm` — ordinary prose, and exactly
#: the false positive the spec and the design both warned this rule about.
#: A compound still fires on its leading numeral (`treinta y un milímetros`
#: on `treinta`), so the only thing lost is a word-number of magnitude one.
#: Recorded as wrong-lax, knowingly, and narrow.
#:
#: The indefinite plurals (`cientos`, `miles`, `decenas`, `centenas`,
#: `millares`) were missing, so `Se esperan cientos de milímetros de lluvia`
#: escaped the rule entirely. They quantify nothing precise, which is what
#: makes them dangerous in a rainfall sentence rather than harmless.
_NUMERAL_WORDS = frozenset({
    "cero", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve",
    "diez", "once", "doce", "trece", "catorce", "quince", "dieciseis", "diecisiete", "dieciocho", "diecinueve",
    "veinte", "veintiun", "veintiuno", "veintiuna", "veintidos", "veintitres", "veinticuatro", "veinticinco",
    "veintiseis", "veintisiete", "veintiocho", "veintinueve",
    "treinta", "cuarenta", "cincuenta", "sesenta", "setenta", "ochenta", "noventa",
    "cien", "ciento", "doscientos", "doscientas", "trescientos", "trescientas", "cuatrocientos", "cuatrocientas",
    "quinientos", "quinientas", "seiscientos", "seiscientas", "setecientos", "setecientas",
    "ochocientos", "ochocientas", "novecientos", "novecientas", "mil", "millon", "millones",
    "cientos", "cientas", "miles", "millares", "decenas", "centenas",
})  # fmt: skip

#: The units this system actually reports. The rule is deliberately narrower
#: than the digit rule: a word-number quantifying anything else — days, or
#: objects in the operator's checklist — is not this rule's concern, and an
#: unconditioned version would reject the template's own body.
_REPORTED_UNITS = frozenset({"mm", "milimetro", "milimetros", "hora", "horas", "h", "porciento"})

#: Nouns that carry a reported unit without writing it. Read **backwards
#: only**, because forwards they are ordinary subjects: `probabilidad de 60 %`
#: must stay silent, while `probabilidad máxima de ochenta` must not.
_IMPLIED_UNIT_NOUNS = frozenset({
    "probabilidad", "probabilidades", "porcentaje", "precipitacion", "precipitaciones",
})  # fmt: skip

#: Words that may sit between a numeral and its unit without breaking the
#: link. `o`, `u`, `mas`, `menos` and `hasta` were missing, so
#: `ochenta o más milímetros` escaped on `o`; `un`/`una`/`uno` are here
#: rather than in the lexicon, for the reason given above.
_NUMERAL_CONNECTORS = frozenset({
    "y", "de", "con", "coma", "punto",
    "un", "uno", "una", "unos", "unas",
    "o", "u", "mas", "menos", "hasta", "casi", "aproximadamente", "cerca", "alrededor",
})  # fmt: skip

#: Additionally skippable when reading backwards from a numeral to the noun
#: that carries its unit. Spanish puts the qualifier between the two —
#: `probabilidad máxima de ochenta` — so a backward scan that stopped at the
#: first adjective would find nothing.
_QUALIFIERS_BEFORE_A_NUMERAL = _NUMERAL_CONNECTORS | frozenset({
    "maxima", "maximo", "minima", "minimo", "total", "acumulada", "acumulado",
    "estimada", "estimado", "prevista", "previsto", "esperada", "esperado",
    "aproximada", "aproximado", "diaria", "diario",
})  # fmt: skip

#: How far back the implied-unit noun may sit. Four is what
#: `probabilidad máxima de <numeral>` needs; wider starts reaching across
#: clauses and picking up a unit the numeral has nothing to do with.
_BACKWARD_WINDOW = 4

_WORD_OR_NUMBER = re.compile(r"[a-z]+|\d+|%")


def _fold(text: str) -> str:
    """Accent-stripped and casefolded, so one spelling is one word."""
    decomposed = unicodedata.normalize("NFD", text.casefold())
    return "".join(character for character in decomposed if not unicodedata.combining(character))


def _unit_written_after(tokens: list[str], start: int) -> str | None:
    """The unit written after `tokens[start]`, or `None`.

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


def _unit_implied_before(tokens: list[str], start: int) -> str | None:
    """The unit the words in front of `tokens[start]` give it, or `None`.

    The forward scan alone missed the phrasing that matters most, because it
    is the template's own: `Hay una probabilidad máxima de ochenta de que el
    río se desborde` states a percentage without writing one, so a model
    imitating the template writes an invented figure the same way. `Lluvia en
    milímetros: ochenta` puts the unit in front as well.

    Only `_QUALIFIERS_BEFORE_A_NUMERAL` may be crossed, and only
    `_BACKWARD_WINDOW` of them. Anything else ends the scan, which is what
    keeps `al menos dos días` silent: `al` is not a qualifier, so the `horas`
    two sentences earlier is never reached.
    """
    index = start - 1
    for _ in range(_BACKWARD_WINDOW):
        if index < 0:
            return None
        token = tokens[index]
        if token in _REPORTED_UNITS or token in _IMPLIED_UNIT_NOUNS:
            return token
        if token not in _QUALIFIERS_BEFORE_A_NUMERAL:
            return None
        index -= 1
    return None


def _word_numbers_quantifying_a_unit(residue: str) -> tuple[tuple[str, str], ...]:
    """Every `(numeral, unit)` pair the residue states in words."""
    tokens = _WORD_OR_NUMBER.findall(_fold(residue))
    found: list[tuple[str, str]] = []
    for index, token in enumerate(tokens):
        if token not in _NUMERAL_WORDS:
            continue
        unit = _unit_written_after(tokens, index) or _unit_implied_before(tokens, index)
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

    lines = [line.strip() for line in _rendered_lines(candidate.body)]
    for header in SECTION_HEADERS:
        count = lines.count(header)
        if count > 1:
            violations.append(
                Violation(ValidationRule.FORGED_SECTION_HEADER, f"{header!r} appears {count} times as a full line")
            )

    if has_unsafe_characters(candidate.title) or any(
        has_unsafe_characters(line) for line in _legitimate_lines(candidate.body)
    ):
        # The body's newlines are legitimate; nothing else in that class is.
        violations.append(
            Violation(ValidationRule.UNSAFE_CHARACTER, "title or body carries a control or bidi character")
        )

    residue = _residue(request, candidate)
    violations.extend(
        Violation(ValidationRule.UNKNOWN_NUMBER, detail) for detail in _untraceable_numbers(request, residue)
    )
    violations.extend(
        Violation(ValidationRule.WORD_NUMBER, f"{word!r} quantifies {unit!r} in words instead of digits")
        for word, unit in _word_numbers_quantifying_a_unit(residue)
    )

    return tuple(violations)
