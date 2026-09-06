"""Live SENAMHI structure canary — **opt-in** (design.md 8.2, 10).

Deselected from the default suite by `addopts = "-m 'not integration'"`. Run
deliberately:

    uv run pytest -m integration

This is **the early warning for scraper breakage**, and it is the only test
that can be. The pinned fixtures prove the parser handles the structure as
captured; nothing offline can notice the live page changing. When this
fails, the fixtures need re-capturing and the parser probably needs work —
see `tests/fixtures/senamhi/README.md`.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

import pytest
from bs4 import BeautifulSoup

from rain_alert.adapters.http import HttpxHtmlFetcher
from rain_alert.adapters.senamhi_classification import classify_phenomenon, normalize_title
from rain_alert.adapters.senamhi_scraper import (
    SenamhiWarningScraper,
    parse_warnings_page,
    warnings_url_for,
)
from rain_alert.domain.hazards import Phenomenon
from rain_alert.domain.sources import Available

REGION = "Lambayeque"

pytestmark = pytest.mark.integration


def _live_page() -> str:
    return HttpxHtmlFetcher().fetch(warnings_url_for(REGION))


def test_the_live_page_still_matches_the_header_signature_and_yields_rows() -> None:
    """If the header labels or the table structure change, this is where it
    surfaces — as a failure, never as a quiet `available` with zero rows."""
    outcome = parse_warnings_page(_live_page(), REGION, datetime.now(UTC))

    # The page has listed history since 2024, so a correct parse is never
    # empty. A low bound rather than an exact count: the history grows.
    assert outcome.rows_seen > 100
    assert len(outcome.anomalies) < 0.5 * outcome.rows_seen


#: Marker stems the classifier claims to recognize, and the family each must
#: land in. A title carrying one of these and still classifying as `UNKNOWN`
#: is a *defect*, not drift: the classifier no longer honours its own
#: vocabulary. Checked per row rather than in aggregate, because the row that
#: matters is the single one in force.
_KNOWN_FAMILY_STEMS: tuple[tuple[str, Phenomenon], ...] = (
    ("PRECIPITACION", Phenomenon.PRECIPITATION),
    ("LLUVIA", Phenomenon.PRECIPITATION),
    ("LLOVIZNA", Phenomenon.PRECIPITATION),
    ("GARUA", Phenomenon.PRECIPITATION),
    ("VIENTO", Phenomenon.WIND),
    ("NEVADA", Phenomenon.SNOW),
    ("GRANIZO", Phenomenon.HAIL),
    ("FRIAJE", Phenomenon.LOW_TEMPERATURE),
)


def _live_titles() -> list[str]:
    return [
        row.find_all("td")[0].get_text(" ", strip=True)
        for row in BeautifulSoup(_live_page(), "html.parser").find_all("tr")
        if row.find_all("td")
    ]


def test_the_live_page_is_still_a_multi_hazard_feed_the_classifier_recognizes() -> None:
    """The premise of design.md 5.1, re-checked against the live vocabulary.

    Two ways this can fail, both worth knowing about: the page becomes
    precipitation-only (the filter is then redundant), or the classifier stops
    recognizing the titles and everything falls through to `UNKNOWN` — which
    fails *safe* for a missed flood but silently disables the filter, so a
    heat aviso in force would raise `imminent`.
    """
    titles = _live_titles()
    assert len(titles) > 100

    phenomena = Counter(classify_phenomenon(title) for title in titles)

    assert phenomena[Phenomenon.PRECIPITATION] > 0, "no precipitation hazard found; is this the right page?"
    assert (
        phenomena[Phenomenon.WIND] + phenomena[Phenomenon.HIGH_TEMPERATURE] + phenomena[Phenomenon.LOW_TEMPERATURE] > 0
    ), "no non-precipitation hazard found; the filter may have become redundant"


def test_no_live_title_at_all_falls_through_to_unknown() -> None:
    """Zero unknowns, not "few" unknowns.

    A share-based bound (`unknown < 10% of rows`) cannot catch the one row
    that decides a cycle: 789 rows of history dilute a single broken in-force
    title to 0.13%. Every title SENAMHI publishes belongs to a family with a
    known answer, so any unknown is a vocabulary change worth a human look.
    """
    unknown = sorted({title for title in _live_titles() if classify_phenomenon(title) is Phenomenon.UNKNOWN})

    assert unknown == [], (
        f"{len(unknown)} distinct titles are unclassified, e.g. {unknown[:5]}; "
        "the title vocabulary has drifted and every unclassified family is read as flood-relevant"
    )


@pytest.mark.parametrize(("stem", "expected"), _KNOWN_FAMILY_STEMS, ids=[stem for stem, _ in _KNOWN_FAMILY_STEMS])
def test_every_live_title_of_a_known_family_classifies_into_that_family(stem: str, expected: Phenomenon) -> None:
    """Per-family, per-row: a `NEVADA` row must classify as snow on *every*
    row, not on most of them. Families absent from today's page are skipped
    rather than asserted, because the page's mix is seasonal."""
    matching = [title for title in _live_titles() if stem in normalize_title(title)]
    if not matching:
        pytest.skip(f"no {stem} row in force on the live page today")

    misread = sorted({title for title in matching if classify_phenomenon(title) is not expected})

    assert misread == [], f"{len(misread)} {stem} titles did not classify as {expected.value}: {misread[:5]}"


def test_every_returned_live_warning_is_flood_relevant() -> None:
    """Whatever is in force today, nothing that cannot flood a coastal city
    may reach the evaluator."""
    outcome = parse_warnings_page(_live_page(), REGION, datetime.now(UTC))

    assert all(warning.is_flood_relevant for warning in outcome.warnings)
    assert all(warning.phenomenon is not Phenomenon.WIND for warning in outcome.warnings)


def test_the_scraper_reports_available_against_the_live_page() -> None:
    """The port-level result the cycle actually consumes."""
    result = SenamhiWarningScraper(HttpxHtmlFetcher()).fetch_current_warnings(REGION, datetime.now(UTC))

    assert isinstance(result, Available), getattr(result, "detail", result)
    assert all(warning.region == REGION for warning in result.data)
