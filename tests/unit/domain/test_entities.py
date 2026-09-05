"""`Forecast` behaviour: accumulation, D10 max-over-window probability, peak
hour, window coverage, and the invariants `__post_init__` defends (design.md
section 3).

The other entities in this module are frozen dataclasses with no behaviour;
field-presence tests for them were pruned as tautological — `mypy --strict`
already catches a field rename.
"""

from datetime import UTC, datetime, timedelta

import pytest

from rain_alert.domain.entities import Forecast, HourlyPoint, Warning
from rain_alert.domain.hazards import Phenomenon, Zone
from rain_alert.domain.values import Coordinates, TimeWindow, WarningLevel


def _utc(hour: int) -> datetime:
    return datetime(2026, 9, 3, 0, tzinfo=UTC) + timedelta(hours=hour)


def _hourly_points(count: int, *, mm: float, probability: int) -> tuple[HourlyPoint, ...]:
    return tuple(HourlyPoint(at=_utc(h), precipitation_mm=mm, probability_pct=probability) for h in range(count))


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


class TestWarningFloodRelevance:
    """`Warning` carries the phenomenon and zone the adapter inferred, so the
    flood-relevance decision is inspectable on the value itself rather than
    hidden in whichever code filtered it (design.md section 5.1)."""

    def _warning(self, *, title: str, phenomenon: Phenomenon, zone: Zone) -> Warning:
        return Warning(
            source_id="28705",
            title=title,
            level=WarningLevel.RED,
            region="Lambayeque",
            window=TimeWindow(start=_utc(0), end=_utc(48)),
            emitted_at=_utc(0),
            phenomenon=phenomenon,
            zone=zone,
        )

    def test_a_coastal_precipitation_warning_is_flood_relevant(self) -> None:
        warning = self._warning(
            title="PRECIPITACIONES EN LA COSTA NORTE Y SIERRA",
            phenomenon=Phenomenon.PRECIPITATION,
            zone=Zone.COAST_AND_HIGHLANDS,
        )
        assert warning.is_flood_relevant is True

    def test_a_red_heat_warning_is_not_flood_relevant(self) -> None:
        """The 2026-09-04 regression case: red, in force, and irrelevant."""
        warning = self._warning(
            title="INCREMENTO DE TEMPERATURA DIURNA EN LA COSTA Y SIERRA",
            phenomenon=Phenomenon.HIGH_TEMPERATURE,
            zone=Zone.COAST_AND_HIGHLANDS,
        )
        assert warning.is_flood_relevant is False

    def test_an_unclassified_warning_defaults_to_relevant(self) -> None:
        """The default must fail safe: a caller that forgets to classify
        produces one extra alert, never a missed flood warning."""
        warning = Warning(
            source_id="28705",
            title="PRECIPITACIONES EN LA COSTA",
            level=WarningLevel.ORANGE,
            region="Lambayeque",
            window=TimeWindow(start=_utc(0), end=_utc(48)),
            emitted_at=_utc(0),
        )
        assert (warning.phenomenon, warning.zone) == (Phenomenon.UNKNOWN, Zone.UNKNOWN)
        assert warning.is_flood_relevant is True
        assert warning.may_raise_imminent is True

    def test_a_highlands_only_rainfall_warning_is_relevant_but_not_imminent_capable(self) -> None:
        """The owner-approved correction: sierra rain is upstream of the city
        in the Rio La Leche basin, so it prepares rather than declares."""
        warning = self._warning(
            title="PRECIPITACIONES EN LA SIERRA",
            phenomenon=Phenomenon.PRECIPITATION,
            zone=Zone.HIGHLANDS,
        )
        assert warning.is_flood_relevant is True
        assert warning.may_raise_imminent is False

    def test_a_coastal_rainfall_warning_is_imminent_capable(self) -> None:
        warning = self._warning(
            title="PRECIPITACIONES EN LA COSTA",
            phenomenon=Phenomenon.PRECIPITATION,
            zone=Zone.COAST,
        )
        assert warning.may_raise_imminent is True
