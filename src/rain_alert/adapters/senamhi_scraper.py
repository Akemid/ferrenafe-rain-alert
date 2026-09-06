"""The SENAMHI warning scraper (weather-sources spec; design.md 8.2, 5.1).

Verified against the live Lambayeque page on 2026-09-04: HTTP 200, ~512 KB,
one `<table>`, 790 `<tr>` (one header row plus 788 data rows). Each data row
carries exactly seven `<td>` cells — title, number, emission date, start
date, end date, duration, level — and the row currently in force is marked
by a `(vigente)` token inside the number cell.

Three properties carry this module:

1. **The table is located by header signature**, never by CSS class or table
   index. Class names and layout wrappers churn far more often than column
   headers, and an index-based selector fails *silently* where a signature
   check fails *loudly*. The live header block repeats its labels, so the
   check tolerates duplicates rather than demanding an exact cell sequence.

2. **Breakage is never a false calm.** The page has listed history since
   2024, so a correctly parsed table is never empty. An unrecognized
   structure, zero extracted rows, more than half the rows unparseable, **or
   any anomaly on the row marked `(vigente)`** all yield `Unavailable`.
   `Available` with an empty tuple means one specific thing: the page parsed,
   and nothing relevant is in force.

   The last of those four is not redundant with the share guard, and it is
   the one that matters most. Breakage confined to the single row in force is
   1 of 789 rows — 0.13% — so the share guard waves it through, and the
   result is `available` with zero warnings while a red aviso is in force.
   That is worse than a declared outage: `available` routes the evaluator to
   the stricter forecast-only threshold, so a broken parse makes the system
   *less* sensitive than a source known to be down, and emits no operator
   notice. See `_refuse_if_in_force`.

   Everything the parse did set aside travels out on `Available.notes`, so a
   partial failure is visible to the operator instead of only to the tests.

3. **Only warnings that are both in force and flood-relevant are returned**
   (design.md 5.1). "In force" needs the `(vigente)` marker *and* a
   start/end window containing `now` — a stale marker on an expired row would
   otherwise alert about rain that already passed. Relevance is the domain
   rule in `domain/hazards.py`. Every exclusion is recorded in the parse
   outcome, so a filtered page stays distinguishable from a broken one.

Detail pages are not fetched here; `Warning.source_id` carries the detail-page
identifier so change 2's `get_warning_detail` tool can use it.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from bs4.element import Tag

from rain_alert.adapters.http import FetchError, HtmlFetcher
from rain_alert.adapters.senamhi_classification import classify_phenomenon, classify_zone
from rain_alert.domain.entities import Warning
from rain_alert.domain.hazards import discard_reason
from rain_alert.domain.sanitize import sanitize_source_text
from rain_alert.domain.sources import Available, SourceResult, Unavailable
from rain_alert.domain.values import SourceName, TimeWindow, UnavailableReason, WarningLevel

BASE_URL = "https://www.senamhi.gob.pe/"
PAGE = "aviso-meteorologico"

#: The live column set, pinned 2026-09-04. Normalized: accent-stripped,
#: upper-cased, trailing punctuation removed (`Nro.` -> `NRO`).
REQUIRED_HEADER_LABELS = frozenset({"AVISO", "NRO", "EMISION", "INICIO", "FIN", "DURACION", "NIVEL"})

#: Which normalized header label feeds which field.
_TITLE, _NUMBER, _EMITTED, _START, _END, _LEVEL = "AVISO", "NRO", "EMISION", "INICIO", "FIN", "NIVEL"

_LEVEL_TOKENS = {"AMARILLO": WarningLevel.YELLOW, "NARANJA": WarningLevel.ORANGE, "ROJO": WarningLevel.RED}

_IN_FORCE_MARKER = "VIGENTE"
_DETAIL_ID = re.compile(r"[?&]b=(\d+)")
_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y")

#: Warning dates are published as calendar days in Peruvian local time.
_PERU = ZoneInfo("America/Lima")

#: Above this share of unparseable rows the *structure* changed, rather than
#: this many rows independently going bad (design.md 8.2).
_MAX_ANOMALY_SHARE = 0.5


@dataclass(frozen=True, slots=True)
class DiscardedWarning:
    """A row that parsed cleanly but was filtered out, and why.

    The operator audit trail needs this: without it, "the filter removed
    everything" and "the scraper is broken" look identical from the outside.
    """

    source_id: str
    title: str
    reason: str


@dataclass(frozen=True, slots=True)
class ParseOutcome:
    """Everything one parse of the page produced.

    `rows_seen` counts *every* data row, including historical and filtered
    ones, because the row count is what proves the parse worked.
    """

    warnings: tuple[Warning, ...]
    rows_seen: int
    anomalies: tuple[str, ...]
    discarded: tuple[DiscardedWarning, ...]


def parse_notes(outcome: ParseOutcome) -> tuple[str, ...]:
    """What this parse set aside, as operator-facing lines (`Available.notes`).

    Discards are listed one by one: they are bounded by the number of rows
    actually in force, which is a handful, and each one is a warning the
    community was *not* told about. Anomalies are summarized rather than
    listed, because a structural change can produce hundreds and the operator
    needs the fact that rows were lost, not a wall of them.

    Every source-derived fragment is sanitized here, and only here. A note is
    printed by the CLI as a `- senamhi: <note>` bullet and read in a terminal,
    so a title or a number cell carrying a newline forges an extra note line
    and an ANSI escape executes — the same defect as the community body, one
    layer over. Sanitizing the fragments rather than the finished line keeps
    the length cap on the source text and leaves the note's own wording whole.
    """
    notes = [
        f"discarded warning {sanitize_source_text(item.source_id)} ({sanitize_source_text(item.title)}): {item.reason}"
        for item in outcome.discarded
    ]
    if outcome.anomalies:
        notes.append(
            f"{len(outcome.anomalies)} of {outcome.rows_seen} rows were unparseable "
            f"and skipped; first: {sanitize_source_text(outcome.anomalies[0])}"
        )
    return tuple(notes)


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", stripped).strip().upper()


def _normalize_label(text: str) -> str:
    return _normalize(text).rstrip(".:")


def region_slug(region: str) -> str:
    """`region` as the page's `dp` parameter (accent-stripped, lower-cased)."""
    return _normalize(region).lower().replace(" ", "")


def warnings_url_for(region: str) -> str:
    """The warnings page for one region."""
    return f"{BASE_URL}?dp={region_slug(region)}&p={PAGE}"


def _cell_texts(row: Tag, name: str) -> list[str]:
    return [cell.get_text(" ", strip=True) for cell in row.find_all(name)]


def _header_index(row: Tag) -> dict[str, int] | None:
    """Column indexes by normalized label, or `None` if this is not the header.

    The first occurrence of each label wins: the live header block repeats its
    labels, and a duplicate must not shift the mapping.
    """
    labels = [_normalize_label(text) for text in _cell_texts(row, "th") or _cell_texts(row, "td")]
    if not REQUIRED_HEADER_LABELS.issubset(labels):
        return None
    index: dict[str, int] = {}
    for position, label in enumerate(labels):
        index.setdefault(label, position)
    return index


def _locate_table(soup: BeautifulSoup) -> tuple[Tag, dict[str, int], int]:
    """The warnings table, its column index, and its header row position."""
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        for position, row in enumerate(rows):
            index = _header_index(row)
            if index is not None:
                return table, index, position
    raise FetchError(
        UnavailableReason.STRUCTURE_UNRECOGNIZED,
        f"no table carries the expected header labels {sorted(REQUIRED_HEADER_LABELS)}",
    )


def _parse_day(text: str) -> datetime:
    """A published calendar day as midnight in Peruvian local time, in UTC."""
    for pattern in _DATE_FORMATS:
        try:
            naive = datetime.strptime(text.strip(), pattern)
        except ValueError:
            continue
        return naive.replace(tzinfo=_PERU).astimezone(UTC)
    raise ValueError(f"unparseable date {text!r}")


def _parse_level(text: str) -> WarningLevel:
    token = _normalize(text)
    if token not in _LEVEL_TOKENS:
        raise ValueError(f"unknown level token {token!r}")
    return _LEVEL_TOKENS[token]


def _source_id(cell: Tag, fallback: str) -> str:
    """The detail-page identifier, from the row's link when it has one."""
    for link in cell.find_all("a", href=True):
        found = _DETAIL_ID.search(str(link["href"]))
        if found:
            return found.group(1)
    return fallback


def _is_marked_in_force(number_text: str) -> bool:
    return _IN_FORCE_MARKER in _normalize(number_text)


def _row_is_marked_in_force(row: Tag) -> bool:
    """Whether the `(vigente)` marker appears anywhere in this row's text.

    Deliberately coarser than `_is_marked_in_force`, which reads the number
    cell by header index. This one is asked *when the cells cannot be trusted*
    — a dropped cell shifts every later column, so there is no reliable number
    cell to read. Scanning the whole row errs towards declaring a structural
    failure, which is the safe direction: no live title in the 12 399 rows
    harvested on 2026-09-04 contains the word, and the `(vigente)` link target
    lives in an `href`, which `get_text` does not return.
    """
    return _IN_FORCE_MARKER in _normalize(row.get_text(" ", strip=True))


def _build_warning(cells: list[Tag], index: dict[str, int], region: str, source_id: str) -> Warning:
    """One row as a domain `Warning`. Raises `ValueError` on an unparseable cell."""
    # The title is stored **raw**. Normalization is for matching, not for
    # storing: `Warning.title` reaches the Spanish community body through
    # `WarningSummary`, and the accent-stripped form put `EXTENSION` in front
    # of recipients. Both classifiers normalize internally, so they can be
    # handed the raw title.
    title = cells[index[_TITLE]].get_text(" ", strip=True)
    start = _parse_day(cells[index[_START]].get_text(" ", strip=True))
    # The end date is inclusive, so the window runs to the end of that day.
    end = _parse_day(cells[index[_END]].get_text(" ", strip=True)) + timedelta(days=1)
    return Warning(
        source_id=source_id,
        title=title,
        level=_parse_level(cells[index[_LEVEL]].get_text(" ", strip=True)),
        region=region,
        window=TimeWindow(start=start, end=end),
        emitted_at=_parse_day(cells[index[_EMITTED]].get_text(" ", strip=True)),
        phenomenon=classify_phenomenon(title),
        zone=classify_zone(title),
    )


def _refuse_if_in_force(row: Tag, anomaly: str) -> None:
    """Raise when the row that could not be parsed is the row in force.

    An anomaly on a historical row is genuinely skippable — that row cannot
    change the verdict. An anomaly on the `(vigente)` row is the *whole*
    verdict going missing, and skipping it produces the worst outcome this
    scraper has: `Available` with an empty tuple, which reads as "the page
    parsed and nothing is in force". The share guard below cannot catch it,
    because one bad row out of 789 is 0.13%.

    It is worse than a declared outage. With `senamhi_status == "available"`
    the evaluator routes to the forecast-only branch at the *stricter* 70%
    probability threshold, so a broken parse leaves the system less sensitive
    than a source known to be down, and no operator notice is emitted.

    Raises:
        FetchError: `STRUCTURE_UNRECOGNIZED`, naming the anomaly.
    """
    if _row_is_marked_in_force(row):
        raise FetchError(
            UnavailableReason.STRUCTURE_UNRECOGNIZED,
            f"the row currently in force could not be parsed ({anomaly}); "
            "reporting unavailable rather than an empty warning list",
        )


def parse_warnings_page(html: str, region: str, now: datetime) -> ParseOutcome:
    """Parse the warnings page into the warnings currently driving risk.

    Args:
        html: The page body.
        region: The region name to stamp on each warning.
        now: The cycle clock, used for the in-force window check.

    Returns:
        A `ParseOutcome` whose `warnings` are in force *and* flood-relevant,
        with every other row accounted for in `rows_seen`, `anomalies` or
        `discarded`.

    Raises:
        FetchError: `STRUCTURE_UNRECOGNIZED` when no table carries the header
            signature or most rows are unparseable, `NO_ROWS_EXTRACTED` when
            the table has no data rows at all.
    """
    table, index, header_position = _locate_table(BeautifulSoup(html, "html.parser"))
    widest = max(index.values())

    data_rows = [row for row in table.find_all("tr")[header_position + 1 :] if row.find_all("td")]
    if not data_rows:
        raise FetchError(
            UnavailableReason.NO_ROWS_EXTRACTED,
            "the table carries the expected headers but no data rows; this page has listed history since 2024",
        )

    warnings: list[Warning] = []
    anomalies: list[str] = []
    discarded: list[DiscardedWarning] = []

    for row in data_rows:
        cells = row.find_all("td")
        if len(cells) <= widest:
            anomaly = f"row has {len(cells)} cells, expected more than {widest}"
            _refuse_if_in_force(row, anomaly)
            anomalies.append(anomaly)
            continue
        number_text = cells[index[_NUMBER]].get_text(" ", strip=True)
        source_id = _source_id(cells[index[_NUMBER]], number_text)
        try:
            warning = _build_warning(cells, index, region, source_id)
        except ValueError as exc:
            anomaly = f"row {source_id}: {exc}"
            _refuse_if_in_force(row, anomaly)
            anomalies.append(anomaly)
            continue

        # Both checks are required. The marker alone can be stale, and the
        # dates alone match many historical rows (weather-sources spec).
        if not (_is_marked_in_force(number_text) and warning.window.start <= now <= warning.window.end):
            continue

        excluded = discard_reason(warning.phenomenon)
        if excluded is not None:
            discarded.append(DiscardedWarning(source_id=source_id, title=warning.title, reason=excluded))
            continue
        warnings.append(warning)

    if len(anomalies) > _MAX_ANOMALY_SHARE * len(data_rows):
        raise FetchError(
            UnavailableReason.STRUCTURE_UNRECOGNIZED,
            f"{len(anomalies)} of {len(data_rows)} rows were unparseable; the page structure has changed",
        )

    return ParseOutcome(
        warnings=tuple(warnings),
        rows_seen=len(data_rows),
        anomalies=tuple(anomalies),
        discarded=tuple(discarded),
    )


class SenamhiWarningScraper:
    """`WarningProvider` over the SENAMHI regional warnings page.

    The fetch is an injected `HtmlFetcher`, so parsing is unit-tested against
    pinned fixtures with zero HTTP (design.md 8.2).
    """

    def __init__(self, fetcher: HtmlFetcher, base_url: str | None = None) -> None:
        self._fetcher = fetcher
        self._base_url = base_url

    def fetch_current_warnings(self, region: str, now: datetime) -> SourceResult[tuple[Warning, ...]]:
        """Fetch and parse the region's warnings, reporting failure as `Unavailable`.

        No exception escapes. A transport failure, an unrecognized structure
        and an outright parser bug all become `Unavailable` with a
        machine-readable reason, because `RunAlertCycle` has a fully tested
        degraded path and no exception handler.
        """
        url = self._base_url or warnings_url_for(region)
        try:
            html = self._fetcher.fetch(url)
            outcome = parse_warnings_page(html, region, now)
        except FetchError as exc:
            return Unavailable(source=SourceName.SENAMHI, reason=exc.reason, detail=exc.detail, observed_at=now)
        except Exception as exc:  # noqa: BLE001 - a parser bug must degrade the cycle, not abort it
            return Unavailable(
                source=SourceName.SENAMHI,
                reason=UnavailableReason.TRANSPORT_ERROR,
                detail=f"unexpected {type(exc).__name__}: {exc}",
                observed_at=now,
            )
        return Available(data=outcome.warnings, fetched_at=now, notes=parse_notes(outcome))
