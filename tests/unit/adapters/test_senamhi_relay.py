"""The relay provider and its pure helpers (senamhi-relay spec; design.md D38).

Pure helpers come first and carry the clock arithmetic as tables, because the
two boundaries that matter (exactly 3 h is stale, a future `LastModified` is
age zero) are exactly the ones an off-by-one survives in.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from rain_alert.adapters.http import FetchError
from rain_alert.adapters.senamhi_relay import (
    MAX_AGE,
    SKEW_TOLERANCE,
    RelayObject,
    RelayReadRecord,
    RelayWarningProvider,
    is_stale,
    relay_age,
    relay_detail,
    skew,
    skew_note,
)
from rain_alert.adapters.senamhi_scraper import SenamhiWarningScraper
from rain_alert.domain.sources import Available, Unavailable
from rain_alert.domain.values import SourceName, UnavailableReason
from tests.support.fakes import FakeRelayReader
from tests.support.fixtures import (
    SENAMHI_ACTIVE_WARNING,
    SENAMHI_BROKEN_STRUCTURE,
    SENAMHI_EMPTY_TABLE,
    SENAMHI_HISTORY_ONLY,
    SENAMHI_PRECIPITATION_COAST,
)

NOW = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
REGION = "Lambayeque"
#: Inside the window of the fixture's one in-force row, as in the scraper tests.
PARSE_NOW = datetime(2026, 9, 4, 12, tzinfo=UTC)


class TestRelayAge:
    def test_age_is_the_cycle_clock_minus_the_s3_last_modified(self) -> None:
        assert relay_age(NOW - timedelta(hours=2, minutes=30), NOW) == timedelta(hours=2, minutes=30)

    def test_a_last_modified_in_the_future_is_age_zero(self) -> None:
        """`default_now()` truncates to whole seconds, so an object written
        during this very second can be up to 1 s ahead of the cycle clock."""
        assert relay_age(NOW + timedelta(seconds=1), NOW) == timedelta(0)


class TestIsStale:
    @pytest.mark.parametrize(
        ("age", "expected"),
        [
            (timedelta(hours=2, minutes=59, seconds=59), False),
            (timedelta(hours=3), True),
            (timedelta(hours=4), True),
            (timedelta(0), False),
        ],
        ids=["2h59m59s-fresh", "exactly-3h-stale", "4h-stale", "zero-fresh"],
    )
    def test_the_limit_is_inclusive_on_the_stale_side(self, age: timedelta, expected: bool) -> None:
        assert is_stale(age, MAX_AGE) is expected

    def test_the_limit_is_a_parameter_not_a_constant(self) -> None:
        assert is_stale(timedelta(minutes=10), timedelta(minutes=5)) is True
        assert is_stale(timedelta(minutes=10), timedelta(minutes=15)) is False


class TestSkew:
    """The producer's claimed `fetched-at` is informational (R1). It is only
    compared with S3's clock to tell the operator the producer clock drifted."""

    def test_skew_is_the_claim_minus_last_modified(self) -> None:
        assert skew(NOW + timedelta(minutes=20), NOW) == timedelta(minutes=20)
        assert skew(NOW - timedelta(minutes=7), NOW) == timedelta(minutes=-7)

    def test_an_absent_claim_has_no_skew(self) -> None:
        assert skew(None, NOW) is None


class TestSkewNote:
    @pytest.mark.parametrize(
        ("claimed", "expected"),
        [
            (NOW + timedelta(minutes=14, seconds=59), None),
            (NOW + timedelta(minutes=15), None),
            (NOW + timedelta(minutes=15, seconds=1), "producer clock skew +15m versus S3 LastModified"),
            (NOW + timedelta(minutes=20), "producer clock skew +20m versus S3 LastModified"),
            (NOW - timedelta(minutes=20), "producer clock skew -20m versus S3 LastModified"),
            (None, None),
        ],
        ids=["just-inside", "exactly-tolerance", "just-over", "ahead-20m", "behind-20m", "no-claim"],
    )
    def test_only_a_skew_over_the_tolerance_is_flagged_with_its_signed_minutes(
        self, claimed: datetime | None, expected: str | None
    ) -> None:
        assert skew_note(claimed, NOW, SKEW_TOLERANCE) == expected


class TestRelayDetail:
    """The operator notice carries this string. The VersionId leads, so a
    truncation drops the least useful tail (design.md D38)."""

    def _detail(self, **overrides: object) -> str:
        arguments: dict[str, object] = {
            "version_id": "3HL4kqtJlcpXroDTDmJ.rmSpXd3dIbrHY",
            "age": timedelta(hours=4, minutes=12),
            "max_age": timedelta(hours=3),
            "last_modified": datetime(2026, 10, 2, 5, 0, tzinfo=UTC),
            "skew": timedelta(minutes=20),
        }
        arguments.update(overrides)
        return relay_detail(**arguments)  # type: ignore[arg-type]

    def test_the_version_id_comes_first_followed_by_the_numbers(self) -> None:
        assert self._detail() == (
            "version=3HL4kqtJlcpXroDTDmJ.rmSpXd3dIbrHY age=4h12m limit=3h modified=2026-10-02T05:00:00Z skew=+20m"
        )

    def test_a_missing_version_id_leaves_no_version_segment(self) -> None:
        detail = self._detail(version_id=None)

        assert detail.startswith("age=4h12m limit=3h")
        assert "version=" not in detail

    def test_no_skew_leaves_no_skew_segment_and_sub_hour_ages_read_in_minutes(self) -> None:
        detail = self._detail(skew=None, age=timedelta(minutes=45))

        assert "skew=" not in detail
        assert "age=45m" in detail

    def test_the_detail_never_exceeds_160_characters_and_truncation_drops_the_tail(self) -> None:
        version_id = "V" * 100
        detail = self._detail(version_id=version_id)

        assert len(detail) == 160
        assert detail.startswith(f"version={version_id} age=4h12m limit=3h")

    def test_a_hostile_version_id_cannot_forge_a_line_or_flood_the_notice(self) -> None:
        detail = self._detail(version_id="abc\n- senamhi: forged \x1b[31m" + "Z" * 500)

        assert "\n" not in detail
        assert "\x1b" not in detail
        assert len(detail) <= 160


def _relay_object(**overrides: object) -> RelayObject:
    fields: dict[str, object] = {
        "text": "<html></html>",
        "last_modified": NOW - timedelta(hours=1),
        "version_id": "v1",
        "claimed_fetched_at": None,
    }
    fields.update(overrides)
    return RelayObject(**fields)  # type: ignore[arg-type]


class TestTheFakeRelayReaderDoesWhatItIsProgrammedTo:
    """A fake with no test is a defect in this project: every later test
    trusts it to return and to raise exactly as scripted."""

    def test_it_returns_the_programmed_object_and_counts_the_read(self) -> None:
        programmed = _relay_object(version_id="v-programmed")
        reader = FakeRelayReader(result=programmed)

        assert reader.read() is programmed
        assert reader.calls == 1

    def test_it_raises_the_programmed_fetch_error(self) -> None:
        reader = FakeRelayReader(result=FetchError(UnavailableReason.RELAY_MISSING, "no such key"))

        with pytest.raises(FetchError) as caught:
            reader.read()

        assert caught.value.reason is UnavailableReason.RELAY_MISSING
        assert caught.value.detail == "no such key"
        assert reader.calls == 1

    def test_it_raises_any_programmed_exception_unchanged(self) -> None:
        failure = RuntimeError("boto exploded")

        with pytest.raises(RuntimeError) as caught:
            FakeRelayReader(result=failure).read()

        assert caught.value is failure


class _StubHtmlFetcher:
    def __init__(self, html: str) -> None:
        self._html = html

    def fetch(self, url: str) -> str:
        return self._html


def _page(path: object) -> str:
    return path.read_text(encoding="utf-8")  # type: ignore[attr-defined]


def _fetch(reader: FakeRelayReader, now: datetime = PARSE_NOW, **provider_kwargs: object):  # type: ignore[no-untyped-def]
    return RelayWarningProvider(reader, **provider_kwargs).fetch_current_warnings(REGION, now)  # type: ignore[arg-type]


class TestAFreshObjectIsParsedExactlyAsTheScraperWouldParseIt:
    @pytest.mark.parametrize(
        ("fixture", "expected_warnings"),
        [(SENAMHI_PRECIPITATION_COAST, 1), (SENAMHI_ACTIVE_WARNING, 0)],
        ids=["flood-relevant-warning-in-force", "heat-warning-discarded-with-a-note"],
    )
    def test_the_fixture_yields_the_scrapers_result_stamped_with_last_modified(
        self, fixture: object, expected_warnings: int
    ) -> None:
        html = _page(fixture)
        modified = PARSE_NOW - timedelta(hours=1)
        scraped = SenamhiWarningScraper(_StubHtmlFetcher(html)).fetch_current_warnings(REGION, PARSE_NOW)

        relayed = _fetch(FakeRelayReader(result=_relay_object(text=html, last_modified=modified)))

        assert isinstance(scraped, Available)
        assert isinstance(relayed, Available)
        assert len(relayed.data) == expected_warnings  # the parse really ran
        assert relayed.data == scraped.data
        assert relayed.notes == scraped.notes
        assert relayed.fetched_at == modified

    def test_a_calm_page_is_an_empty_available_not_a_failure(self) -> None:
        """Triangulation: the non-trivial case above has one warning, this one
        exercises the other parse outcome through the same provider."""
        relayed = _fetch(FakeRelayReader(result=_relay_object(text=_page(SENAMHI_HISTORY_ONLY))))

        assert isinstance(relayed, Available)
        assert relayed.data == ()

    def test_a_producer_clock_far_from_s3_adds_one_skew_note(self) -> None:
        modified = PARSE_NOW - timedelta(hours=1)
        obj = _relay_object(
            text=_page(SENAMHI_ACTIVE_WARNING),
            last_modified=modified,
            claimed_fetched_at=modified + timedelta(minutes=20),
        )

        relayed = _fetch(FakeRelayReader(result=obj))

        assert isinstance(relayed, Available)
        assert relayed.notes[-1] == "producer clock skew +20m versus S3 LastModified"


class TestStalenessIsNeverACalm:
    """A 4 h old page that says "nothing in force" must not read as a calm now:
    the stale object is not parsed at all (senamhi-relay spec)."""

    def test_a_four_hour_old_calm_page_is_stale_not_an_empty_available(self) -> None:
        modified = PARSE_NOW - timedelta(hours=4)
        calm = _relay_object(text=_page(SENAMHI_HISTORY_ONLY), last_modified=modified, version_id="v-stale")

        result = _fetch(FakeRelayReader(result=calm))

        assert isinstance(result, Unavailable)
        assert result.source is SourceName.SENAMHI
        assert result.reason is UnavailableReason.STALE_RELAY
        assert result.observed_at == PARSE_NOW
        assert result.detail.startswith("version=v-stale age=4h limit=3h")

    def test_a_stale_object_is_never_parsed(self) -> None:
        """If the parser ran, this page would be `STRUCTURE_UNRECOGNIZED`."""
        stale_and_broken = _relay_object(
            text=_page(SENAMHI_BROKEN_STRUCTURE), last_modified=PARSE_NOW - timedelta(hours=5)
        )

        result = _fetch(FakeRelayReader(result=stale_and_broken))

        assert isinstance(result, Unavailable)
        assert result.reason is UnavailableReason.STALE_RELAY

    def test_exactly_the_limit_is_stale_and_one_second_inside_is_fresh(self) -> None:
        html = _page(SENAMHI_HISTORY_ONLY)

        at_limit = _fetch(FakeRelayReader(result=_relay_object(text=html, last_modified=PARSE_NOW - MAX_AGE)))
        inside = _fetch(
            FakeRelayReader(result=_relay_object(text=html, last_modified=PARSE_NOW - MAX_AGE + timedelta(seconds=1)))
        )

        assert isinstance(at_limit, Unavailable)
        assert isinstance(inside, Available)

    def test_the_clock_is_the_injected_one_not_the_wall_clock(self) -> None:
        """The same object is fresh at one cycle clock and stale at a later one."""
        html = _page(SENAMHI_HISTORY_ONLY)
        modified = PARSE_NOW - timedelta(hours=1)

        early = _fetch(FakeRelayReader(result=_relay_object(text=html, last_modified=modified)), PARSE_NOW)
        late = _fetch(
            FakeRelayReader(result=_relay_object(text=html, last_modified=modified)), PARSE_NOW + timedelta(hours=3)
        )

        assert isinstance(early, Available)
        assert isinstance(late, Unavailable)
        assert late.reason is UnavailableReason.STALE_RELAY

    def test_the_detail_carries_the_skew_when_the_producer_made_a_claim(self) -> None:
        modified = PARSE_NOW - timedelta(hours=4)
        obj = _relay_object(
            text=_page(SENAMHI_HISTORY_ONLY),
            last_modified=modified,
            claimed_fetched_at=modified + timedelta(minutes=20),
        )

        result = _fetch(FakeRelayReader(result=obj))

        assert isinstance(result, Unavailable)
        assert result.detail.endswith("skew=+20m")


class TestAParseFailureOfAFreshObjectIsTheScrapersFailure:
    """A fresh object the parser rejects reads as the scraper would have read
    the same page, so the operator sees one vocabulary for "the page broke"."""

    @pytest.mark.parametrize(
        "fixture",
        [SENAMHI_BROKEN_STRUCTURE, SENAMHI_EMPTY_TABLE],
        ids=["structure-unrecognized", "no-rows-extracted"],
    )
    def test_the_reason_and_detail_equal_the_scrapers_on_the_same_page(self, fixture: object) -> None:
        html = _page(fixture)
        scraped = SenamhiWarningScraper(_StubHtmlFetcher(html)).fetch_current_warnings(REGION, PARSE_NOW)

        relayed = _fetch(FakeRelayReader(result=_relay_object(text=html, last_modified=PARSE_NOW - timedelta(hours=1))))

        assert isinstance(scraped, Unavailable)
        assert isinstance(relayed, Unavailable)
        assert relayed.reason is scraped.reason
        assert relayed.detail == scraped.detail
        assert relayed.source is SourceName.SENAMHI
        assert relayed.observed_at == PARSE_NOW

    def test_the_two_fixtures_really_do_have_different_reasons(self) -> None:
        """Guards the parametrization above against comparing one reason with itself."""
        reasons = {
            _fetch(FakeRelayReader(result=_relay_object(text=_page(fixture), last_modified=PARSE_NOW))).reason  # type: ignore[union-attr]
            for fixture in (SENAMHI_BROKEN_STRUCTURE, SENAMHI_EMPTY_TABLE)
        }

        assert reasons == {UnavailableReason.STRUCTURE_UNRECOGNIZED, UnavailableReason.NO_ROWS_EXTRACTED}

    def test_a_parser_bug_degrades_the_cycle_as_a_transport_error(self) -> None:
        """Mirrors `senamhi_scraper.py`: a non-FetchError exception must not escape."""
        result = _fetch(FakeRelayReader(result=_relay_object(text=None, last_modified=PARSE_NOW)))  # type: ignore[arg-type]

        assert isinstance(result, Unavailable)
        assert result.reason is UnavailableReason.TRANSPORT_ERROR
        assert result.detail.startswith("unexpected TypeError: ")


class TestNothingEscapesFromAFailingReader:
    @pytest.mark.parametrize(
        "reason",
        [UnavailableReason.RELAY_MISSING, UnavailableReason.RELAY_UNREADABLE],
        ids=lambda reason: reason.value,
    )
    def test_a_fetch_error_maps_through_with_its_reason_and_detail_unchanged(self, reason: UnavailableReason) -> None:
        result = _fetch(FakeRelayReader(result=FetchError(reason, "the reader's own detail")))

        assert isinstance(result, Unavailable)
        assert result.source is SourceName.SENAMHI
        assert result.reason is reason
        assert result.detail == "the reader's own detail"
        assert result.observed_at == PARSE_NOW

    def test_an_arbitrary_exception_becomes_relay_unreadable_naming_its_type(self) -> None:
        result = _fetch(FakeRelayReader(result=RuntimeError("botocore raised something new")))

        assert isinstance(result, Unavailable)
        assert result.reason is UnavailableReason.RELAY_UNREADABLE
        assert result.detail == "unexpected RuntimeError: botocore raised something new"


class TestOnReadIsCalledExactlyOncePerRead:
    """The CloudWatch metric filter reads this record, so a path that skips it
    is a degraded cycle the alarm never sees (design.md D40)."""

    def _records(self, reader: FakeRelayReader) -> tuple[object, list[RelayReadRecord]]:
        records: list[RelayReadRecord] = []
        result = _fetch(reader, on_read=records.append)
        return result, records

    def test_a_healthy_read_reports_degraded_zero_with_the_object_facts(self) -> None:
        modified = PARSE_NOW - timedelta(hours=1)
        obj = _relay_object(
            text=_page(SENAMHI_HISTORY_ONLY),
            last_modified=modified,
            version_id="v-ok",
            claimed_fetched_at=modified + timedelta(minutes=20),
        )

        _, records = self._records(FakeRelayReader(result=obj))

        assert records == [
            RelayReadRecord(
                degraded=0,
                reason=None,
                version_id="v-ok",
                last_modified=modified,
                age_seconds=3600,
                skew_seconds=1200,
            )
        ]

    def test_a_stale_read_reports_degraded_one_with_the_age(self) -> None:
        modified = PARSE_NOW - timedelta(hours=4)
        obj = _relay_object(text=_page(SENAMHI_HISTORY_ONLY), last_modified=modified, version_id="v-old")

        _, records = self._records(FakeRelayReader(result=obj))

        assert records == [
            RelayReadRecord(
                degraded=1,
                reason=UnavailableReason.STALE_RELAY,
                version_id="v-old",
                last_modified=modified,
                age_seconds=14400,
                skew_seconds=None,
            )
        ]

    @pytest.mark.parametrize(
        "reason",
        [UnavailableReason.RELAY_MISSING, UnavailableReason.RELAY_UNREADABLE],
        ids=lambda reason: reason.value,
    )
    def test_a_read_that_never_produced_an_object_reports_only_the_reason(self, reason: UnavailableReason) -> None:
        _, records = self._records(FakeRelayReader(result=FetchError(reason, "x")))

        assert records == [
            RelayReadRecord(
                degraded=1, reason=reason, version_id=None, last_modified=None, age_seconds=None, skew_seconds=None
            )
        ]

    def test_an_arbitrary_reader_exception_is_reported_as_relay_unreadable(self) -> None:
        _, records = self._records(FakeRelayReader(result=RuntimeError("boom")))

        assert [(record.degraded, record.reason) for record in records] == [(1, UnavailableReason.RELAY_UNREADABLE)]

    def test_a_parse_failure_of_a_fresh_object_is_degraded_too(self) -> None:
        """Producer and Lambda parser versions drifting is the only way to get
        here, and it must alarm like any other relay failure (D40)."""
        modified = PARSE_NOW - timedelta(hours=1)
        obj = _relay_object(text=_page(SENAMHI_BROKEN_STRUCTURE), last_modified=modified, version_id="v-bad")

        _, records = self._records(FakeRelayReader(result=obj))

        assert records == [
            RelayReadRecord(
                degraded=1,
                reason=UnavailableReason.STRUCTURE_UNRECOGNIZED,
                version_id="v-bad",
                last_modified=modified,
                age_seconds=3600,
                skew_seconds=None,
            )
        ]

    def test_a_parser_bug_is_degraded_too(self) -> None:
        _, records = self._records(FakeRelayReader(result=_relay_object(text=None, last_modified=PARSE_NOW)))  # type: ignore[arg-type]

        assert [(record.degraded, record.reason) for record in records] == [(1, UnavailableReason.TRANSPORT_ERROR)]

    def test_each_call_emits_its_own_single_record(self) -> None:
        records: list[RelayReadRecord] = []
        provider = RelayWarningProvider(
            FakeRelayReader(result=_relay_object(text=_page(SENAMHI_HISTORY_ONLY))), on_read=records.append
        )

        provider.fetch_current_warnings(REGION, PARSE_NOW)
        provider.fetch_current_warnings(REGION, PARSE_NOW)

        assert len(records) == 2

    def test_the_default_callback_does_nothing_and_does_not_break_the_read(self) -> None:
        result = _fetch(FakeRelayReader(result=_relay_object(text=_page(SENAMHI_HISTORY_ONLY))))

        assert isinstance(result, Available)
