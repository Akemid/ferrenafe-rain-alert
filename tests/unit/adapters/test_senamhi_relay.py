"""The relay provider and its pure helpers (senamhi-relay spec; design.md D38).

Pure helpers come first and carry the clock arithmetic as tables, because the
two boundaries that matter (exactly 3 h is stale, a future `LastModified` is
age zero) are exactly the ones an off-by-one survives in.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from rain_alert.adapters.senamhi_relay import (
    MAX_AGE,
    SKEW_TOLERANCE,
    is_stale,
    relay_age,
    relay_detail,
    skew,
    skew_note,
)

NOW = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)


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
