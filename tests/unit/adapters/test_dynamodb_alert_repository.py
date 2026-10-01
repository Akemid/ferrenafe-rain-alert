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

from rain_alert.adapters import dynamodb_alert_repository
from rain_alert.adapters.dynamodb_alert_repository import (
    ALERT_RETENTION_DAYS,
    CONNECT_TIMEOUT,
    READ_TIMEOUT,
    TOTAL_MAX_ATTEMPTS,
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
        """Recorded proof for task 1.6, rewritten in fix round 1 for clarity.

        Every current `Level` member (`none`, `prepare`, `imminent`) is
        plain ASCII lowercase, so an ASCII `~` (0x7E) bound already sorts
        above all three today — the parametrized test above does not
        actually go red if the sentinel is swapped for `~`. What breaks is
        the *general* guarantee design.md D26 states: the bound must hold
        "regardless of what a future `Level` value spells". This test
        constructs one hypothetical future value that sorts above `~`
        lexically (a non-ASCII label) and shows the tilde bound is already
        broken by it, while the real U+FFFF sentinel still holds.
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

    def test_mutation_proof_a_too_short_retention_breaks_the_relation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Recorded proof for task 1.8, corrected in fix round 1: the
        original version of this test computed the relation over local
        literals and never called `compute_expires_at` at all -- it would
        have passed even if the module were deleted. This one monkeypatches
        the real module constant and calls the real function, so the
        assertion is actually exercising production code: a retention
        constant at or below the dedup lookback (72h ~= 3 days) must fail
        the same relation the real constant (30 days) satisfies."""
        monkeypatch.setattr(dynamodb_alert_repository, "ALERT_RETENTION_DAYS", 2)
        sent_at = BASE

        expires_at = dynamodb_alert_repository.compute_expires_at(sent_at)

        assert not (expires_at - int(sent_at.timestamp()) > DEDUP_LOOKBACK_HOURS * 3600)

    def test_a_naive_sent_at_raises_rather_than_silently_shifting_the_ttl(self) -> None:
        """Fix round 1, MINOR item. `TimeWindow` enforces timezone-aware UTC
        on construction (`domain/values.py`), but `AlertRecord.sent_at` does
        not carry the same enforcement, and `datetime.timestamp()` on a
        naive value is interpreted as the *local* system time -- silently
        shifting the computed TTL by the host's UTC offset. Raising here
        matches the enforcement `TimeWindow` already has, rather than
        trusting every caller to have constructed `sent_at` correctly."""
        naive_sent_at = datetime(2026, 9, 4, 12)  # no tzinfo

        with pytest.raises(ValueError, match="timezone-aware"):
            compute_expires_at(naive_sent_at)

    def test_a_non_utc_sent_at_raises_rather_than_silently_shifting_the_ttl(self) -> None:
        from datetime import timedelta as _timedelta
        from datetime import timezone as _timezone

        non_utc_sent_at = BASE.astimezone(_timezone(_timedelta(hours=-5)))

        with pytest.raises(ValueError, match="UTC"):
            compute_expires_at(non_utc_sent_at)


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


class _RecordingQueryPaginator:
    """Wraps a real `query` paginator, recording every `paginate()` call's
    kwargs before delegating. `moto`'s backend is synchronous in-memory, so
    it cannot distinguish a consistent read from an eventually consistent
    one — the "a just-written record is visible" scenario would pass either
    way. This spy is what actually proves the request carried
    `ConsistentRead=True`, not merely that the query behaved as if it had."""

    def __init__(self, real_paginator: Any, calls: list[dict[str, Any]]) -> None:
        self._real_paginator = real_paginator
        self._calls = calls

    def paginate(self, **kwargs: Any) -> Any:
        self._calls.append(kwargs)
        return self._real_paginator.paginate(**kwargs)


class _ConsistentReadRecordingClient:
    """A hand-written spy wrapping a real (`moto`-backed) `dynamodb` client,
    recording the kwargs `get_paginator("query").paginate(...)` and
    `get_item(...)` were called with. Every other call is delegated straight
    through via `__getattr__`. Not `unittest.mock`: this is the same
    wrap-and-delegate pattern as `_CallCountingClient`
    (`test_ssm_config_repository.py`) and `tests/support/fakes.py`'s spies."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self.query_paginate_calls: list[dict[str, Any]] = []
        self.get_item_calls: list[dict[str, Any]] = []

    def get_paginator(self, operation_name: str) -> Any:
        real_paginator = self._client.get_paginator(operation_name)
        if operation_name == "query":
            return _RecordingQueryPaginator(real_paginator, self.query_paginate_calls)
        return real_paginator

    def get_item(self, **kwargs: Any) -> Any:
        self.get_item_calls.append(kwargs)
        return self._client.get_item(**kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


class TestConsistentReadIsActuallyRequested:
    """Fix round 1, MUST item 1. `moto`'s DynamoDB backend is synchronous
    in-memory, so it cannot distinguish `ConsistentRead=True` from the
    default eventually-consistent read — every prior test in this file would
    still pass with the kwarg deleted entirely. Only a kwarg-recording spy
    proves the request itself carries it."""

    def test_query_and_get_item_both_request_a_consistent_read(self, dynamodb_client: Any) -> None:
        spy = _ConsistentReadRecordingClient(dynamodb_client)
        repository = DynamoDbAlertRepository(TABLE_NAME, client=spy)
        repository.record_alert(_record())

        repository.alerts_with_window_start_between(CITY, BASE - timedelta(hours=1), BASE + timedelta(hours=1))
        repository.get_active_outage(CITY)

        assert len(spy.query_paginate_calls) == 1
        assert spy.query_paginate_calls[0]["ConsistentRead"] is True
        assert len(spy.get_item_calls) == 1
        assert spy.get_item_calls[0]["ConsistentRead"] is True


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


# --- outage-notice dedup state, over moto (not separately numbered in
# tasks.md's Phase 1 breakdown, but required by the AlertRepository port and
# design.md D26's outage-item row; added for completeness rather than left
# implemented-but-untested) ---


class TestOutageState:
    def test_get_active_outage_is_none_when_nothing_was_ever_saved(self, dynamodb_client: Any) -> None:
        repository = DynamoDbAlertRepository(TABLE_NAME, client=dynamodb_client)
        assert repository.get_active_outage(CITY) is None

    def test_a_saved_outage_survives_across_separate_repository_instances(self, dynamodb_client: Any) -> None:
        writer = DynamoDbAlertRepository(TABLE_NAME, client=dynamodb_client)
        writer.save_active_outage(_outage(notified=False))

        reader = DynamoDbAlertRepository(TABLE_NAME, client=dynamodb_client)
        restored = reader.get_active_outage(CITY)

        assert restored == _outage(notified=False)

    def test_save_active_outage_upserts_rather_than_duplicating(self, dynamodb_client: Any) -> None:
        repository = DynamoDbAlertRepository(TABLE_NAME, client=dynamodb_client)
        repository.save_active_outage(_outage(notified=False))
        repository.save_active_outage(_outage(notified=True))

        restored = repository.get_active_outage(CITY)

        assert restored is not None
        assert restored.notified_at is not None

    def test_clear_active_outage_removes_it(self, dynamodb_client: Any) -> None:
        repository = DynamoDbAlertRepository(TABLE_NAME, client=dynamodb_client)
        repository.save_active_outage(_outage())

        repository.clear_active_outage(CITY)

        assert repository.get_active_outage(CITY) is None

    def test_clearing_one_city_never_touches_another_citys_outage_item(self, dynamodb_client: Any) -> None:
        """The `pk` is city-scoped (`OUTAGE#<city_slug>`), so clearing one
        city's outage item must be a distinct `DeleteItem` against a distinct
        key -- not a query-and-clear-everything path that would silently
        clear an unrelated city, if this system ever monitored more than
        one."""
        repository = DynamoDbAlertRepository(TABLE_NAME, client=dynamodb_client)
        repository.save_active_outage(_outage())
        repository.save_active_outage(replace(_outage(), city_slug="other-city"))

        repository.clear_active_outage(CITY)

        assert repository.get_active_outage(CITY) is None
        assert repository.get_active_outage("other-city") is not None


# --- bounded client config (fix round 1, SHOULD item 4) ---


class TestTheRealClientIsBuiltWithAnExplicitBoundedConfig:
    """`_resolved_client` is the one method every other test bypasses by
    injecting `client=`, so nothing checked what it builds.

    A bare `boto3.client("dynamodb")` inherits botocore's defaults: 60 s
    connect, 60 s read, legacy retries with `max_attempts: 5` -- roughly five
    minutes of silent waiting against a blackholed endpoint. Worse here than
    in `s3_snapshot_publisher.py`, where this pattern was first written:
    `alerts_with_window_start_between` runs *before* the send decision, so a
    hung dedup read costs the whole cycle's Lambda budget and no alert goes
    out at all.
    """

    def test_the_real_client_carries_the_module_bound(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def fake_boto3_client(service_name: str, config: Any) -> object:
            captured["service_name"] = service_name
            captured["config"] = config
            return object()

        monkeypatch.setattr(dynamodb_alert_repository.boto3, "client", fake_boto3_client)

        DynamoDbAlertRepository(TABLE_NAME)._resolved_client()

        assert captured["service_name"] == "dynamodb"
        config = captured["config"]
        assert config.connect_timeout == CONNECT_TIMEOUT
        assert config.read_timeout == READ_TIMEOUT
        assert config.retries == {"total_max_attempts": TOTAL_MAX_ATTEMPTS}

    def test_the_bound_stays_small_enough_for_a_scheduled_lambda(self) -> None:
        """A single small `Query`/`GetItem`/`PutItem` is not a cold-starting
        model call: the worst case stays well inside a scheduled Lambda's
        budget, so a later edit that copies `agentcore_invoker.py`'s 35 s
        read timeout wholesale fails here instead of holding the cycle open
        on a dedup read that runs before any alert can be sent."""
        assert TOTAL_MAX_ATTEMPTS * (CONNECT_TIMEOUT + READ_TIMEOUT) <= 20.0
