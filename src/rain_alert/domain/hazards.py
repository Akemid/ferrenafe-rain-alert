"""Which official warnings may drive a flood alert for Ferrenafe.

*Added 2026-09-04, owner-approved. See design.md section 5.1.*

The SENAMHI Lambayeque warnings page is a **multi-hazard** feed, which the
original design did not account for. Of the 788 rows live on 2026-09-04,
roughly 233 were wind, 131 daytime or nighttime temperature and only 137
precipitation. The single warning *in force* that day was a RED
`INCREMENTO DE TEMPERATURA DIURNA` — a heat warning. Since the evaluator's
first branch raises `imminent` on any orange or red warning, wiring the
scraper up as designed would have told the whole Ferrenafe community that
flooding was imminent because of a heat wave.

Two rules therefore stand between the page and the evaluator, and they are
deliberately *different questions*:

1. **`is_flood_relevant(phenomenon)`** — may this warning drive a flood alert
   at all? Only rainfall can. Wind, heat, cold, snow and hail cannot.
2. **`may_raise_imminent(phenomenon, zone)`** — may it, on its own, claim
   that flooding is *imminent*? Only if it reaches the coast.

The geography rule was originally the same question as the first one:
highlands-only warnings were discarded outright. **That was corrected on
2026-09-04, owner-approved, after the hydrology was checked** — see
`may_raise_imminent` for the reasoning and the measured cost of the old rule.

Both are expressed here, in the domain, as pure functions over classified
values. The translation from SENAMHI's Spanish title vocabulary into these
terms is the *adapter's* job (`adapters/senamhi_classification.py`), because
that vocabulary belongs to the source, not to the domain. This module owns
the rules; the adapter owns the words.

**The asymmetry around `UNKNOWN` is deliberate.** An unclassifiable
phenomenon is treated as relevant, and an unclassifiable zone is treated as
reaching the coast, because missing a real flood warning is far worse than
raising one extra alert. `HIGHLANDS` is a positive statement that the coast
is not covered; `UNKNOWN` only means the title did not say.
"""

from __future__ import annotations

from enum import StrEnum


class Phenomenon(StrEnum):
    """The meteorological hazard a warning is about.

    Every family SENAMHI actually publishes has a member here, deliberately.
    `UNKNOWN` is treated as flood-relevant, so each family left unclassified
    is a hole in the filter, not a neutral gap: `NEVADA` and `GRANIZO` would
    otherwise reach the evaluator as if they were rain.
    """

    PRECIPITATION = "precipitation"
    WIND = "wind"
    HIGH_TEMPERATURE = "high_temperature"
    LOW_TEMPERATURE = "low_temperature"
    #: Snowfall. A sierra hazard, and not a flood driver for Ferrenafe: snow
    #: falls above the Rio La Leche headwaters and releases as meltwater over
    #: weeks, not as the hours-scale runoff this system warns about.
    SNOW = "snow"
    #: Hail. Intense but extremely local and short-lived; it does not load a
    #: river basin the way sustained rainfall does.
    HAIL = "hail"
    UNKNOWN = "unknown"


class Zone(StrEnum):
    """The part of the department a warning covers."""

    COAST = "coast"
    HIGHLANDS = "highlands"
    COAST_AND_HIGHLANDS = "coast_and_highlands"
    UNKNOWN = "unknown"


#: Phenomena that can produce the flooding this system warns about. `UNKNOWN`
#: is included on purpose (see the module docstring).
FLOOD_RELEVANT_PHENOMENA = frozenset({Phenomenon.PRECIPITATION, Phenomenon.UNKNOWN})

#: Zones that positively exclude the coast, where Ferrenafe city sits. Rain
#: here is upstream of the city rather than over it — see `may_raise_imminent`.
ZONES_WITHOUT_THE_COAST = frozenset({Zone.HIGHLANDS})


def is_flood_relevant(phenomenon: Phenomenon) -> bool:
    """Whether a warning about this phenomenon may drive a flood alert.

    Geography is deliberately not a parameter. Rain anywhere in the Rio La
    Leche basin can flood Ferrenafe; what geography decides is how *far* the
    warning may take the level, which is `may_raise_imminent`'s question.
    """
    return phenomenon in FLOOD_RELEVANT_PHENOMENA


def may_raise_imminent(phenomenon: Phenomenon, zone: Zone) -> bool:
    """Whether this warning may, on its own, raise `imminent`.

    *Owner-approved 2026-09-04, replacing an outright highlands exclusion.*

    **Why highlands rain counts at all.** The Rio La Leche rises at 4 230 m
    on Mount Choicopico, inside Ferrenafe province, and flows west through
    Ferrenafe and Lambayeque provinces. SENAMHI measures its critical level
    at the Puchaca station in Incahuasi — a sierra district *of Ferrenafe
    province* — and attributes rises there to intense rain in the basin
    headwaters. Riverside defence works protect Pitipo and Incahuasi in this
    province, plus Pacora, Illimo and Jayanca. Sierra rain is therefore
    upstream of the city with hours of lead time. In the 2017 Nino Costero,
    the La Leche overflowed under intense rain and Batangrande, in Pitipo
    district of Ferrenafe province, was among the worst-affected places in
    Peru; the Taymi and Loco overflows cut the road linking Pitipo to
    Ferrenafe city. The previous rule discarded 144 of
    the 243 live precipitation rows on the Lambayeque page, including
    `PRECIPITACIONES EN LA SIERRA`, its single most common precipitation
    title at 78 rows — that is, it threw away the earliest signal available.

    **Why it is only an early signal.** Ferrenafe city itself is coastal.
    Sierra rain reaches it by river routing, not by falling overhead, so a
    highlands-only warning justifies *preparation* — it may contribute to
    `prepare` through the combined branch — but not a claim that flooding is
    already imminent. Claiming imminence on rain that has not yet run down
    the basin is exactly the kind of false alarm that costs the system the
    credibility every future real warning depends on.

    A warning that is not flood-relevant at all can never raise `imminent`,
    whatever its geography.
    """
    return is_flood_relevant(phenomenon) and zone not in ZONES_WITHOUT_THE_COAST


def discard_reason(phenomenon: Phenomenon) -> str | None:
    """Why this warning was discarded, or `None` when it was kept.

    The operator audit trail needs to state why a warning never reached the
    evaluator; a silent filter is indistinguishable from a broken scraper.
    """
    if phenomenon not in FLOOD_RELEVANT_PHENOMENA:
        return f"phenomenon {phenomenon.value} cannot cause flooding"
    return None
