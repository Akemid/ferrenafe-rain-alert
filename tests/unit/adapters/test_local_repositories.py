"""The local repositories (design.md 8.3, 4.1; weather-sources spec
"Placeholder coordinates documented"; repo-hygiene spec "No secrets or
personal data").
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from rain_alert.adapters.local.in_memory_alert_repository import InMemoryAlertRepository
from rain_alert.adapters.local.json_alert_repository import JsonFileAlertRepository
from rain_alert.adapters.local.static_config_repository import (
    LAT_ENV_VAR,
    LON_ENV_VAR,
    PLACEHOLDER_COORDINATES,
    StaticConfigRepository,
)
from rain_alert.adapters.local.static_contact_repository import StaticContactRepository
from rain_alert.domain.messages import AlertMessage, AlertRecord, OutageRecord
from rain_alert.domain.reasons import ForecastThresholdReason
from rain_alert.domain.values import Level, SourceName, TimeWindow, WarningLevel
from tests.support.fakes import FakeAlertRepository

CITY = "ferrenafe"
BASE = datetime(2026, 9, 4, 12, tzinfo=UTC)


def _window(offset_hours: int, length_hours: int = 24) -> TimeWindow:
    start = BASE + timedelta(hours=offset_hours)
    return TimeWindow(start=start, end=start + timedelta(hours=length_hours))


def _record(offset_hours: int = 0, *, level: Level = Level.PREPARE, city: str = CITY) -> AlertRecord:
    window = _window(offset_hours)
    return AlertRecord(
        city_slug=city,
        level=level,
        window=window,
        sent_at=window.start,
        message=AlertMessage(
            title=f"Alerta de lluvias — {city} — {level.value}",
            body="Ciudad: Ferreñafe\nNivel: prepárate",
            level=level,
            valid_until=window.end,
        ),
        reasons=(ForecastThresholdReason(accumulated_mm=24.0, hours=48, probability_pct=95),),
        senamhi_status="available",
        open_meteo_status="available",
        composer="template",
    )


def _outage(notified: bool = True) -> OutageRecord:
    return OutageRecord(
        city_slug=CITY,
        unavailable_sources=frozenset({SourceName.SENAMHI}),
        opened_at=BASE,
        notified_at=BASE if notified else None,
    )


REPOSITORY_FACTORIES = ["in_memory", "json_file", "recording_fake"]


@pytest.fixture(params=REPOSITORY_FACTORIES)
def repository(request, tmp_path: Path):
    """Every `AlertRepository` implementation in the repository, so the port
    contract is asserted once against all of them and they cannot drift apart.

    `FakeAlertRepository` is included deliberately. It is test-only, but the
    use-case tests trust it to answer dedup queries the way production will —
    so a fake that answers them differently makes those tests assert a
    behaviour the system does not have.
    """
    if request.param == "in_memory":
        return InMemoryAlertRepository()
    if request.param == "recording_fake":
        return FakeAlertRepository()
    return JsonFileAlertRepository(tmp_path / "state" / "alerts.json")


class TestTheAlertRepositoryContract:
    """Run against both implementations."""

    def test_a_recorded_alert_is_found_by_a_window_start_range(self, repository) -> None:
        record = _record()
        repository.record_alert(record)

        found = repository.alerts_with_window_start_between(
            CITY, BASE - timedelta(hours=72), BASE + timedelta(hours=72)
        )

        assert found == (record,)

    def test_an_alert_outside_the_range_is_not_returned(self, repository) -> None:
        """Triangulation: a repository that ignored the range and returned
        everything would pass the test above."""
        repository.record_alert(_record(offset_hours=200))

        found = repository.alerts_with_window_start_between(CITY, BASE - timedelta(hours=72), BASE)

        assert found == ()

    def test_another_citys_alerts_are_not_returned(self, repository) -> None:
        mine = _record()
        repository.record_alert(mine)
        repository.record_alert(_record(city="chiclayo"))

        found = repository.alerts_with_window_start_between(
            CITY, BASE - timedelta(hours=72), BASE + timedelta(hours=72)
        )

        assert found == (mine,)

    def test_results_come_back_ascending_by_window_start(self, repository) -> None:
        """The port documents ascending order, and `should_send` reads the
        highest prior level out of the result."""
        later = _record(offset_hours=10)
        earlier = _record(offset_hours=1)
        repository.record_alert(later)
        repository.record_alert(earlier)

        found = repository.alerts_with_window_start_between(
            CITY, BASE - timedelta(hours=72), BASE + timedelta(hours=72)
        )

        assert [record.window.start for record in found] == [earlier.window.start, later.window.start]

    def test_the_range_bounds_are_inclusive(self, repository) -> None:
        record = _record()
        repository.record_alert(record)

        assert repository.alerts_with_window_start_between(CITY, record.window.start, record.window.start) == (record,)

    def test_no_active_outage_is_reported_as_none(self, repository) -> None:
        assert repository.get_active_outage(CITY) is None

    def test_a_saved_outage_is_read_back(self, repository) -> None:
        outage = _outage()
        repository.save_active_outage(outage)

        assert repository.get_active_outage(CITY) == outage

    def test_saving_again_upserts_rather_than_accumulating(self, repository) -> None:
        """At most one active outage per city (design.md 4.1)."""
        repository.save_active_outage(_outage(notified=False))
        repository.save_active_outage(_outage(notified=True))

        active = repository.get_active_outage(CITY)
        assert active is not None
        assert active.notified_at == BASE

    def test_clearing_removes_the_active_outage(self, repository) -> None:
        repository.save_active_outage(_outage())
        repository.clear_active_outage(CITY)

        assert repository.get_active_outage(CITY) is None

    def test_clearing_when_nothing_is_active_is_not_an_error(self, repository) -> None:
        repository.clear_active_outage(CITY)

        assert repository.get_active_outage(CITY) is None

    def test_another_citys_outage_is_not_returned(self, repository) -> None:
        repository.save_active_outage(_outage())

        assert repository.get_active_outage("chiclayo") is None

    def test_clearing_another_citys_outage_leaves_mine_active(self, repository) -> None:
        """`clear_active_outage` takes a `city_slug`, so it must honour it.

        A repository that clears unconditionally loses the record that says
        the operator was already told about this city's outage, and the next
        cycle re-notifies. Change 3's `DeleteItem(pk="OUTAGE#<slug>")` is
        keyed by city, so this is the production behaviour.
        """
        repository.save_active_outage(_outage())

        repository.clear_active_outage("chiclayo")

        assert repository.get_active_outage(CITY) == _outage()


class TestJsonFilePersistence:
    """What the file adapter adds over the in-memory one: durability."""

    def test_state_survives_a_new_repository_instance(self, tmp_path: Path) -> None:
        """Each CLI run is a fresh process, so this is the only reason the file
        adapter exists — and it is what makes three dual-outage cycles emit one
        notice instead of three."""
        path = tmp_path / "alerts.json"
        record = _record()
        outage = _outage(notified=False)

        first = JsonFileAlertRepository(path)
        first.record_alert(record)
        first.save_active_outage(outage)

        reopened = JsonFileAlertRepository(path)

        assert reopened.get_active_outage(CITY) == outage
        assert reopened.alerts_with_window_start_between(
            CITY, BASE - timedelta(hours=1), BASE + timedelta(hours=1)
        ) == (record,)

    def test_a_missing_file_reads_as_empty_state_rather_than_failing(self, tmp_path: Path) -> None:
        repository = JsonFileAlertRepository(tmp_path / "nothing-here.json")

        assert repository.get_active_outage(CITY) is None
        assert repository.alerts_with_window_start_between(CITY, BASE, BASE) == ()

    def test_the_parent_directory_is_created_on_first_write(self, tmp_path: Path) -> None:
        path = tmp_path / "deep" / "nested" / "alerts.json"

        JsonFileAlertRepository(path).record_alert(_record())

        assert path.exists()

    def test_the_document_holds_the_two_collections_from_the_design(self, tmp_path: Path) -> None:
        """design.md 4.1: the file adapter and the DynamoDB adapter are
        shape-identical, so change 3 is a mechanical mapping."""
        path = tmp_path / "alerts.json"
        repository = JsonFileAlertRepository(path)
        repository.record_alert(_record())
        repository.save_active_outage(_outage())

        document = json.loads(path.read_text(encoding="utf-8"))

        assert set(document) == {"alerts", "active_outage"}
        assert len(document["alerts"]) == 1
        assert document["active_outage"]["city_slug"] == CITY

    def test_a_cleared_outage_is_written_as_null_not_left_stale(self, tmp_path: Path) -> None:
        path = tmp_path / "alerts.json"
        repository = JsonFileAlertRepository(path)
        repository.save_active_outage(_outage())
        repository.clear_active_outage(CITY)

        assert json.loads(path.read_text(encoding="utf-8"))["active_outage"] is None

    def test_no_temporary_file_is_left_behind(self, tmp_path: Path) -> None:
        """The write is atomic via a temp file plus `os.replace`; a leftover
        temp file would mean the replace never happened."""
        path = tmp_path / "alerts.json"
        JsonFileAlertRepository(path).record_alert(_record())

        assert [entry.name for entry in tmp_path.iterdir()] == ["alerts.json"]

    def test_an_existing_file_is_not_corrupted_when_a_record_cannot_be_written(self, tmp_path: Path) -> None:
        """The point of the atomic write: a failed serialization must leave the
        previous state readable rather than a truncated document."""
        path = tmp_path / "alerts.json"
        repository = JsonFileAlertRepository(path)
        good = _record()
        repository.record_alert(good)

        unwritable = replace(_record(offset_hours=1), level="not-a-level")  # type: ignore[arg-type]

        with pytest.raises((TypeError, ValueError)):
            repository.record_alert(unwritable)

        reopened = JsonFileAlertRepository(path)
        assert reopened.alerts_with_window_start_between(CITY, BASE, BASE) == (good,)

    def test_a_failed_write_does_not_leave_the_record_in_memory_either(self, tmp_path: Path) -> None:
        """The in-memory state must not claim an alert was recorded that was
        never written — the next read of that state would say the community
        had already been told."""
        path = tmp_path / "alerts.json"
        repository = JsonFileAlertRepository(path)
        good = _record()
        repository.record_alert(good)

        with pytest.raises((TypeError, ValueError)):
            repository.record_alert(replace(_record(offset_hours=1), level="not-a-level"))  # type: ignore[arg-type]

        assert repository.alerts_with_window_start_between(CITY, BASE, BASE + timedelta(hours=2)) == (good,)

    def test_a_corrupt_state_file_fails_loudly(self, tmp_path: Path) -> None:
        """Silently starting from empty state would re-alert the community for
        a window that was already sent."""
        path = tmp_path / "alerts.json"
        path.write_text("{ not json", encoding="utf-8")

        with pytest.raises(ValueError):
            JsonFileAlertRepository(path).get_active_outage(CITY)

    def test_the_reasons_survive_the_file_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "alerts.json"
        record = _record()
        JsonFileAlertRepository(path).record_alert(record)

        restored = JsonFileAlertRepository(path).alerts_with_window_start_between(CITY, BASE, BASE)

        assert restored[0].reasons == record.reasons


class TestStaticConfigRepository:
    def test_the_coordinates_are_flagged_as_a_placeholder(self) -> None:
        """weather-sources: Placeholder coordinates documented. The exact
        Ferrenafe coordinates are an open decision, so the config must say so
        rather than presenting a guess as verified."""
        config = StaticConfigRepository(env={}).load()

        assert config.coordinates == PLACEHOLDER_COORDINATES
        assert config.coordinates_are_placeholder is True

    def test_the_configured_city_and_region_drive_both_sources(self) -> None:
        config = StaticConfigRepository(env={}).load()

        assert (config.city, config.city_slug, config.region) == ("Ferreñafe", "ferrenafe", "Lambayeque")
        assert config.timezone == "America/Lima"

    def test_the_thresholds_are_the_calibrated_values(self) -> None:
        config = StaticConfigRepository(env={}).load()

        thresholds = config.thresholds
        assert (thresholds.prepare_mm_48h, thresholds.prepare_probability_pct) == (9.5, 60)
        assert thresholds.prepare_probability_pct_degraded == 70
        assert (thresholds.imminent_mm_24h, thresholds.imminent_probability_pct) == (20.0, 70)
        assert thresholds.imminent_warning_levels == frozenset({WarningLevel.ORANGE, WarningLevel.RED})
        assert thresholds.prepare_warning_levels == frozenset(
            {WarningLevel.YELLOW, WarningLevel.ORANGE, WarningLevel.RED}
        )

    def test_the_horizons_match_the_evaluators_expectations(self) -> None:
        config = StaticConfigRepository(env={}).load()

        assert (config.forecast_hours, config.dedup_lookback_hours) == (48, 72)

    def test_the_checklist_is_recipient_facing_spanish(self) -> None:
        """The checklist is rendered into the community message body, so it is
        the one place in this codebase where copy is Spanish by contract."""
        config = StaticConfigRepository(env={}).load()

        assert config.checklist
        assert any("agua" in item.lower() for item in config.checklist)

    def test_explicit_coordinates_from_the_environment_are_used(self) -> None:
        config = StaticConfigRepository(env={LAT_ENV_VAR: "-6.6377", LON_ENV_VAR: "-79.7889"}).load()

        assert (config.coordinates.latitude, config.coordinates.longitude) == (-6.6377, -79.7889)

    def test_explicit_coordinates_clear_the_placeholder_flag(self) -> None:
        """An operator who supplies coordinates is asserting them, so the CLI
        must stop printing the placeholder marker."""
        config = StaticConfigRepository(env={LAT_ENV_VAR: "-6.6377", LON_ENV_VAR: "-79.7889"}).load()

        assert config.coordinates_are_placeholder is False

    def test_only_one_of_the_two_overrides_is_ignored_and_stays_a_placeholder(self) -> None:
        """Half an override is not a coordinate; silently pairing a real
        latitude with a placeholder longitude would forecast for the wrong
        place while claiming to be configured."""
        config = StaticConfigRepository(env={LAT_ENV_VAR: "-6.6377"}).load()

        assert config.coordinates == PLACEHOLDER_COORDINATES
        assert config.coordinates_are_placeholder is True

    def test_an_unparseable_override_fails_loudly(self) -> None:
        """Falling back to the placeholder would hide an operator typo and
        forecast for the wrong city without saying so."""
        with pytest.raises(ValueError, match=LAT_ENV_VAR):
            StaticConfigRepository(env={LAT_ENV_VAR: "north-ish", LON_ENV_VAR: "-79.7889"}).load()

    def test_an_out_of_range_override_fails_loudly(self) -> None:
        with pytest.raises(ValueError):
            StaticConfigRepository(env={LAT_ENV_VAR: "-600", LON_ENV_VAR: "-79.7889"}).load()


class TestStaticContactRepository:
    def test_the_active_channel_returns_the_local_operator_contact(self) -> None:
        contacts = StaticContactRepository().list_active("console")

        assert len(contacts) == 1
        assert contacts[0].channel == "console"

    def test_another_channel_returns_nothing(self) -> None:
        """Triangulation: a repository that ignored the channel would pass the
        test above."""
        assert StaticContactRepository().list_active("whatsapp") == ()

    def test_the_contact_holds_no_personal_data(self) -> None:
        """repo-hygiene: no phone numbers, emails or handles that a grep scan
        would have to be taught to forgive."""
        contact = StaticContactRepository().list_active("console")[0]

        rendered = f"{contact.contact_id} {contact.channel} {contact.handle}"
        assert "@" not in rendered
        assert not any(character.isdigit() for character in rendered)
        assert contact.consent_at is None
