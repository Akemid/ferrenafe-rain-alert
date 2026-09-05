"""The fixture-backed adapters behind `--offline-fixtures` (design.md 9).

They swap the fetch leaf only, so the real `parse_forecast_payload` and the
real `SenamhiWarningScraper` still do the work. That is what makes the
offline path trustworthy for calibration — and it is also why the offline
provider owes the same "no exception escapes" guarantee the live one does.
`RunAlertCycle` has no exception handler, so anything that escapes here kills
the cycle.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from rain_alert.adapters.local.offline_sources import FileHtmlFetcher, OfflineOpenMeteoProvider
from rain_alert.domain.sources import Available, Unavailable
from rain_alert.domain.values import Coordinates, SourceName, UnavailableReason

LOCATION = Coordinates(latitude=-6.64, longitude=-79.79)
DAY_START = datetime(2026, 9, 4, 0, tzinfo=UTC)


def _payload(hours: int = 72) -> dict[str, object]:
    stamps = [(DAY_START + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(hours)]
    return {
        "hourly": {
            "time": stamps,
            "precipitation": [0.0] * hours,
            "precipitation_probability": [10] * hours,
        }
    }


def _fixture(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "open_meteo.json"
    path.write_text(text, encoding="utf-8")
    return path


class TestTheOfflineForecastProvider:
    def test_a_recorded_payload_is_parsed_by_the_real_parser(self, tmp_path: Path) -> None:
        path = _fixture(tmp_path, json.dumps(_payload()))

        result = OfflineOpenMeteoProvider(path).fetch_forecast(LOCATION, 48, DAY_START)

        assert isinstance(result, Available)
        assert result.data.horizon_hours == 48

    def test_a_missing_fixture_degrades_the_cycle_like_a_transport_failure(self, tmp_path: Path) -> None:
        result = OfflineOpenMeteoProvider(tmp_path / "absent.json").fetch_forecast(LOCATION, 48, DAY_START)

        assert isinstance(result, Unavailable)
        assert result.reason is UnavailableReason.TRANSPORT_ERROR

    def test_a_fixture_that_is_not_json_is_a_malformed_payload(self, tmp_path: Path) -> None:
        path = _fixture(tmp_path, "<html>not json</html>")

        result = OfflineOpenMeteoProvider(path).fetch_forecast(LOCATION, 48, DAY_START)

        assert isinstance(result, Unavailable)
        assert result.reason is UnavailableReason.MALFORMED_PAYLOAD

    def test_a_non_finite_reading_in_a_recorded_payload_does_not_abort_the_cycle(self, tmp_path: Path) -> None:
        """A recorded payload is a captured live response, so it can carry the
        same bare `NaN` the live endpoint can."""
        payload = _payload()
        payload["hourly"]["precipitation_probability"][3] = float("nan")  # type: ignore[index]
        path = _fixture(tmp_path, json.dumps(payload))

        result = OfflineOpenMeteoProvider(path).fetch_forecast(LOCATION, 48, DAY_START)

        assert isinstance(result, Unavailable)
        assert result.reason is UnavailableReason.MALFORMED_PAYLOAD

    def test_an_unexpected_parser_bug_is_reported_rather_than_raised(self, tmp_path: Path, monkeypatch) -> None:
        """Same rule as the live provider and the sibling scraper: no
        exception escapes a source adapter, because the use case has no
        handler and a whole cycle would be lost."""

        def boom(*args, **kwargs):
            raise KeyError("a field the parser assumed was there")

        monkeypatch.setattr("rain_alert.adapters.local.offline_sources.parse_forecast_payload", boom)
        path = _fixture(tmp_path, json.dumps(_payload()))

        result = OfflineOpenMeteoProvider(path).fetch_forecast(LOCATION, 48, DAY_START)

        assert isinstance(result, Unavailable)
        assert result.source is SourceName.OPEN_METEO
        assert "KeyError" in result.detail


class TestTheFileHtmlFetcher:
    def test_it_serves_the_pinned_file_and_ignores_the_url(self, tmp_path: Path) -> None:
        path = tmp_path / "senamhi.html"
        path.write_text("<table></table>", encoding="utf-8")

        assert FileHtmlFetcher(path).fetch("https://ignored.example") == "<table></table>"
