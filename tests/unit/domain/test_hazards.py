"""The flood-relevance rule (weather-sources spec "Only flood-relevant
official warnings may drive an alert").

The rule exists because the SENAMHI Lambayeque page is a multi-hazard feed.
Of the 788 rows live on 2026-09-04, roughly 233 were wind, 131 daytime or
nighttime temperature and only 137 precipitation — and the single warning
*in force* that day was a RED daytime-temperature warning. Feeding that to
the evaluator would have told the whole community that flooding was imminent
because of a heat wave.
"""

from __future__ import annotations

import pytest

from rain_alert.domain.hazards import (
    Phenomenon,
    Zone,
    discard_reason,
    is_flood_relevant,
    may_raise_imminent,
)


class TestPhenomenonFilter:
    @pytest.mark.parametrize(
        "phenomenon",
        [
            Phenomenon.WIND,
            Phenomenon.HIGH_TEMPERATURE,
            Phenomenon.LOW_TEMPERATURE,
            Phenomenon.SNOW,
            Phenomenon.HAIL,
        ],
        ids=["wind", "heat", "cold", "snow", "hail"],
    )
    def test_a_non_precipitation_hazard_is_never_flood_relevant(self, phenomenon: Phenomenon) -> None:
        """Even on the coast, and even at the highest severity: wind, heat,
        cold, snow and hail cannot cause the flooding this system warns about.

        Snow releases as meltwater over weeks rather than as the hours-scale
        runoff that loads the Rio La Leche; hail is intense but too local and
        too brief to load a basin at all.
        """
        assert is_flood_relevant(phenomenon) is False

    def test_precipitation_is_flood_relevant(self) -> None:
        assert is_flood_relevant(Phenomenon.PRECIPITATION) is True

    def test_an_unrecognized_phenomenon_is_treated_as_relevant(self) -> None:
        """Conservative on the unknown: an unrecognized title may be new
        wording for a rainfall hazard. Missing a real flood warning is worse
        than one extra alert."""
        assert is_flood_relevant(Phenomenon.UNKNOWN) is True


class TestZoneDecidesHowFarAWarningMayGo:
    """Zone no longer decides *whether* a rainfall warning counts. It decides
    how far it may take the level.

    The Rio La Leche rises at 4 230 m on Mount Choicopico, inside Ferrenafe
    province, and flows west through the city. SENAMHI measures its critical
    level at Puchaca, in the sierra district of Incahuasi, and attributes
    rises to intense rain in the basin headwaters. Sierra rain is therefore
    upstream of Ferrenafe with hours of lead time — the mechanism behind the
    2017 flooding — so discarding it outright threw away the earliest signal
    the system can get.
    """

    def test_no_zone_removes_a_rainfall_warning_from_the_system(self) -> None:
        """The old rule discarded highlands-only rainfall outright — 144 of
        the 243 live precipitation rows on the Lambayeque page, including its
        single most common precipitation title. Nothing does now: a rainfall
        warning always reaches the evaluator, whatever its geography."""
        assert [zone for zone in Zone if discard_reason(Phenomenon.PRECIPITATION) is not None] == []

    @pytest.mark.parametrize(
        ("zone", "may"),
        [
            (Zone.COAST, True),
            (Zone.COAST_AND_HIGHLANDS, True),
            (Zone.UNKNOWN, True),
            (Zone.HIGHLANDS, False),
        ],
        ids=["coast", "coast and highlands", "unstated", "highlands only"],
    )
    def test_only_a_warning_reaching_the_coast_may_raise_imminent(self, zone: Zone, may: bool) -> None:
        """Ferrenafe city is coastal, so sierra rain reaches it by river
        routing rather than by falling overhead. That warrants preparation,
        not a claim that flooding is imminent."""
        assert may_raise_imminent(Phenomenon.PRECIPITATION, zone) is may

    def test_a_hazard_that_cannot_flood_never_raises_imminent_wherever_it_falls(self) -> None:
        assert may_raise_imminent(Phenomenon.HIGH_TEMPERATURE, Zone.COAST) is False
        assert may_raise_imminent(Phenomenon.SNOW, Zone.COAST_AND_HIGHLANDS) is False

    def test_an_unclassifiable_phenomenon_may_still_raise_imminent_on_the_coast(self) -> None:
        """Conservative on the unknown: an unrecognized title may be new
        wording for a rainfall hazard."""
        assert may_raise_imminent(Phenomenon.UNKNOWN, Zone.COAST) is True


class TestDiscardAudit:
    """Every discard must be explainable, so the operator can audit why a
    warning never reached the evaluator."""

    def test_a_kept_warning_has_no_discard_reason(self) -> None:
        assert discard_reason(Phenomenon.PRECIPITATION) is None

    @pytest.mark.parametrize(
        ("phenomenon", "named"),
        [
            (Phenomenon.HIGH_TEMPERATURE, "high_temperature"),
            (Phenomenon.WIND, "wind"),
            (Phenomenon.SNOW, "snow"),
            (Phenomenon.HAIL, "hail"),
        ],
        ids=["heat", "wind", "snow", "hail"],
    )
    def test_the_phenomenon_that_disqualified_the_warning_is_named(self, phenomenon: Phenomenon, named: str) -> None:
        reason = discard_reason(phenomenon)

        assert reason is not None
        assert named in reason


def test_the_rule_and_the_audit_never_disagree() -> None:
    """A discard reason exactly when the warning is discarded: two readers of
    one rule that could otherwise drift apart."""
    for phenomenon in Phenomenon:
        assert is_flood_relevant(phenomenon) is (discard_reason(phenomenon) is None)


def test_imminent_capability_never_exceeds_flood_relevance() -> None:
    """A warning that cannot drive an alert at all must not be able to drive
    the most severe one. The two rules could otherwise drift into a state
    where a heat aviso is barred from `prepare` but not from `imminent`."""
    for phenomenon in Phenomenon:
        for zone in Zone:
            if may_raise_imminent(phenomenon, zone):
                assert is_flood_relevant(phenomenon) is True
