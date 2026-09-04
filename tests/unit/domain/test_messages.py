"""Message and record types (design.md section 3, mirrors design spec 7.2/7.4)."""

from datetime import UTC, datetime, timedelta

from rain_alert.domain.messages import (
    AlertMessage,
    AlertRecord,
    ForecastSummary,
    MessageRequest,
    OperatorNotice,
    OutageRecord,
    WarningSummary,
)
from rain_alert.domain.values import Level, NoticeKind, SourceName, TimeWindow, WarningLevel


def _utc(hour: int) -> datetime:
    return datetime(2026, 9, 3, 0, tzinfo=UTC) + timedelta(hours=hour)


class TestForecastSummary:
    def test_forecast_summary_carries_expected_fields(self) -> None:
        summary = ForecastSummary(mm_24h=10.0, mm_48h=15.0, peak_at=_utc(3), peak_probability_pct=70)
        assert summary.mm_24h == 10.0
        assert summary.peak_probability_pct == 70


class TestWarningSummary:
    def test_warning_summary_carries_expected_fields(self) -> None:
        window = TimeWindow(start=_utc(0), end=_utc(6))
        summary = WarningSummary(source_id="senamhi-1", level=WarningLevel.ORANGE, title="Aviso", window=window)
        assert summary.source_id == "senamhi-1"
        assert summary.level == WarningLevel.ORANGE


class TestMessageRequest:
    def test_message_request_carries_expected_fields(self) -> None:
        window = TimeWindow(start=_utc(0), end=_utc(48))
        request = MessageRequest(
            city="Ferreñafe",
            timezone="America/Lima",
            level=Level.PREPARE,
            window=window,
            reasons=("10.0 mm / 48 h at 60% max probability",),
            forecast=ForecastSummary(mm_24h=5.0, mm_48h=10.0, peak_at=_utc(2), peak_probability_pct=60),
            warning=None,
            senamhi_status="available",
            open_meteo_status="available",
            checklist=("Store water", "Secure loose objects"),
        )
        assert request.city == "Ferreñafe"
        assert request.warning is None
        assert request.checklist == ("Store water", "Secure loose objects")


class TestAlertMessage:
    def test_alert_message_carries_expected_fields(self) -> None:
        message = AlertMessage(title="Alerta", body="Cuerpo del mensaje", level=Level.PREPARE, valid_until=_utc(48))
        assert message.title == "Alerta"
        assert message.level == Level.PREPARE


class TestAlertRecord:
    def test_alert_record_carries_expected_fields(self) -> None:
        window = TimeWindow(start=_utc(0), end=_utc(48))
        message = AlertMessage(title="Alerta", body="Cuerpo", level=Level.PREPARE, valid_until=_utc(48))
        record = AlertRecord(
            city_slug="ferrenafe",
            level=Level.PREPARE,
            window=window,
            sent_at=_utc(0),
            message=message,
            reasons=("reason 1",),
            senamhi_status="available",
            open_meteo_status="available",
            composer="template",
        )
        assert record.city_slug == "ferrenafe"
        assert record.composer == "template"


class TestOutageRecord:
    def test_is_dual_true_when_two_sources_unavailable(self) -> None:
        record = OutageRecord(
            city_slug="ferrenafe",
            unavailable_sources=frozenset({SourceName.SENAMHI, SourceName.OPEN_METEO}),
            opened_at=_utc(0),
            notified_at=_utc(0),
        )
        assert record.is_dual() is True

    def test_is_dual_false_when_one_source_unavailable(self) -> None:
        record = OutageRecord(
            city_slug="ferrenafe",
            unavailable_sources=frozenset({SourceName.SENAMHI}),
            opened_at=_utc(0),
            notified_at=None,
        )
        assert record.is_dual() is False


class TestOperatorNotice:
    def test_operator_notice_carries_expected_fields(self) -> None:
        notice = OperatorNotice(
            kind=NoticeKind.SOURCE_UNAVAILABLE,
            subject="SENAMHI unavailable",
            body="SENAMHI has been unavailable since 2026-09-03T00:00:00+00:00",
            sources=frozenset({SourceName.SENAMHI}),
            occurred_at=_utc(0),
        )
        assert notice.kind == NoticeKind.SOURCE_UNAVAILABLE
        assert notice.sources == frozenset({SourceName.SENAMHI})
