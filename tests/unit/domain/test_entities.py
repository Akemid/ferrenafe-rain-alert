"""Domain entities: Warning, Forecast, RiskAssessment, Contact (design.md
section 3, D10 — probability aggregation is max over the window)."""

from datetime import UTC, datetime, timedelta

import pytest

from rain_alert.domain.entities import Contact, Forecast, HourlyPoint, RiskAssessment, Warning
from rain_alert.domain.values import Coordinates, Level, TimeWindow, WarningLevel


def _utc(hour: int) -> datetime:
    return datetime(2026, 9, 3, 0, tzinfo=UTC) + timedelta(hours=hour)


def _hourly_points(count: int, *, mm: float, probability: int) -> tuple[HourlyPoint, ...]:
    return tuple(HourlyPoint(at=_utc(h), precipitation_mm=mm, probability_pct=probability) for h in range(count))


class TestWarning:
    def test_warning_carries_expected_fields(self) -> None:
        window = TimeWindow(start=_utc(0), end=_utc(6))
        warning = Warning(
            source_id="senamhi-123",
            title="Aviso de lluvias intensas",
            level=WarningLevel.ORANGE,
            region="Lambayeque",
            window=window,
            emitted_at=_utc(0),
        )
        assert warning.source_id == "senamhi-123"
        assert warning.level == WarningLevel.ORANGE
        assert warning.window == window


class TestForecast:
    def test_accumulated_mm_sums_precipitation_over_the_window(self) -> None:
        points = _hourly_points(4, mm=2.5, probability=50)
        forecast = Forecast(location=Coordinates(latitude=-6.64, longitude=-79.79), points=points)
        assert forecast.accumulated_mm(4) == pytest.approx(10.0)

    def test_accumulated_mm_only_counts_the_requested_hours(self) -> None:
        points = _hourly_points(4, mm=2.5, probability=50)
        forecast = Forecast(location=Coordinates(latitude=-6.64, longitude=-79.79), points=points)
        assert forecast.accumulated_mm(2) == pytest.approx(5.0)

    def test_max_probability_pct_is_the_maximum_not_the_mean(self) -> None:
        points = (
            HourlyPoint(at=_utc(0), precipitation_mm=1.0, probability_pct=30),
            HourlyPoint(at=_utc(1), precipitation_mm=1.0, probability_pct=90),
            HourlyPoint(at=_utc(2), precipitation_mm=1.0, probability_pct=40),
        )
        forecast = Forecast(location=Coordinates(latitude=-6.64, longitude=-79.79), points=points)
        assert forecast.max_probability_pct(3) == 90

    def test_peak_hour_is_the_point_with_highest_precipitation(self) -> None:
        points = (
            HourlyPoint(at=_utc(0), precipitation_mm=1.0, probability_pct=30),
            HourlyPoint(at=_utc(1), precipitation_mm=9.0, probability_pct=90),
            HourlyPoint(at=_utc(2), precipitation_mm=2.0, probability_pct=40),
        )
        forecast = Forecast(location=Coordinates(latitude=-6.64, longitude=-79.79), points=points)
        assert forecast.peak_hour(3) == points[1]

    def test_precipitation_window_spans_the_requested_hours(self) -> None:
        points = _hourly_points(4, mm=1.0, probability=50)
        forecast = Forecast(location=Coordinates(latitude=-6.64, longitude=-79.79), points=points)
        window = forecast.precipitation_window(3)
        assert window.start == points[0].at
        assert window.end == points[2].at

    def test_non_contiguous_points_raise(self) -> None:
        points = (
            HourlyPoint(at=_utc(0), precipitation_mm=1.0, probability_pct=30),
            HourlyPoint(at=_utc(2), precipitation_mm=1.0, probability_pct=30),
        )
        with pytest.raises(ValueError, match="contiguous"):
            Forecast(location=Coordinates(latitude=-6.64, longitude=-79.79), points=points)


class TestRiskAssessment:
    def test_risk_assessment_carries_expected_fields(self) -> None:
        window = TimeWindow(start=_utc(0), end=_utc(48))
        assessment = RiskAssessment(
            level=Level.PREPARE,
            window=window,
            reasons=("10.0 mm / 48 h at 60% max probability",),
            senamhi_status="available",
            open_meteo_status="available",
            degraded=False,
        )
        assert assessment.level == Level.PREPARE
        assert assessment.reasons == ("10.0 mm / 48 h at 60% max probability",)
        assert assessment.degraded is False


class TestContact:
    def test_contact_carries_expected_fields(self) -> None:
        contact = Contact(contact_id="operator-local", channel="console", handle="stdout", consent_at=None)
        assert contact.contact_id == "operator-local"
        assert contact.consent_at is None
