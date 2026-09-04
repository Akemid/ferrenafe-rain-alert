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

    def test_precipitation_window_covers_the_sampled_period_not_only_the_sample_starts(self) -> None:
        """An hourly sample at 14:00 describes 14:00-15:00, so a window over
        three samples ends one hour after the last sample's timestamp."""
        points = _hourly_points(4, mm=1.0, probability=50)
        forecast = Forecast(location=Coordinates(latitude=-6.64, longitude=-79.79), points=points)
        window = forecast.precipitation_window(3)
        assert window.start == points[0].at
        assert window.end == points[2].at + timedelta(hours=1)

    def test_precipitation_window_over_48_points_covers_48_hours(self) -> None:
        """W5: `AlertMessage.valid_until` is the window end, so an off-by-one
        hour here declares expiry before the data runs out."""
        points = _hourly_points(48, mm=1.0, probability=50)
        forecast = Forecast(location=Coordinates(latitude=-6.64, longitude=-79.79), points=points)
        window = forecast.precipitation_window(48)
        assert window.end - window.start == timedelta(hours=48)

    def test_single_point_forecast_yields_a_one_hour_window(self) -> None:
        """A one-hour cloudburst must produce a valid window, not a
        `TimeWindow(start == end)` ValueError that aborts the whole cycle."""
        points = _hourly_points(1, mm=25.0, probability=80)
        forecast = Forecast(location=Coordinates(latitude=-6.64, longitude=-79.79), points=points)
        window = forecast.precipitation_window(1)
        assert window.start == points[0].at
        assert window.end == points[0].at + timedelta(hours=1)

    def test_horizon_hours_reports_how_many_hours_the_forecast_covers(self) -> None:
        points = _hourly_points(24, mm=1.0, probability=50)
        forecast = Forecast(location=Coordinates(latitude=-6.64, longitude=-79.79), points=points)
        assert forecast.horizon_hours == 24

    @pytest.mark.parametrize("hours", [0, -1, 25], ids=["zero-hours", "negative-hours", "beyond-the-horizon"])
    def test_accessors_reject_a_horizon_the_forecast_cannot_answer(self, hours: int) -> None:
        """W1: a 24-point forecast must not answer a 48-hour question, and a
        zero-hour slice must not reach `max()` with an empty sequence."""
        points = _hourly_points(24, mm=1.0, probability=50)
        forecast = Forecast(location=Coordinates(latitude=-6.64, longitude=-79.79), points=points)
        for accessor in (
            forecast.accumulated_mm,
            forecast.max_probability_pct,
            forecast.peak_hour,
            forecast.precipitation_window,
        ):
            with pytest.raises(ValueError, match="hours"):
                accessor(hours)

    def test_empty_points_raise(self) -> None:
        """The documented "48 contiguous hourly points" invariant must be
        defended where it is claimed, not left for `peak_hour` to trip over."""
        with pytest.raises(ValueError, match="empty"):
            Forecast(location=Coordinates(latitude=-6.64, longitude=-79.79), points=())

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
