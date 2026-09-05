"""The persisted shape of alerts, outages and structured reasons
(design.md 4.1).

This shape is a carried-forward contract, not a local detail: change 3's
DynamoDB `alerts-sent` adapter reuses it attribute for attribute, so the
round trip is pinned here while it is still cheap to change.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rain_alert.adapters.serialization import (
    alert_record_from_dict,
    alert_record_to_dict,
    outage_record_from_dict,
    outage_record_to_dict,
    reason_from_dict,
    reason_to_dict,
)
from rain_alert.domain.messages import AlertMessage, AlertRecord, OutageRecord
from rain_alert.domain.reasons import (
    ForecastThresholdReason,
    NoQualifyingWarningReason,
    Reason,
    SenamhiUnavailableReason,
    WarningReason,
)
from rain_alert.domain.values import Level, SourceName, TimeWindow, WarningLevel

START = datetime(2026, 9, 4, 12, tzinfo=UTC)
END = datetime(2026, 9, 6, 12, tzinfo=UTC)
WINDOW = TimeWindow(start=START, end=END)

ALL_REASON_VARIANTS: tuple[Reason, ...] = (
    WarningReason(level=WarningLevel.ORANGE, title="PRECIPITACIONES EN LA COSTA NORTE Y SIERRA"),
    ForecastThresholdReason(accumulated_mm=24.0, hours=48, probability_pct=95),
    ForecastThresholdReason(accumulated_mm=9.5, hours=24, probability_pct=70, probability_threshold_pct=70),
    SenamhiUnavailableReason(),
    NoQualifyingWarningReason(),
)


class TestReasonRoundTrip:
    @pytest.mark.parametrize("reason", ALL_REASON_VARIANTS, ids=lambda reason: type(reason).__name__)
    def test_every_reason_variant_survives_a_round_trip(self, reason: Reason) -> None:
        assert reason_from_dict(reason_to_dict(reason)) == reason

    def test_the_tag_is_the_domains_own_reason_kind(self) -> None:
        """`ReasonKind` exists to be the serialization tag, so the stored
        document must not invent a second vocabulary."""
        assert reason_to_dict(SenamhiUnavailableReason())["kind"] == "senamhi_unavailable"
        assert reason_to_dict(ALL_REASON_VARIANTS[0])["kind"] == "official_warning"

    def test_the_numbers_are_stored_as_numbers_not_as_rendered_prose(self) -> None:
        """The audit trail must stay machine-readable: change 3 logs these for
        threshold calibration."""
        stored = reason_to_dict(ForecastThresholdReason(accumulated_mm=24.0, hours=48, probability_pct=95))

        assert stored == {
            "kind": "forecast_threshold",
            "accumulated_mm": 24.0,
            "hours": 48,
            "probability_pct": 95,
            "probability_threshold_pct": None,
        }

    def test_the_optional_threshold_is_preserved_rather_than_dropped(self) -> None:
        reason = ForecastThresholdReason(accumulated_mm=9.5, hours=48, probability_pct=70, probability_threshold_pct=70)

        restored = reason_from_dict(reason_to_dict(reason))

        assert isinstance(restored, ForecastThresholdReason)
        assert restored.probability_threshold_pct == 70

    def test_an_unknown_reason_kind_fails_loudly(self) -> None:
        """A state file written by a newer version must not be read as if a
        reason simply were not there; the audit trail would silently lie."""
        with pytest.raises(ValueError, match="reason kind"):
            reason_from_dict({"kind": "sea_surface_temperature"})

    def test_a_document_with_no_kind_fails_loudly(self) -> None:
        with pytest.raises(ValueError, match="reason kind"):
            reason_from_dict({"accumulated_mm": 1.0})


def _record(**overrides) -> AlertRecord:
    fields = {
        "city_slug": "ferrenafe",
        "level": Level.PREPARE,
        "window": WINDOW,
        "sent_at": START,
        "message": AlertMessage(
            title="Alerta de lluvias — Ferreñafe — prepárate",
            body="Ciudad: Ferreñafe\nNivel: prepárate\nMotivos:\n- Se pronostican 24.0 mm.",
            level=Level.PREPARE,
            valid_until=END,
        ),
        "reasons": ALL_REASON_VARIANTS,
        "senamhi_status": "available",
        "open_meteo_status": "available",
        "composer": "template",
    }
    return AlertRecord(**{**fields, **overrides})


class TestAlertRecordRoundTrip:
    def test_a_record_survives_a_round_trip_with_every_reason_variant(self) -> None:
        record = _record()

        assert alert_record_from_dict(alert_record_to_dict(record)) == record

    def test_a_multiline_spanish_body_survives_intact(self) -> None:
        """The body is the exact text sent to the community; a mangled newline
        or a stripped accent makes the record useless as evidence."""
        record = _record()

        restored = alert_record_from_dict(alert_record_to_dict(record))

        assert restored.message.body == record.message.body
        assert "\n" in restored.message.body
        assert "prepárate" in restored.message.body

    def test_the_stored_attributes_are_the_dynamodb_attribute_names(self) -> None:
        """design.md 4.1 pins these names so change 3's adapter is a
        mechanical mapping with no translation layer."""
        stored = alert_record_to_dict(_record())

        assert set(stored) == {
            "city_slug",
            "level",
            "window_start",
            "window_end",
            "sent_at",
            "title",
            "body",
            "valid_until",
            "reasons",
            "senamhi_status",
            "open_meteo_status",
            "composer",
        }

    def test_the_window_start_is_stored_as_sortable_utc_iso_8601(self) -> None:
        """It is the DynamoDB sort key: a non-UTC offset would sort wrongly
        against its neighbours and silently break the dedup range query."""
        stored = alert_record_to_dict(_record())

        assert stored["window_start"] == "2026-09-04T12:00:00+00:00"
        assert stored["window_start"] < stored["window_end"]

    def test_timestamps_come_back_timezone_aware(self) -> None:
        restored = alert_record_from_dict(alert_record_to_dict(_record()))

        assert restored.window.start.tzinfo is not None
        assert restored.window.start.utcoffset() == UTC.utcoffset(None)
        assert restored.sent_at == START

    def test_a_record_with_no_reasons_round_trips_as_an_empty_tuple(self) -> None:
        restored = alert_record_from_dict(alert_record_to_dict(_record(reasons=())))

        assert restored.reasons == ()

    def test_the_degraded_statuses_are_preserved(self) -> None:
        """Triangulation: both statuses default to "available" above, so a
        serializer that hardcoded them would have passed."""
        restored = alert_record_from_dict(
            alert_record_to_dict(_record(senamhi_status="unavailable", open_meteo_status="available"))
        )

        assert (restored.senamhi_status, restored.open_meteo_status) == ("unavailable", "available")


class TestOutageRecordRoundTrip:
    def test_a_single_source_outage_survives_a_round_trip(self) -> None:
        record = OutageRecord(
            city_slug="ferrenafe",
            unavailable_sources=frozenset({SourceName.SENAMHI}),
            opened_at=START,
            notified_at=END,
        )

        assert outage_record_from_dict(outage_record_to_dict(record)) == record

    def test_a_dual_outage_survives_a_round_trip(self) -> None:
        record = OutageRecord(
            city_slug="ferrenafe",
            unavailable_sources=frozenset({SourceName.SENAMHI, SourceName.OPEN_METEO}),
            opened_at=START,
            notified_at=None,
        )

        restored = outage_record_from_dict(outage_record_to_dict(record))

        assert restored == record
        assert restored.is_dual() is True

    def test_an_un_notified_record_round_trips_as_none_not_as_a_timestamp(self) -> None:
        """`notified_at is None` means "still owed a notice" (design.md 6). If
        the round trip invented a timestamp, the operator would never be told
        about the outage."""
        record = OutageRecord(
            city_slug="ferrenafe",
            unavailable_sources=frozenset({SourceName.SENAMHI}),
            opened_at=START,
            notified_at=None,
        )

        stored = outage_record_to_dict(record)

        assert stored["notified_at"] is None
        assert outage_record_from_dict(stored).notified_at is None

    def test_the_source_set_is_stored_sorted_so_the_document_is_stable(self) -> None:
        """A `frozenset` has no order; an unstable document makes every diff
        of the state file noise."""
        record = OutageRecord(
            city_slug="ferrenafe",
            unavailable_sources=frozenset({SourceName.SENAMHI, SourceName.OPEN_METEO}),
            opened_at=START,
            notified_at=None,
        )

        assert outage_record_to_dict(record)["unavailable_sources"] == ["open_meteo", "senamhi"]

    def test_the_stored_attributes_are_the_dynamodb_attribute_names(self) -> None:
        record = OutageRecord(
            city_slug="ferrenafe",
            unavailable_sources=frozenset({SourceName.SENAMHI}),
            opened_at=START,
            notified_at=None,
        )

        assert set(outage_record_to_dict(record)) == {
            "city_slug",
            "unavailable_sources",
            "opened_at",
            "notified_at",
        }


def test_an_unhandled_reason_variant_fails_loudly_instead_of_serializing_to_null() -> None:
    """W2: `reason_to_dict` had no exhaustiveness guard, and `mypy --strict`
    does not catch a `match` that falls through to an implicit `None`.

    A future `Reason` variant would therefore serialize to `None`, write
    `null` into the state file and into change 3's DynamoDB audit trail, and
    crash the *next read* with `AttributeError` — far from the code that
    caused it, and with the audit trail already corrupted. Failing here is
    deliberate and loud.
    """

    class UnhandledReason:
        """Stands in for a variant added to `Reason` without a case here."""

    with pytest.raises(AssertionError):
        reason_to_dict(UnhandledReason())  # type: ignore[arg-type]
