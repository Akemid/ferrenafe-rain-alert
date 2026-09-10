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
3. **Unicode bidi controls, separators and the zero-width/format class are
   removed** (U+202A-U+202E, U+2066-U+2069, U+2028-U+2029, U+200B-U+200F,
   U+FEFF and the rest of `_REMOVED`). A right-to-left override reverses the
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

#: Everything that has no legitimate place in a one-line title, in one place,
#: because two copies of this class is how a bidi override eventually gets
#: through one of them. Written as explicit ranges rather than a Unicode
#: category lookup so the class is readable and costs nothing at import.
#:
#: | Range | What |
#: |---|---|
#: | U+0000-U+001F | C0 controls |
#: | U+007F-U+009F | DEL and the C1 controls |
#: | U+00AD | soft hyphen — invisible, and it breaks an exact match |
#: | U+061C | Arabic letter mark |
#: | U+200B-U+200F | zero-width space/non-joiner/joiner, LRM, RLM |
#: | U+2028-U+202E | line and paragraph separators, bidi embeddings/overrides |
#: | U+2060-U+2064 | word joiner and the invisible operators |
#: | U+2066-U+2069 | bidi isolates |
#: | U+FEFF | zero-width no-break space (BOM) |
#: | U+FFF9-U+FFFB | interlinear annotation controls |
#:
#: The two families beyond the original C0/C1/bidi set were added after an
#: adversarial review, and each closes a reproduced hole:
#:
#: - **U+2028 and U+2029.** `\s` matches them, so `sanitize_source_text`
#:   already collapsed them — but they were absent from this class, so
#:   `has_unsafe_characters` called them safe. `str.splitlines`, which every
#:   renderer here uses, splits on U+2028 while `str.split("\n")` does not.
#:   A body carrying one therefore showed the reader two `Recomendaciones:`
#:   sections where the validator had counted one.
#: - **The zero-width and format class.** `\s` does *not* match these, so
#:   they survived sanitizing outright. A zero-width space is invisible to a
#:   reader and fatal to an exact-match comparison: `Recomendaciones` + U+200B
#:   reads as the genuine header and compares unequal to it.
#:
#: U+202F (narrow no-break space) and U+2007 (figure space) are deliberately
#: *outside* these ranges: they are visible spacing, not invisible control,
#: and the whitespace rule already collapses them.
#:
#: Written with escapes rather than the literal characters, because most of
#: them are invisible and a reader has to be able to see what this says.
_REMOVED = re.compile(
    r"[\x00-\x1f\x7f-\x9f\u00ad\u061c\u200b-\u200f\u2028-\u202e\u2060-\u2064\u2066-\u2069\ufeff\ufff9-\ufffb]"
)


def has_unsafe_characters(value: str) -> bool:
    """True when `value` carries a control or bidi character.

    The question `sanitize_source_text` answers by transforming, asked without
    transforming. `domain.message_validation` needs it that way: a body a
    language model composed is rejected when it carries one of these, never
    quietly cleaned up, because a draft that produced a bidi override produced
    something else wrong too.

    Exposing the predicate rather than letting the validator copy `_REMOVED`
    keeps the character class in one place. Two copies of it is how a bidi
    override eventually gets through one of them.

    Args:
        value: Any text about to be rendered for a human.

    Returns:
        True when at least one character of the `_REMOVED` class is present:
        a C0/C1 control, DEL, a Unicode bidi control, a line or paragraph
        separator, or a zero-width/format character. Newlines and tabs
        count: the caller knows whether a line break is legitimate where it
        is looking, and this function does not.

    Example:
        >>> has_unsafe_characters("Aviso de lluvias")
        False
        >>> has_unsafe_characters("Aviso\\x1b[31m")
        True
    """
    return _REMOVED.search(value) is not None


def sanitize_source_text(value: str) -> str:
    """`value` reduced to one trimmed, control-free, length-capped line.

    Args:
        value: Free text that came from an external source and is about to be
            rendered for a human — a recipient or an operator.

    Returns:
        The same text with every whitespace run collapsed to a single space,
        control and bidi characters removed, trimmed, and truncated to
        `MAX_SOURCE_TEXT_LENGTH` with `TRUNCATION_MARKER` when it was longer.

    Example:
        >>> sanitize_source_text("Aviso\\nRecomendaciones:")
        'Aviso Recomendaciones:'
    """
    collapsed = _WHITESPACE_RUN.sub(" ", value)
    # After the removals, not before: padding with control characters must not
    # be a way to push real text past the cap.
    cleaned = _REMOVED.sub("", collapsed).strip()
    if len(cleaned) <= MAX_SOURCE_TEXT_LENGTH:
        return cleaned
    return cleaned[: MAX_SOURCE_TEXT_LENGTH - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER
