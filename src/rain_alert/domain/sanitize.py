"""The single sanitizer every source-derived free text passes through before
it is rendered for a human (design.md 3.3).

**Why this is a domain module and not a helper in each adapter.** The text
that needs it comes from one place — a scraped SENAMHI aviso title — but it
is rendered at four boundaries: the Spanish community body, the English
operator audit line, the operator's `Notes` block, and the persisted/`--json`
audit document. Escaping applied per adapter is escaping that one boundary
will eventually be added without. Sanitizing is a rule about what the system
is willing to say, so the rule lives in the domain and each boundary calls it.

**The concrete defect this closes.** `Warning.title` is stored raw and
deliberately (`adapters/senamhi_scraper.py` explains why: the accent-stripped
form once put `EXTENSION` in front of recipients). `domain/template.py`
interpolates it into a line-oriented body assembled with `"\\n".join`, whose
grammar is `- ` bullets and `Motivos:` / `Recomendaciones:` headers. A title
containing newlines therefore wrote that grammar itself. Reproduced end to
end: the body came out with **two** `Recomendaciones:` sections, the forged
one ahead of the genuine one and formatted identically, plus a live ANSI
escape sequence and a surviving right-to-left override. A reader deciding
what to do in a flood cannot tell the forged instructions from the system's
own, and that is the whole value of the message.

Four transformations, in this order:

1. **Every whitespace run, newlines and tabs included, becomes one space.**
   Newlines are *replaced*, not deleted — deleting them would silently weld
   two words together and change what the title says.
2. **C0 and C1 control characters are removed.** This is what takes the `ESC`
   out of an ANSI sequence; the visible `[31m` remainder is inert text. The
   operator reads this in a terminal too, so the escape problem is theirs as
   much as the recipient's.
3. **Every character that is not a graphic character is removed**, which
   covers the Unicode bidi controls, the line and paragraph separators, and
   the whole zero-width/format class. A right-to-left override reverses the
   display order of everything after it, so a title can make a line read
   backwards on screen while the bytes say something else. A line separator
   writes a new line under `str.splitlines`, which is how every renderer
   here breaks a body. A zero-width space is invisible to a reader and
   breaks any exact-match comparison the text is subjected to. `json.dumps`
   escapes control characters but writes all of these through verbatim under
   `ensure_ascii=False`, which is why the `--json` path needs this and not
   only the message body.
4. **Length is capped, visibly.** A silently clipped title reads as a real
   title that happens to end oddly.

Deliberately *not* an allow-list of characters. Aviso titles are Spanish,
accented, and carry em dashes and parentheses; a rule that stripped non-ASCII
would mangle every real one. The rule removes what has no legitimate place in
a one-line title, and leaves the language alone.

The function is idempotent, because it is applied at more than one boundary
and those boundaries must not have to know about each other.
"""

from __future__ import annotations

import re
import unicodedata

#: Chosen from the 2026-09-04 harvest of all 12 regional SENAMHI pages
#: (12 399 titles). The longest live title, suffix included, is well under 150
#: characters — `PRECIPITACIONES DE MODERADA A FUERTE INTENSIDAD EN LA COSTA
#: NORTE Y SIERRA (EXTENSION DEL AVISO 335)` is 100. 200 leaves room for a
#: longer real title than SENAMHI has ever published while keeping a hostile
#: one from outgrowing the message it is quoted in: the whole body is a
#: handful of short lines, and a title that could run to thousands of
#: characters would push the genuine `Recomendaciones:` off any screen.
MAX_SOURCE_TEXT_LENGTH = 200

#: Visible on purpose: the operator must be able to tell the system cut it.
TRUNCATION_MARKER = "…"

_WHITESPACE_RUN = re.compile(r"\s+")

#: The general categories that mean "this codepoint is not a graphic
#: character". Everything in them is removed, in one place, because two
#: copies of this class is how a bidi override eventually gets through one of
#: them.
#:
#: | Category | What it covers |
#: |---|---|
#: | `Cc` | the C0 controls, DEL and the C1 controls |
#: | `Cf` | soft hyphen, U+061C, U+200B-U+200F, the bidi embeddings, overrides and isolates, the word joiner and the invisible operators, U+FEFF, the interlinear annotations, U+180E, and the whole tags block |
#: | `Cs` | surrogates |
#: | `Co` | private use — whatever the font decides, including nothing |
#: | `Cn` | unassigned — no glyph, and no promise about the next Unicode version |
#: | `Zl`, `Zp` | the line and paragraph separators |
#:
#: **Why a property and not an enumeration.** This class used to list its
#: members, as the table below recorded. A second adversarial review
#: reproduced thirteen invisible characters outside that list, each unflagged
#: by `has_unsafe_characters` *and* surviving `sanitize_source_text` intact.
#: The tags block is the worst of them: it encodes printable ASCII one
#: codepoint per character and is the standard carrier for text a reader
#: cannot see. One tag character inside a second `Recomendaciones:` header
#: was enough — the header rendered identically to the genuine one and
#: compared unequal to it, so the exact-match count returned one while the
#: reader saw two, forged section first. An enumeration is only ever as
#: complete as the last person to think about it, and the rule these two
#: functions have always *meant* is a property, so it is written as one.
#:
#: The enumeration had already been widened once, after the first review, to
#: reach U+2028/U+2029 and the zero-width and format class. Widening it was
#: the wrong repair: it fixed the members that had been thought of and left
#: the shape of the mistake in place. The categories above subsume every
#: range it ever listed.
#:
#: U+00A0 (no-break space), U+202F (narrow no-break space), U+2007 (figure
#: space) and the rest of `Zs` are deliberately *outside* the class: they are
#: visible spacing, not invisible control, and the whitespace rule already
#: collapses every one of them to a single space.
_NON_GRAPHIC_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})

#: The invisibles whose general category is a visible one, so the property
#: above cannot reach them. Each was reproduced surviving both functions.
#:
#: | Codepoint | Category | What |
#: |---|---|---|
#: | U+115F, U+1160, U+3164 | `Lo` | the Hangul fillers — letters with no glyph |
#: | U+2800 | `So` | braille pattern blank — a symbol with no dots |
#: | U+FE00-U+FE0F, U+E0100-U+E01EF | `Mn` | the variation selectors |
#: | U+180B-U+180D | `Mn` | the Mongolian free variation selectors |
#:
#: `Mn` as a whole is emphatically **not** here. A combining tilde is that
#: same category, and `Ferreñafe` written with one has to keep passing —
#: rejecting a correct message over an encoding choice is a defect, not
#: strictness, and it is the city's own name. The variation selectors are
#: therefore named individually rather than taken by property.
_ALSO_INVISIBLE = frozenset(
    chr(codepoint)
    for start, end in (
        (0x115F, 0x1160),
        (0x180B, 0x180D),
        (0x2800, 0x2800),
        (0x3164, 0x3164),
        (0xFE00, 0xFE0F),
        (0xE0100, 0xE01EF),
    )
    for codepoint in range(start, end + 1)
)


def _is_invisible(character: str) -> bool:
    """True when `character` is one a reader cannot see and a parser can."""
    return character in _ALSO_INVISIBLE or unicodedata.category(character) in _NON_GRAPHIC_CATEGORIES


def has_unsafe_characters(value: str) -> bool:
    """True when `value` carries a control or bidi character.

    The question `sanitize_source_text` answers by transforming, asked without
    transforming. `domain.message_validation` needs it that way: a body a
    language model composed is rejected when it carries one of these, never
    quietly cleaned up, because a draft that produced a bidi override produced
    something else wrong too.

    Exposing the predicate rather than letting the validator restate the
    character class keeps that class in one place. Two copies of it is how a
    bidi override eventually gets through one of them.

    Args:
        value: Any text about to be rendered for a human.

    Returns:
        True when at least one invisible character is present — anything in
        `_NON_GRAPHIC_CATEGORIES` or `_ALSO_INVISIBLE`: a C0/C1 control, DEL,
        a Unicode bidi control, a line or paragraph separator, a
        zero-width/format character, a tag character, a variation selector,
        a Hangul filler or the braille blank. Newlines and tabs count: the
        caller knows whether a line break is legitimate where it is looking,
        and this function does not.

    Example:
        >>> has_unsafe_characters("Aviso de lluvias")
        False
        >>> has_unsafe_characters("Aviso\\x1b[31m")
        True
    """
    return any(_is_invisible(character) for character in value)


def sanitize_source_text(value: str) -> str:
    """`value` reduced to one trimmed, control-free, length-capped line.

    Args:
        value: Free text that came from an external source and is about to be
            rendered for a human — a recipient or an operator.

    Returns:
        The same text with every whitespace run collapsed to a single space,
        every invisible character removed, trimmed, and truncated to
        `MAX_SOURCE_TEXT_LENGTH` with `TRUNCATION_MARKER` when it was longer.

    Example:
        >>> sanitize_source_text("Aviso\\nRecomendaciones:")
        'Aviso Recomendaciones:'
    """
    collapsed = _WHITESPACE_RUN.sub(" ", value)
    # After the removals, not before: padding with control characters must not
    # be a way to push real text past the cap.
    cleaned = "".join(character for character in collapsed if not _is_invisible(character)).strip()
    if len(cleaned) <= MAX_SOURCE_TEXT_LENGTH:
        return cleaned
    return cleaned[: MAX_SOURCE_TEXT_LENGTH - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER
