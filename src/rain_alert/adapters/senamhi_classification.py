"""SENAMHI title vocabulary -> domain phenomenon and zone terms.

This is the *adapter* half of the flood-relevance filter (design.md section
5.1): the rule lives in `domain/hazards.py`, the source's words live here,
because the Spanish wording of an aviso title belongs to SENAMHI and not to
the domain.

It is a separate module rather than a private helper inside the scraper on
purpose. The filter decides which official warnings the community is ever
told about, so it needs a first-class test seam and a diff a reviewer can
read, not a regex buried in an HTTP adapter.

Vocabulary evidence — 12 399 rows harvested across the 12 regional pages on
2026-09-04, of which 789 are Lambayeque's. Every one of the 12 399 titles
classifies into a named family; none falls through to `UNKNOWN`:

| Family | Marker stems | Rows (all regions) | Rows (Lambayeque) |
|---|---|---|---|
| precipitation | `PRECIPITACION`, `LLUVIA`, `LLOVIZNA`, `GARUA` | 5 443 | 243 |
| high temperature | `INCREMENTO DE [LA] TEMPERATURA` | 2 339 | 169 |
| wind | `VIENTO` | 2 296 | 291 |
| low temperature | `DESCENSO DE [LA] TEMPERATURA`, `FRIAJE` | 2 259 | 86 |
| snow | `NEVADA` | 62 | 0 |
| hail | `GRANIZO` | 0 | 0 |

`GRANIZO` carried no live row on the harvest date; it is a documented
SENAMHI aviso family, and classifying it costs one pattern while leaving it
out would let hail reach the evaluator as `UNKNOWN`, which this filter reads
as flood-relevant.

Matching is on **normalized** text (accent-stripped, upper-cased,
whitespace-collapsed) and on word boundaries, never on exact strings. The
page carries both `PRECIPITACIONES EN LA COSTA NORTE Y SIERRA` and
`PRECIPITACIONES EN COSTA NORTE Y SIERRA`, publishes temperature avisos both
with and without the article, and appends suffixes like
`(EXTENSION DEL AVISO 335)`, so an exact-string map silently dropped real
warnings — the worst possible failure for this filter.
"""

from __future__ import annotations

import re
import unicodedata

from rain_alert.domain.hazards import Phenomenon, Zone

_WHITESPACE = re.compile(r"\s+")

# Patterns, not phrases, and the first match wins. Three properties are load
# bearing:
#
# 1. **Word boundaries.** `\b` anchors every marker, so `TEMPERATURAS` in
#    prose is not read as the aviso family and `COSTADO` is not read as
#    `COSTA` (in `_COAST` below).
# 2. **Optional article.** SENAMHI publishes the same family both as
#    `INCREMENTO DE TEMPERATURA DIURNA ...` and as
#    `INCREMENTO DE LA TEMPERATURA DIURNA ...` — 56 rows across 9 of the 12
#    regional pages used the article form on 2026-09-04. A fixed phrase
#    matched only the first, so the second classified as `UNKNOWN`, which
#    this filter treats as *relevant*: the heat aviso reached the evaluator.
# 3. **Open-ended stems.** `\bLLUVIA` (not `\bLLUVIA\b`) also matches
#    `LLUVIAS`; `\bVIENTO` also matches `VIENTOS`. Plural and singular are
#    the same hazard.
#
# Precipitation is checked first on purpose. A mixed title such as
# `PRECIPITACIONES Y NEVADA EN LA SIERRA` must resolve to precipitation,
# because erring towards alerting is the posture the whole module takes on
# ambiguity.
_PHENOMENON_MARKERS: tuple[tuple[re.Pattern[str], Phenomenon], ...] = (
    (re.compile(r"\bPRECIPITACION|\bLLUVIA|\bLLOVIZNA|\bGARUA"), Phenomenon.PRECIPITATION),
    (re.compile(r"\bVIENTO"), Phenomenon.WIND),
    (re.compile(r"\bINCREMENTO\s+DE\s+(?:LA\s+)?TEMPERATURA\b"), Phenomenon.HIGH_TEMPERATURE),
    # `FRIAJE` is the Amazonian cold-air incursion, the same cold hazard family
    # as `DESCENSO DE TEMPERATURA`; SENAMHI itself titles some rows
    # `DESCENSO DE LA TEMPERATURA DIURNA EN LA SELVA - VIGESIMO SEPTIMO FRIAJE`.
    (re.compile(r"\bDESCENSO\s+DE\s+(?:LA\s+)?TEMPERATURA\b|\bFRIAJE\b"), Phenomenon.LOW_TEMPERATURE),
    (re.compile(r"\bNEVADA"), Phenomenon.SNOW),
    (re.compile(r"\bGRANIZO"), Phenomenon.HAIL),
)

_COAST = re.compile(r"\bCOSTA\b")
_HIGHLANDS = re.compile(r"\bSIERRA\b")


def normalize_title(title: str) -> str:
    """`title` accent-stripped, upper-cased and whitespace-collapsed."""
    decomposed = unicodedata.normalize("NFKD", title)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return _WHITESPACE.sub(" ", stripped).strip().upper()


def classify_phenomenon(title: str) -> Phenomenon:
    """The hazard `title` is about, or `UNKNOWN` when no marker matches.

    `UNKNOWN` is not a failure: `domain.hazards` treats it as relevant, so an
    unrecognized title errs towards alerting rather than towards silence.
    """
    normalized = normalize_title(title)
    for marker, phenomenon in _PHENOMENON_MARKERS:
        if marker.search(normalized) is not None:
            return phenomenon
    return Phenomenon.UNKNOWN


def classify_zone(title: str) -> Zone:
    """The part of the department `title` covers, or `UNKNOWN` when unstated."""
    normalized = normalize_title(title)
    coast = _COAST.search(normalized) is not None
    highlands = _HIGHLANDS.search(normalized) is not None
    if coast and highlands:
        return Zone.COAST_AND_HIGHLANDS
    if coast:
        return Zone.COAST
    if highlands:
        return Zone.HIGHLANDS
    return Zone.UNKNOWN
