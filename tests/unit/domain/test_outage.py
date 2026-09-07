"""evaluate_outage — the eight-row state-transition table (design.md section
6; source-outage-notices spec). Pure: no AlertRepository import — the
active/next state is passed in and returned as data.
"""

from datetime import UTC, datetime

from rain_alert.domain.messages import OutageRecord
from rain_alert.domain.outage import OutageDecision, evaluate_outage
from rain_alert.domain.sources import Available, Unavailable
from rain_alert.domain.values import Level, NoticeKind, SourceName, UnavailableReason

NOW = datetime(2026, 9, 3, 12, tzinfo=UTC)
CITY_SLUG = "ferrenafe"


def _available() -> Available[None]:
    return Available(data=None, fetched_at=NOW)


def _unavailable(source: SourceName) -> Unavailable:
    return Unavailable(source=source, reason=UnavailableReason.TIMEOUT, detail="timed out", observed_at=NOW)


def _active(sources: frozenset[SourceName], *, notified: bool = True) -> OutageRecord:
    return OutageRecord(
        city_slug=CITY_SLUG,
        unavailable_sources=sources,
        opened_at=NOW,
        notified_at=NOW if notified else None,
    )


def test_no_outage_and_no_active_record_yields_nothing() -> None:
    """Row 1: ∅ unavailable, no active record."""
    decision = evaluate_outage(_available(), _available(), None, CITY_SLUG, NOW)

    assert decision == OutageDecision(notices=(), next_state=None, state_changed=False, suppress_community_alert=False)


def test_recovery_after_dual_outage_emits_exactly_one_recovery_notice() -> None:
    """Row 2: ∅ unavailable, active record present — full recovery."""
    active = _active(frozenset({SourceName.SENAMHI, SourceName.OPEN_METEO}))

    decision = evaluate_outage(_available(), _available(), active, CITY_SLUG, NOW)

    assert len(decision.notices) == 1
    assert decision.notices[0].kind == NoticeKind.SOURCES_RECOVERED
    assert decision.notices[0].sources == frozenset({SourceName.SENAMHI, SourceName.OPEN_METEO})
    assert decision.next_state is None
    assert decision.state_changed is True
    assert decision.suppress_community_alert is False


def test_senamhi_down_with_no_active_record_triggers_one_notice() -> None:
    """Row 3: {A} unavailable, no active record."""
    decision = evaluate_outage(_unavailable(SourceName.SENAMHI), _available(), None, CITY_SLUG, NOW)

    assert len(decision.notices) == 1
    assert decision.notices[0].kind == NoticeKind.SOURCE_UNAVAILABLE
    assert decision.notices[0].sources == frozenset({SourceName.SENAMHI})
    assert decision.next_state == OutageRecord(
        city_slug=CITY_SLUG, unavailable_sources=frozenset({SourceName.SENAMHI}), opened_at=NOW, notified_at=NOW
    )
    assert decision.state_changed is True
    assert decision.suppress_community_alert is False


def test_no_duplicate_notice_while_still_down_with_the_same_single_source() -> None:
    """Row 4: {A} unavailable, active record already has {A} and was notified."""
    active = _active(frozenset({SourceName.SENAMHI}))

    decision = evaluate_outage(_unavailable(SourceName.SENAMHI), _available(), active, CITY_SLUG, NOW)

    assert decision.notices == ()
    assert decision.next_state == active
    assert decision.state_changed is False


def test_an_unchanged_outage_that_was_never_notified_is_notified_now() -> None:
    """Row 4 variant: dedup keyed on set equality alone lost the notice for
    any record persisted with `notified_at=None`, forever. `notified_at` had
    no reader at all, so the field could only ever be written."""
    active = _active(frozenset({SourceName.SENAMHI}), notified=False)

    decision = evaluate_outage(_unavailable(SourceName.SENAMHI), _available(), active, CITY_SLUG, NOW)

    assert len(decision.notices) == 1
    assert decision.notices[0].kind == NoticeKind.SOURCE_UNAVAILABLE
    assert decision.notices[0].sources == frozenset({SourceName.SENAMHI})
    assert decision.next_state == OutageRecord(
        city_slug=CITY_SLUG, unavailable_sources=frozenset({SourceName.SENAMHI}), opened_at=NOW, notified_at=NOW
    )
    assert decision.state_changed is True


def test_an_unchanged_dual_outage_that_was_never_notified_is_notified_now() -> None:
    """Row 6 variant, same defect."""
    active = _active(frozenset({SourceName.SENAMHI, SourceName.OPEN_METEO}), notified=False)

    decision = evaluate_outage(
        _unavailable(SourceName.SENAMHI), _unavailable(SourceName.OPEN_METEO), active, CITY_SLUG, NOW
    )

    assert len(decision.notices) == 1
    assert decision.notices[0].sources == frozenset({SourceName.SENAMHI, SourceName.OPEN_METEO})
    assert decision.next_state is not None
    assert decision.next_state.notified_at == NOW
    assert decision.state_changed is True
    assert decision.suppress_community_alert is True


def test_dual_outage_with_no_active_record_triggers_one_notice_naming_both() -> None:
    """Row 5: {A, B} unavailable, no active record."""
    decision = evaluate_outage(
        _unavailable(SourceName.SENAMHI), _unavailable(SourceName.OPEN_METEO), None, CITY_SLUG, NOW
    )

    assert len(decision.notices) == 1
    assert decision.notices[0].sources == frozenset({SourceName.SENAMHI, SourceName.OPEN_METEO})
    assert decision.next_state is not None
    assert decision.next_state.is_dual()
    assert decision.suppress_community_alert is True


def test_no_duplicate_notice_while_still_dual_outage() -> None:
    """Row 6: {A, B} unavailable, active record already has {A, B} and was notified."""
    active = _active(frozenset({SourceName.SENAMHI, SourceName.OPEN_METEO}))

    decision = evaluate_outage(
        _unavailable(SourceName.SENAMHI), _unavailable(SourceName.OPEN_METEO), active, CITY_SLUG, NOW
    )

    assert decision.notices == ()
    assert decision.next_state == active
    assert decision.state_changed is False
    assert decision.suppress_community_alert is True


def test_dual_outage_narrows_to_single_source_recovery_and_still_down() -> None:
    """Row 7: {A} unavailable, active record has {A, B} — B recovered, A still down."""
    active = _active(frozenset({SourceName.SENAMHI, SourceName.OPEN_METEO}))

    decision = evaluate_outage(_unavailable(SourceName.SENAMHI), _available(), active, CITY_SLUG, NOW)

    assert len(decision.notices) == 2
    kinds = {notice.kind for notice in decision.notices}
    assert kinds == {NoticeKind.SOURCES_RECOVERED, NoticeKind.SOURCE_UNAVAILABLE}
    recovered_notice = next(n for n in decision.notices if n.kind == NoticeKind.SOURCES_RECOVERED)
    unavailable_notice = next(n for n in decision.notices if n.kind == NoticeKind.SOURCE_UNAVAILABLE)
    assert recovered_notice.sources == frozenset({SourceName.OPEN_METEO})
    assert unavailable_notice.sources == frozenset({SourceName.SENAMHI})
    assert decision.next_state == OutageRecord(
        city_slug=CITY_SLUG, unavailable_sources=frozenset({SourceName.SENAMHI}), opened_at=NOW, notified_at=NOW
    )
    assert decision.suppress_community_alert is False


def test_single_source_widens_to_dual_outage() -> None:
    """Row 8: {A, B} unavailable, active record has only {A} — B newly down."""
    active = _active(frozenset({SourceName.SENAMHI}))

    decision = evaluate_outage(
        _unavailable(SourceName.SENAMHI), _unavailable(SourceName.OPEN_METEO), active, CITY_SLUG, NOW
    )

    assert len(decision.notices) == 1
    assert decision.notices[0].kind == NoticeKind.SOURCE_UNAVAILABLE
    assert decision.notices[0].sources == frozenset({SourceName.OPEN_METEO})
    assert decision.next_state is not None
    assert decision.next_state.unavailable_sources == frozenset({SourceName.SENAMHI, SourceName.OPEN_METEO})
    assert decision.suppress_community_alert is True


def test_no_recovery_notice_without_a_prior_outage() -> None:
    decision = evaluate_outage(_available(), _available(), None, CITY_SLUG, NOW)

    assert not any(n.kind == NoticeKind.SOURCES_RECOVERED for n in decision.notices)


def test_notices_use_a_distinct_kind_from_a_community_alert() -> None:
    """NoticeKind values are distinct from Level values — there is no shared vocabulary.

    S5: the assertion used to be `kind in set(NoticeKind)`, which cannot fail
    while `NoticeKind` has two members and did not test the docstring's claim
    at all. The disjointness is the property worth pinning: a notice tagged
    `imminent` would be indistinguishable from a community alert wherever the
    two are routed by their tag, which is what `ConsoleNotifier` and change 3's
    delivery layer both do.
    """
    decision = evaluate_outage(_unavailable(SourceName.SENAMHI), _available(), None, CITY_SLUG, NOW)

    assert decision.notices[0].kind is NoticeKind.SOURCE_UNAVAILABLE
    assert {kind.value for kind in NoticeKind}.isdisjoint({level.value for level in Level})
