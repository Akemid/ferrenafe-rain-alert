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
3. **Unicode bidi controls are removed** (U+202A-U+202E, U+2066-U+2069). A
   right-to-left override reverses the display order of everything after it,
   so a title can make a line read backwards on screen while the bytes say
   something else. `json.dumps` escapes control characters but writes these
   through verbatim under `ensure_ascii=False`, which is why the `--json`
   path needs this and not only the message body.
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

#: C0 (U+0000-U+001F), DEL and C1 (U+007F-U+009F), and the Unicode bidi
#: controls: the embeddings/overrides U+202A-U+202E and the isolates
#: U+2066-U+2069.
_REMOVED = re.compile(r"[\x00-\x1f\x7f-\x9f‪-‮⁦-⁩]")


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
