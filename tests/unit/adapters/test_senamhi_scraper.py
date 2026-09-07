"""The SENAMHI warning scraper (weather-sources spec; design.md 8.2, 5.1).

Parsing never touches the network: every case runs against the pinned
fixtures through a stub `HtmlFetcher`.

The clock used throughout is 2026-09-04T12:00Z, which falls inside the
window of the capture's one in-force row (`Inicio 2026-09-04`,
`Fin 2026-09-06`).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rain_alert.adapters.http import FetchError
from rain_alert.adapters.senamhi_scraper import (
    REQUIRED_HEADER_LABELS,
    SenamhiWarningScraper,
    parse_warnings_page,
    warnings_url_for,
)
from rain_alert.domain.hazards import Phenomenon, Zone
from rain_alert.domain.sources import Available, Unavailable
from rain_alert.domain.values import SourceName, UnavailableReason, WarningLevel
from tests.support.fixtures import (
    SENAMHI_ACTIVE_WARNING,
    SENAMHI_BROKEN_STRUCTURE,
    SENAMHI_EMPTY_TABLE,
    SENAMHI_HISTORY_ONLY,
    SENAMHI_PRECIPITATION_COAST,
)

REGION = "Lambayeque"
NOW = datetime(2026, 9, 4, 12, tzinfo=UTC)


class StubHtmlFetcher:
    """`HtmlFetcher` that serves pinned bytes and records the URL asked for."""

    def __init__(self, html: str) -> None:
        self._html = html
        self.urls: list[str] = []

    def fetch(self, url: str) -> str:
        self.urls.append(url)
        return self._html


class FailingHtmlFetcher:
    def __init__(self, error: FetchError) -> None:
        self._error = error

    def fetch(self, url: str) -> str:
        raise self._error


def _html(path) -> str:
    return path.read_text(encoding="utf-8")


def _scrape(path, now: datetime = NOW):
    return SenamhiWarningScraper(StubHtmlFetcher(_html(path))).fetch_current_warnings(REGION, now)


class TestTheWarningInForce:
    def test_a_coastal_precipitation_warning_in_force_is_returned(self) -> None:
        """weather-sources: A coastal precipitation warning in force is returned."""
        result = _scrape(SENAMHI_PRECIPITATION_COAST)

        assert isinstance(result, Available)
        assert len(result.data) == 1
        warning = result.data[0]
        assert warning.title == "PRECIPITACIONES EN LA COSTA NORTE Y SIERRA"
        assert warning.level is WarningLevel.ORANGE
        assert warning.region == REGION
        assert warning.phenomenon is Phenomenon.PRECIPITATION
        assert warning.zone is Zone.COAST_AND_HIGHLANDS
        assert warning.is_flood_relevant is True
        assert result.fetched_at == NOW

    def test_the_window_spans_the_full_inclusive_end_day_in_lima_time(self) -> None:
        """`Inicio 2026-09-04` / `Fin 2026-09-06` is a 3-day aviso in Lima
        time (UTC-5), so it must cover 2026-09-04T05:00Z through
        2026-09-07T05:00Z — not stop at midnight on the end date."""
        result = _scrape(SENAMHI_PRECIPITATION_COAST)

        assert isinstance(result, Available)
        window = result.data[0].window
        assert window.start == datetime(2026, 9, 4, 5, tzinfo=UTC)
        assert window.end == datetime(2026, 9, 7, 5, tzinfo=UTC)
        assert window.start <= NOW <= window.end

    def test_the_source_id_is_the_detail_page_identifier(self) -> None:
        """design.md 8.2: detail pages are not fetched here, but the ID must
        be carried so change 2's `get_warning_detail` tool can use it."""
        result = _scrape(SENAMHI_PRECIPITATION_COAST)

        assert isinstance(result, Available)
        assert result.data[0].source_id == "28705"

    def test_the_title_keeps_its_accents(self) -> None:
        """W3: the normalized form is for *matching*, not for storing.

        `Warning.title` flows into the Spanish community body through
        `WarningSummary`, so storing the accent-stripped form put `EXTENSION`
        in front of recipients in text this codebase otherwise accents
        carefully. Both classifiers normalize internally, so they can be
        handed the raw title.
        """
        mutated = _html(SENAMHI_PRECIPITATION_COAST).replace(
            "PRECIPITACIONES EN LA COSTA NORTE Y SIERRA",
            "PRECIPITACIONES EN LA COSTA NORTE Y SIERRA (EXTENSIÓN DEL AVISO 340)",
        )

        result = SenamhiWarningScraper(StubHtmlFetcher(mutated)).fetch_current_warnings(REGION, NOW)

        assert isinstance(result, Available)
        assert result.data[0].title == "PRECIPITACIONES EN LA COSTA NORTE Y SIERRA (EXTENSIÓN DEL AVISO 340)"
        # …and the raw title still classifies, because both classifiers
        # normalize internally.
        assert result.data[0].phenomenon is Phenomenon.PRECIPITATION
        assert result.data[0].zone is Zone.COAST_AND_HIGHLANDS

    def test_the_emission_date_is_parsed(self) -> None:
        result = _scrape(SENAMHI_PRECIPITATION_COAST)

        assert isinstance(result, Available)
        assert result.data[0].emitted_at == datetime(2026, 9, 2, 5, tzinfo=UTC)


class TestTheMultiHazardFilter:
    """design.md 5.1 — the regression this filter exists to prevent."""

    def test_the_real_captures_red_heat_warning_in_force_yields_zero_warnings(self) -> None:
        """The real page on 2026-09-04 had exactly one warning in force: a RED
        heat warning. Available, and empty — not an imminent flood alert."""
        result = _scrape(SENAMHI_ACTIVE_WARNING)

        assert isinstance(result, Available)
        assert result.data == ()

    def test_the_article_variant_of_the_heat_warning_is_discarded_too(self) -> None:
        """S3b: the article variant is the exact wording that defeated the
        filter once — `INCREMENTO DE LA TEMPERATURA` is not a substring of
        `INCREMENTO DE TEMPERATURA`, so it classified as `UNKNOWN` and reached
        the evaluator as if it were rain. Proven at the classifier, and now
        through the scraper as well, because everything between the two is
        where a filter gets forgotten.
        """
        mutated = _html(SENAMHI_ACTIVE_WARNING).replace(
            "INCREMENTO DE TEMPERATURA DIURNA", "INCREMENTO DE LA TEMPERATURA DIURNA"
        )

        result = SenamhiWarningScraper(StubHtmlFetcher(mutated)).fetch_current_warnings(REGION, NOW)

        assert isinstance(result, Available)
        assert result.data == ()
        assert any(
            "INCREMENTO DE LA TEMPERATURA DIURNA" in note and "high_temperature" in note for note in result.notes
        ), result.notes

    def test_the_heat_warning_is_recorded_as_discarded_with_its_reason(self) -> None:
        """weather-sources: Exclusions are auditable. An empty result from a
        filter must stay distinguishable from an empty result from breakage."""
        outcome = parse_warnings_page(_html(SENAMHI_ACTIVE_WARNING), REGION, NOW)

        assert outcome.warnings == ()
        assert len(outcome.discarded) == 1
        discarded = outcome.discarded[0]
        assert discarded.title == "INCREMENTO DE TEMPERATURA DIURNA EN LA COSTA Y SIERRA"
        assert "high_temperature" in discarded.reason

    def test_the_discard_reaches_the_operator_through_the_port(self) -> None:
        """weather-sources: Exclusions are auditable — *outside* the parser.

        `ParseOutcome.discarded` is only visible to this module's own tests.
        `Available.notes` is what the CLI reads, so this is where a filtered
        page stops being indistinguishable from a broken one for the person
        actually running the system."""
        result = _scrape(SENAMHI_ACTIVE_WARNING)

        assert isinstance(result, Available)
        assert result.data == ()
        assert any(
            "INCREMENTO DE TEMPERATURA DIURNA EN LA COSTA Y SIERRA" in note and "high_temperature" in note
            for note in result.notes
        ), result.notes

    def test_a_calm_healthy_page_carries_no_notes(self) -> None:
        """The other half of the contract: notes must mean something. A page
        that parsed cleanly with nothing in force says nothing."""
        result = _scrape(SENAMHI_HISTORY_ONLY)

        assert isinstance(result, Available)
        assert result.notes == ()

    def test_a_historical_row_anomaly_reaches_the_operator_through_the_port(self) -> None:
        """The partial-parse case: the page is usable, some rows are not, and
        the operator is told so instead of reading a clean `available`."""
        mutated = _html(SENAMHI_PRECIPITATION_COAST).replace(">2026-08-28<", ">28 de agosto<")

        result = SenamhiWarningScraper(StubHtmlFetcher(mutated)).fetch_current_warnings(REGION, NOW)

        assert isinstance(result, Available)
        assert any("unparseable" in note and "28 de agosto" in note for note in result.notes), result.notes

    def test_a_highlands_only_precipitation_warning_in_force_is_returned(self) -> None:
        """weather-sources: A highlands-only precipitation warning is an early
        signal. `PRECIPITACIONES EN LA SIERRA` is the single most common
        precipitation title on the page — 78 of Lambayeque's 789 rows, and
        144 of its 243 precipitation rows are highlands-only. Dropping them
        threw away the upstream signal the Rio La Leche gives the city."""
        mutated = _html(SENAMHI_PRECIPITATION_COAST).replace(
            "PRECIPITACIONES EN LA COSTA NORTE Y SIERRA", "PRECIPITACIONES EN LA SIERRA"
        )

        result = SenamhiWarningScraper(StubHtmlFetcher(mutated)).fetch_current_warnings(REGION, NOW)

        assert isinstance(result, Available)
        assert len(result.data) == 1
        assert result.data[0].zone is Zone.HIGHLANDS
        assert result.data[0].is_flood_relevant is True
        assert result.data[0].may_raise_imminent is False

    def test_a_hostile_title_cannot_forge_an_extra_operator_note(self) -> None:
        """Same class of defect as the community body, one layer over: the CLI
        prints notes as `- senamhi: <note>` bullets, so a title carrying a
        newline writes that grammar itself, and a live escape sequence lands
        in the operator's terminal.

        The title is deliberately still stored raw on `Warning` — normalizing
        it for storage is what once put `EXTENSION` in front of recipients.
        Sanitizing happens where the text is rendered for a human."""
        mutated = _html(SENAMHI_ACTIVE_WARNING).replace(
            "INCREMENTO DE TEMPERATURA DIURNA EN LA COSTA Y SIERRA",
            "INCREMENTO DE TEMPERATURA DIURNA\n- senamhi: FORGED \x1b[31mNOTE\x1b[0m ‮",
        )

        result = SenamhiWarningScraper(StubHtmlFetcher(mutated)).fetch_current_warnings(REGION, NOW)

        assert isinstance(result, Available)
        assert len(result.notes) == 1
        note = result.notes[0]
        assert len(note.splitlines()) == 1
        assert "\x1b" not in note
        assert "‮" not in note
        assert "INCREMENTO DE TEMPERATURA DIURNA" in note

    def test_every_row_is_still_parsed_even_though_one_row_is_returned(self) -> None:
        """The row count is what proves the parse worked, so it must count
        every data row — including the historical and the filtered ones."""
        outcome = parse_warnings_page(_html(SENAMHI_PRECIPITATION_COAST), REGION, NOW)

        assert outcome.rows_seen == 12
        assert len(outcome.warnings) == 1


class TestHistoricalRows:
    def test_a_page_whose_rows_are_all_historical_is_available_and_empty(self) -> None:
        """weather-sources: Healthy page with no current warnings."""
        result = _scrape(SENAMHI_HISTORY_ONLY)

        assert isinstance(result, Available)
        assert result.data == ()

    def test_an_unmarked_row_inside_its_dates_is_still_excluded(self) -> None:
        """weather-sources: An unmarked row inside its window is excluded.
        `history_only.html`'s most recent row carries `Inicio 2026-09-04` /
        `Fin 2026-09-06`, which contain the test clock — so if the marker were
        ignored, this row would be treated as in force."""
        outcome = parse_warnings_page(_html(SENAMHI_HISTORY_ONLY), REGION, NOW)

        assert outcome.warnings == ()
        assert outcome.rows_seen == 12

    def test_a_marked_row_whose_window_has_passed_is_excluded(self) -> None:
        """weather-sources: A marked row whose window has passed is excluded.
        The marker alone is not trusted: a stale `(vigente)` left on an expired
        row would otherwise alert about rain that already passed."""
        outcome = parse_warnings_page(_html(SENAMHI_PRECIPITATION_COAST), REGION, datetime(2026, 9, 20, 12, tzinfo=UTC))

        assert outcome.warnings == ()
        assert outcome.rows_seen == 12

    def test_a_marked_row_before_its_window_starts_is_excluded(self) -> None:
        outcome = parse_warnings_page(_html(SENAMHI_PRECIPITATION_COAST), REGION, datetime(2026, 9, 1, 12, tzinfo=UTC))

        assert outcome.warnings == ()


class TestBreakageIsNeverAFalseCalm:
    """weather-sources: "Scraper breakage yields unavailable, never a false
    calm". The page has carried history since 2024, so a correctly parsed
    table is never empty."""

    def test_an_unrecognized_structure_is_unavailable(self) -> None:
        """weather-sources: Altered HTML structure."""
        result = _scrape(SENAMHI_BROKEN_STRUCTURE)

        assert isinstance(result, Unavailable)
        assert result.reason is UnavailableReason.STRUCTURE_UNRECOGNIZED
        assert result.source is SourceName.SENAMHI
        assert result.observed_at == NOW

    def test_a_header_with_renamed_labels_is_unavailable_not_empty(self) -> None:
        """Triangulation on the header signature: a real table whose labels
        were renamed must fail loudly, not parse to zero warnings."""
        renamed = _html(SENAMHI_PRECIPITATION_COAST).replace("Nivel", "Grado").replace("Inicio", "Desde")

        with pytest.raises(FetchError) as caught:
            parse_warnings_page(renamed, REGION, NOW)

        assert caught.value.reason is UnavailableReason.STRUCTURE_UNRECOGNIZED

    def test_a_header_row_with_zero_data_rows_is_unavailable(self) -> None:
        """weather-sources: Zero rows extracted from a page that should have
        history."""
        result = _scrape(SENAMHI_EMPTY_TABLE)

        assert isinstance(result, Unavailable)
        assert result.reason is UnavailableReason.NO_ROWS_EXTRACTED

    def test_a_page_with_no_table_at_all_is_unavailable(self) -> None:
        result = SenamhiWarningScraper(StubHtmlFetcher("<html><body>maintenance</body></html>")).fetch_current_warnings(
            REGION, NOW
        )

        assert isinstance(result, Unavailable)
        assert result.reason is UnavailableReason.STRUCTURE_UNRECOGNIZED

    def test_a_transport_failure_is_reported_with_its_own_reason(self) -> None:
        scraper = SenamhiWarningScraper(FailingHtmlFetcher(FetchError(UnavailableReason.TIMEOUT, "read timeout")))

        result = scraper.fetch_current_warnings(REGION, NOW)

        assert isinstance(result, Unavailable)
        assert result.reason is UnavailableReason.TIMEOUT
        assert result.detail == "read timeout"

    def test_no_unexpected_exception_escapes_as_anything_but_unavailable(self) -> None:
        """A parser bug must degrade the cycle, not abort it: `RunAlertCycle`
        has a fully tested degraded path and no exception handler."""

        class ExplodingFetcher:
            def fetch(self, url: str) -> str:
                raise RuntimeError("bug in the fetcher")

        result = SenamhiWarningScraper(ExplodingFetcher()).fetch_current_warnings(REGION, NOW)

        assert isinstance(result, Unavailable)
        assert result.reason is UnavailableReason.TRANSPORT_ERROR
        assert "bug in the fetcher" in result.detail


class TestAnUnparseableRowInForceIsStructuralFailure:
    """weather-sources: "Scraper breakage yields unavailable, never a false
    calm" — applied to the *one row that decides the cycle*.

    The share-based structural guard cannot defend this. A breakage confined
    to the single `(vigente)` row is 1 of 789 live rows, or 0.13%, which sails
    past a 50% threshold. Worse, the resulting `available` status routes the
    evaluator to the *stricter* forecast-only branch, so a broken parse makes
    the system less sensitive than a source known to be down — and emits no
    operator notice at all.
    """

    def test_an_unparseable_date_on_the_row_in_force_is_unavailable(self) -> None:
        """The reviewer's reproduction, verbatim: a routine formatting change
        on the start date of the row currently in force. Before this rule the
        CLI reported `available`, `Level none` and zero notices while an
        orange coastal precipitation aviso was in force."""
        mutated = _html(SENAMHI_PRECIPITATION_COAST).replace(
            '<td  class="vigente">2026-09-04</td>', '<td  class="vigente">2026-09-04 08:00</td>'
        )

        result = SenamhiWarningScraper(StubHtmlFetcher(mutated)).fetch_current_warnings(REGION, NOW)

        assert isinstance(result, Unavailable)
        assert result.reason is UnavailableReason.STRUCTURE_UNRECOGNIZED
        assert "2026-09-04 08:00" in result.detail

    def test_a_missing_cell_on_the_row_in_force_is_unavailable(self) -> None:
        """A dropped cell shifts every later column, so the row cannot be read
        by the header index at all — and the row that cannot be read is the
        only one that could have raised the level."""
        mutated = _html(SENAMHI_PRECIPITATION_COAST).replace('<td  class="vigente">71 Hrs. </td>\n', "", 1)

        result = SenamhiWarningScraper(StubHtmlFetcher(mutated)).fetch_current_warnings(REGION, NOW)

        assert isinstance(result, Unavailable)
        assert result.reason is UnavailableReason.STRUCTURE_UNRECOGNIZED

    def test_an_unknown_level_token_on_the_row_in_force_is_unavailable(self) -> None:
        """An unrecognized level on the row in force is the same failure: the
        level is exactly what the evaluator needs from it."""
        mutated = _html(SENAMHI_PRECIPITATION_COAST).replace(">NARANJA<", ">MAGENTA<")

        result = SenamhiWarningScraper(StubHtmlFetcher(mutated)).fetch_current_warnings(REGION, NOW)

        assert isinstance(result, Unavailable)
        assert result.reason is UnavailableReason.STRUCTURE_UNRECOGNIZED
        assert "MAGENTA" in result.detail


class TestRowAnomalies:
    def test_an_unknown_level_token_on_a_historical_row_is_an_anomaly_not_a_guess(self) -> None:
        """design.md 8.2: an unknown token is counted as an anomaly, never
        mapped to a nearby level. A historical row is genuinely skippable —
        it cannot change the verdict."""
        mutated = _html(SENAMHI_PRECIPITATION_COAST).replace(">AMARILLO<", ">MAGENTA<", 1)

        outcome = parse_warnings_page(mutated, REGION, NOW)

        assert any("MAGENTA" in anomaly for anomaly in outcome.anomalies)
        assert len(outcome.warnings) == 1  # the row in force is untouched and still reported

    def test_an_unparseable_date_on_a_historical_row_is_an_anomaly(self) -> None:
        mutated = _html(SENAMHI_PRECIPITATION_COAST).replace(">2026-08-28<", ">28 de agosto<")

        outcome = parse_warnings_page(mutated, REGION, NOW)

        assert any("28 de agosto" in anomaly for anomaly in outcome.anomalies)
        assert len(outcome.warnings) == 1

    def test_a_few_anomalies_do_not_condemn_a_healthy_page(self) -> None:
        """Two bad rows out of twelve are row problems; the page still parsed,
        and the row in force is still reported. `2026-08-30` appears in two
        rows of the fixture (one emission date, one start date), so this
        breaks exactly two."""
        mutated = _html(SENAMHI_PRECIPITATION_COAST).replace(">2026-08-30<", ">who knows<")

        outcome = parse_warnings_page(mutated, REGION, NOW)

        assert len(outcome.anomalies) == 2
        assert len(outcome.warnings) == 1

    def test_mostly_unparseable_rows_mean_the_structure_changed(self) -> None:
        """design.md 8.2: more than half the rows unparseable is a structure
        problem, not twelve independent row problems.

        Only the historical rows are corrupted (`<td >`, not
        `<td  class="vigente">`), so this exercises the *share* guard rather
        than the in-force guard above — otherwise the first data row would
        raise before the share was ever computed.
        """
        mutated = _html(SENAMHI_PRECIPITATION_COAST).replace("<td >2026-0", "<td >fecha-")

        with pytest.raises(FetchError) as caught:
            parse_warnings_page(mutated, REGION, NOW)

        assert caught.value.reason is UnavailableReason.STRUCTURE_UNRECOGNIZED


class TestLevelNormalization:
    @pytest.mark.parametrize(
        ("token", "expected"),
        [("AMARILLO", WarningLevel.YELLOW), ("NARANJA", WarningLevel.ORANGE), ("ROJO", WarningLevel.RED)],
        ids=["amarillo", "naranja", "rojo"],
    )
    def test_each_spanish_level_token_maps_to_its_domain_level(self, token: str, expected: WarningLevel) -> None:
        mutated = _html(SENAMHI_PRECIPITATION_COAST).replace(">NARANJA<", f">{token}<")

        outcome = parse_warnings_page(mutated, REGION, NOW)

        assert [warning.level for warning in outcome.warnings] == [expected]

    def test_the_token_is_matched_accent_and_case_insensitively(self) -> None:
        mutated = _html(SENAMHI_PRECIPITATION_COAST).replace(">NARANJA<", ">Naranja<")

        outcome = parse_warnings_page(mutated, REGION, NOW)

        assert [warning.level for warning in outcome.warnings] == [WarningLevel.ORANGE]


class TestTargetUrl:
    def test_the_url_is_built_from_the_region_the_port_was_given(self) -> None:
        assert warnings_url_for("Lambayeque") == ("https://www.senamhi.gob.pe/?dp=lambayeque&p=aviso-meteorologico")

    def test_region_names_are_slugged_accent_insensitively(self) -> None:
        assert "dp=huanuco" in warnings_url_for("Huánuco")

    def test_the_scraper_asks_for_the_region_it_was_called_with(self) -> None:
        fetcher = StubHtmlFetcher(_html(SENAMHI_PRECIPITATION_COAST))

        SenamhiWarningScraper(fetcher).fetch_current_warnings(REGION, NOW)

        assert fetcher.urls == ["https://www.senamhi.gob.pe/?dp=lambayeque&p=aviso-meteorologico"]


def test_the_header_signature_is_the_live_column_set() -> None:
    """Pinned against the live page of 2026-09-04. Located by header signature
    rather than CSS class or table index, because class names and layout
    wrappers churn far more often than column headers — and an index-based
    selector fails silently where a signature check fails loudly."""
    assert set(REQUIRED_HEADER_LABELS) == {"AVISO", "NRO", "EMISION", "INICIO", "FIN", "DURACION", "NIVEL"}


def test_duplicate_header_labels_do_not_break_the_signature_check() -> None:
    """The live header block repeats its labels, so the check must tolerate
    duplicates instead of requiring an exact cell sequence."""
    doubled = _html(SENAMHI_PRECIPITATION_COAST).replace("<th>Nivel</th>", "<th>Nivel</th><th>Nivel</th><th>Aviso</th>")

    outcome = parse_warnings_page(doubled, REGION, NOW)

    assert len(outcome.warnings) == 1
