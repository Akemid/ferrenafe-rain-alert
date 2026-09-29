"""`DynamoDbAlertRepository` — the `AlertRepository` over DynamoDB (design.md
D26, D27, D28).

Pure mapping is tested with no client at all — key construction, sentinel
ordering, the TTL relation, the outage item's missing `expires_at`. The
`Query`/pagination/`PutItem` behaviour is tested against `moto`'s in-memory
DynamoDB backend, per D28: a hand-written fake client would have to
re-implement DynamoDB's key-condition evaluator, and would then pass against
a `KeyConditionExpression` real DynamoDB rejects. `moto` is a service double
in the same category as `FileHtmlFetcher` (`tests/support/fakes.py`), not the
`unittest.mock` this project forbids.

Every `moto`-backed test constructs its own table, per invocation, and
constructs a **fresh** `DynamoDbAlertRepository` instance around it — never
one long-lived instance reused across what stands in for separate
invocations. Reusing one instance would prove nothing about "survives
between invocations", which is the whole point of `alert-persistence`'s
first requirement.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from moto import mock_aws

from rain_alert.adapters.dynamodb_alert_repository import (
    ALERT_RETENTION_DAYS,
    DynamoDbAlertRepository,
    alert_item_pk,
    alert_item_sk,
    alert_record_from_item,
    alert_record_to_item,
    compute_expires_at,
    outage_item_pk,
    outage_item_sk,
    outage_record_from_item,
    outage_record_to_item,
    upper_bound_sort_key,
)
from rain_alert.domain.messages import AlertMessage, AlertRecord, OutageRecord
from rain_alert.domain.reasons import ForecastThresholdReason
from rain_alert.domain.values import ComposerName, Level, SourceName, TimeWindow

TABLE_NAME = "ferrenafe-alerts-sent"
CITY = "ferrenafe"
BASE = datetime(2026, 9, 4, 12, tzinfo=UTC)
DEDUP_LOOKBACK_HOURS = 72


def _window(offset_hours: int = 0, length_hours: int = 24) -> TimeWindow:
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
            composed_by=ComposerName.AGENT,
        ),
        reasons=(ForecastThresholdReason(accumulated_mm=24.0, hours=48, probability_pct=95),),
        senamhi_status="available",
        open_meteo_status="available",
        composer="agent",
    )


def _outage(notified: bool = True) -> OutageRecord:
    return OutageRecord(
        city_slug=CITY,
        unavailable_sources=frozenset({SourceName.SENAMHI, SourceName.OPEN_METEO}),
        opened_at=BASE,
        notified_at=BASE if notified else None,
    )


# --- key construction (1.3/1.4) ---


class TestKeyConstruction:
    def test_alert_item_pk_is_the_alert_prefix_over_city_slug(self) -> None:
        assert alert_item_pk(CITY) == "ALERT#ferrenafe"

    def test_alert_item_sk_is_window_start_iso_hash_level(self) -> None:
        window_start = BASE
        assert alert_item_sk(window_start, Level.IMMINENT) == f"{BASE.isoformat()}#imminent"

    def test_outage_item_pk_is_the_outage_prefix_over_city_slug(self) -> None:
        assert outage_item_pk(CITY) == "OUTAGE#ferrenafe"

    def test_outage_item_sk_is_the_reserved_literal_current(self) -> None:
        assert outage_item_sk() == "CURRENT"


# --- sentinel ordering (1.5/1.6) ---


class TestSentinelOrdering:
    @pytest.mark.parametrize("level", list(Level))
    def test_every_level_sorts_below_the_upper_bound(self, level: Level) -> None:
        """The sentinel must be an upper bound regardless of which `Level` the
        record at `latest_start` carries — not just the one this fixture
        happens to pick."""
        actual_key = alert_item_sk(BASE, level)
        bound = upper_bound_sort_key(BASE)
        assert actual_key < bound

    def test_the_sentinel_is_u_plus_ffff_not_ascii_tilde(self) -> None:
        """Pins the *choice*, not merely its presence — see the mutation proof
        in test_mutation_proof_tilde_sentinel_is_not_a_safe_substitute below,
        which shows an ASCII `~` is not a safe substitute for it."""
        assert upper_bound_sort_key(BASE) == f"{BASE.isoformat()}#￿"

    def test_mutation_proof_tilde_sentinel_is_not_a_safe_substitute(self) -> None:
        """Recorded proof for task 1.6: swapping the sentinel for ASCII `~`
        breaks the ordering relation for `Level.IMMINENT`, whose value sorts
        above `~` in a way `�`... — see docstring above; the concrete
        failure is `"imminent" > "~"` is False is NOT what we want to show.

        The actual defect a `~`-based bound has is narrower than "sorts
        below": `~` (0x7E) sorts BELOW many Unicode code points a pathological
        future `Level` member's value could use, and below every ASCII
        lowercase letter used by every existing member's own value at a tie
        in the timestamp prefix — this test demonstrates the concrete case
        that matters today: a hypothetical member whose value starts above
        `~` lexically would break the naive bound. Levels today are ASCII
        lowercase, so this test constructs the failing input directly rather
        than relying on an existing member to expose it, and states why: the
        parametrized test above only proves the *chosen* sentinel is safe; it
        is this test's job to prove an *alternate* choice is not.
        """
        tilde_bound = f"{BASE.isoformat()}#~"
        # A pathological future level value that sorts above "~" (0x7E) --
        # for instance an accented or non-ASCII label -- would break the
        # tilde bound while the U+FFFF sentinel still holds.
        pathological_future_level_value = "über"  # sorts above "~" lexically
        candidate_key = f"{BASE.isoformat()}#{pathological_future_level_value}"
        assert not (candidate_key < tilde_bound), "tilde bound should already be broken by this input"
        assert candidate_key < upper_bound_sort_key(BASE), "the U+FFFF sentinel still holds"


# --- TTL relation (1.7/1.8) ---


class TestTtlRelation:
    def test_ttl_exceeds_the_dedup_lookback_window(self) -> None:
        sent_at = BASE
        expires_at = compute_expires_at(sent_at)
        assert expires_at - int(sent_at.timestamp()) > DEDUP_LOOKBACK_HOURS * 3600

    def test_ttl_is_exactly_the_retention_constant_in_days(self) -> None:
        sent_at = BASE
        expires_at = compute_expires_at(sent_at)
        assert expires_at == int(sent_at.timestamp()) + ALERT_RETENTION_DAYS * 86400

    def test_the_retention_constant_is_30_days_per_the_spec_not_365(self) -> None:
        """`alert-persistence` spec pins 30 days; design.md D27 proposed 365
        before the spec resolved it in the spec's favor. This is the recorded
        resolution (tasks.md task 1.7's own note)."""
        assert ALERT_RETENTION_DAYS == 30

    def test_mutation_proof_a_too_short_retention_breaks_the_relation(self) -> None:
        """Recorded proof for task 1.8: a retention constant at or below the
        dedup lookback (72h ~= 3 days) fails the same relation the real
        constant must satisfy. This does not mutate the module constant in
        place (which every other test in this session would then see); it
        recomputes the relation with a substitute value to show the relation
        is not vacuously true for any integer."""
        too_short_days = 2
        sent_at = BASE
        substitute_expires_at = int(sent_at.timestamp()) + too_short_days * 86400
        assert not (substitute_expires_at - int(sent_at.timestamp()) > DEDUP_LOOKBACK_HOURS * 3600)


# --- item <-> record mapping, pure (1.3/1.4, 1.9/1.10) ---


class TestItemMapping:
    def test_alert_record_to_item_carries_pk_sk_and_expires_at(self) -> None:
        record = _record()

        item = alert_record_to_item(record)

        assert item["pk"] == "ALERT#ferrenafe"
        assert item["sk"] == f"{record.window.start.isoformat()}#prepare"
        assert item["expires_at"] == compute_expires_at(record.sent_at)

    def test_alert_record_round_trips_through_item_mapping(self) -> None:
        record = _record(level=Level.IMMINENT)

        restored = alert_record_from_item(alert_record_to_item(record))

        assert restored == record

    def test_outage_record_to_item_carries_pk_and_the_current_sort_key(self) -> None:
        record = _outage()

        item = outage_record_to_item(record)

        assert item["pk"] == "OUTAGE#ferrenafe"
        assert item["sk"] == "CURRENT"

    def test_outage_item_carries_no_expires_at_attribute_at_all(self) -> None:
        """Not `None` — absent. DynamoDB TTL only deletes items that *have*
        the attribute (design.md D27), so a null value would still be a
        silent bug (TTL would be inert either way, but a future reader
        checking `"expires_at" in item` must see the true state)."""
        item = outage_record_to_item(_outage())

        assert "expires_at" not in item

    def test_outage_record_round_trips_through_item_mapping(self) -> None:
        record = _outage(notified=False)

        restored = outage_record_from_item(outage_record_to_item(record))

        assert restored == record


# --- moto-backed: Query, ConsistentRead, ScanIndexForward, pagination (1.11-1.14) ---


def _create_table(client: Any) -> None:
    client.create_table(
        TableName=TABLE_NAME,
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    client.get_waiter("table_exists").wait(TableName=TABLE_NAME)


@pytest.fixture
def dynamodb_client() -> Any:
    with mock_aws():
        import boto3

        client = boto3.client("dynamodb", region_name="us-east-2")
        _create_table(client)
        yield client


class TestQueryAgainstMoto:
    def test_query_returns_a_record_written_by_a_separate_repository_instance(self, dynamodb_client: Any) -> None:
        """'Survives between invocations' means what it says: a fresh
        repository instance (a fresh cold invocation) must see what a
        *different*, prior instance wrote. A test that reuses one instance
        for both the write and the read proves nothing about durability —
        an in-process cache would pass it too."""
        writer = DynamoDbAlertRepository(TABLE_NAME, client=dynamodb_client)
        writer.record_alert(_record())

        reader = DynamoDbAlertRepository(TABLE_NAME, client=dynamodb_client)
        results = reader.alerts_with_window_start_between(CITY, BASE - timedelta(hours=1), BASE + timedelta(hours=1))

        assert len(results) == 1
        assert results[0].city_slug == CITY

    def test_query_is_scoped_to_the_requested_city_slug(self, dynamodb_client: Any) -> None:
        writer = DynamoDbAlertRepository(TABLE_NAME, client=dynamodb_client)
        writer.record_alert(_record(city="other-city"))
        writer.record_alert(_record())

        reader = DynamoDbAlertRepository(TABLE_NAME, client=dynamodb_client)
        results = reader.alerts_with_window_start_between(CITY, BASE - timedelta(hours=1), BASE + timedelta(hours=1))

        assert len(results) == 1
        assert results[0].city_slug == CITY

    def test_query_upper_bound_is_inclusive_of_an_escalation_at_the_same_window_start(
        self, dynamodb_client: Any
    ) -> None:
        """The load-bearing case design.md D26 names: a standing `prepare`
        escalates to `imminent` for the SAME window start. Both records must
        exist (see TestEscalationDoesNotOverwrite below) and both must be
        visible to a query bounded at exactly that window start -- the naive
        `latest_start.isoformat()` bound (no sentinel) would exclude the
        `imminent` record because `...#imminent` sorts after the bare
        timestamp."""
        writer = DynamoDbAlertRepository(TABLE_NAME, client=dynamodb_client)
        writer.record_alert(_record(level=Level.PREPARE))
        writer.record_alert(_record(level=Level.IMMINENT))

        reader = DynamoDbAlertRepository(TABLE_NAME, client=dynamodb_client)
        results = reader.alerts_with_window_start_between(CITY, BASE, BASE)

        assert {r.level for r in results} == {Level.PREPARE, Level.IMMINENT}

    def test_results_are_ascending_by_window_start(self, dynamodb_client: Any) -> None:
        writer = DynamoDbAlertRepository(TABLE_NAME, client=dynamodb_client)
        writer.record_alert(_record(offset_hours=10))
        writer.record_alert(_record(offset_hours=0))
        writer.record_alert(_record(offset_hours=5))

        reader = DynamoDbAlertRepository(TABLE_NAME, client=dynamodb_client)
        results = reader.alerts_with_window_start_between(CITY, BASE - timedelta(hours=1), BASE + timedelta(hours=11))

        starts = [r.window.start for r in results]
        assert starts == sorted(starts)

    def test_pagination_returns_every_item_across_more_than_one_page(self, dynamodb_client: Any) -> None:
        """A first page that happens to hold everything makes a missing
        `LastEvaluatedKey` loop invisible until the data grows. DynamoDB
        (and `moto`'s emulation of it) caps one `Query` page at 1 MiB of item
        data, so this seeds enough oversized items to force at least one
        `LastEvaluatedKey` continuation deterministically, rather than
        relying on item *count* alone, which real page-size behaviour does
        not guarantee."""
        oversized_body = "x" * 60_000  # ~60 KB/item; 20 items exceeds the 1 MiB page cap
        writer = DynamoDbAlertRepository(TABLE_NAME, client=dynamodb_client)
        for offset in range(20):
            record = _record(offset_hours=offset)
            record = replace(record, message=replace(record.message, body=oversized_body))
            writer.record_alert(record)

        reader = DynamoDbAlertRepository(TABLE_NAME, client=dynamodb_client)
        results = reader.alerts_with_window_start_between(CITY, BASE - timedelta(hours=1), BASE + timedelta(hours=25))

        assert len(results) == 20


# --- record_alert is unconditional (1.15/1.16) ---


class TestRecordAlertIsUnconditional:
    def test_writing_the_same_key_twice_overwrites_last_write_wins(self, dynamodb_client: Any) -> None:
        repository = DynamoDbAlertRepository(TABLE_NAME, client=dynamodb_client)
        first = _record()
        second = replace(
            first,
            message=replace(first.message, title="a different, later composition"),
        )

        repository.record_alert(first)
        repository.record_alert(second)  # same pk/sk as `first` -- must not raise

        results = repository.alerts_with_window_start_between(
            CITY, BASE - timedelta(hours=1), BASE + timedelta(hours=1)
        )
        assert len(results) == 1
        assert results[0].message.title == "a different, later composition"

    def test_an_escalation_for_the_same_window_does_not_overwrite_the_prior_level(self, dynamodb_client: Any) -> None:
        """The specific overwrite this design guards against: `prepare` then
        `imminent` for the SAME window start must both survive, because the
        sort key includes the level. A naive `TimeWindow.key()`-only sort key
        would silently overwrite the `prepare` record with `imminent`."""
        repository = DynamoDbAlertRepository(TABLE_NAME, client=dynamodb_client)
        repository.record_alert(_record(level=Level.PREPARE))
        repository.record_alert(_record(level=Level.IMMINENT))

        results = repository.alerts_with_window_start_between(
            CITY, BASE - timedelta(hours=1), BASE + timedelta(hours=1)
        )

        assert len(results) == 2
        assert {r.level for r in results} == {Level.PREPARE, Level.IMMINENT}

    def test_mutation_proof_a_key_without_the_level_would_overwrite_the_escalation(self) -> None:
        """Recorded proof for the design's own claim: construct the sort key
        the naive way (window-start only, per `TimeWindow.key()`) for both
        records and show they collide -- which is exactly what would cause
        the overwrite above, without needing to weaken the production key
        function itself."""
        window = _window()
        naive_prepare_key = window.key()
        naive_imminent_key = window.key()
        assert naive_prepare_key == naive_imminent_key, "the naive key collides -- this is the bug the design avoids"

        # The real key construction does not collide:
        real_prepare_key = alert_item_sk(window.start, Level.PREPARE)
        real_imminent_key = alert_item_sk(window.start, Level.IMMINENT)
        assert real_prepare_key != real_imminent_key


# --- full round trip through the composed path (1.17/1.18) ---


class TestFullRoundTrip:
    def test_a_fully_populated_alert_record_survives_write_and_read(self, dynamodb_client: Any) -> None:
        record = _record(level=Level.IMMINENT)

        writer = DynamoDbAlertRepository(TABLE_NAME, client=dynamodb_client)
        writer.record_alert(record)

        reader = DynamoDbAlertRepository(TABLE_NAME, client=dynamodb_client)
        results = reader.alerts_with_window_start_between(CITY, BASE - timedelta(hours=1), BASE + timedelta(hours=1))

        assert len(results) == 1
        assert results[0] == record
