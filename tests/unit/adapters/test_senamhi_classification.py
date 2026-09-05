"""Classification of SENAMHI warning titles into domain phenomenon/zone terms.

Every title in the tables below is verbatim from the live Lambayeque page on
2026-09-04 (65 distinct titles across 788 rows), except the two explicitly
marked as invented to probe the unknown branches. Matching is on normalized
text — accent-stripped and upper-cased — because the wording varies: the page
carries both `PRECIPITACIONES EN LA COSTA NORTE Y SIERRA` and
`PRECIPITACIONES EN COSTA NORTE Y SIERRA`, so an exact-string map would have
silently dropped the second.
"""

from __future__ import annotations

import pytest

from rain_alert.adapters.senamhi_classification import classify_phenomenon, classify_zone, normalize_title
from rain_alert.domain.hazards import Phenomenon, Zone, is_flood_relevant, may_raise_imminent


class TestNormalization:
    def test_accents_case_and_whitespace_are_normalized_away(self) -> None:
        assert normalize_title("  Precipitaciones  en la Costa\nNorte ") == "PRECIPITACIONES EN LA COSTA NORTE"

    def test_normalization_is_idempotent(self) -> None:
        once = normalize_title("Descenso de Temperatura Nocturna en la Sierra")
        assert normalize_title(once) == once


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("PRECIPITACIONES EN LA SIERRA", Phenomenon.PRECIPITATION),
        ("PRECIPITACIONES EN LA COSTA NORTE Y SIERRA", Phenomenon.PRECIPITATION),
        ("PRECIPITACIONES EN COSTA NORTE Y SIERRA", Phenomenon.PRECIPITATION),
        ("LLUVIA EN LA COSTA", Phenomenon.PRECIPITATION),
        ("LLUVIA EN LA COSTA Y SIERRA NORTE", Phenomenon.PRECIPITATION),
        ("INCREMENTO DE VIENTO EN LA COSTA", Phenomenon.WIND),
        ("INCREMENTO DE VIENTO SIERRA NORTE", Phenomenon.WIND),
        ("INCREMENTO DE TEMPERATURA DIURNA EN LA COSTA Y SIERRA", Phenomenon.HIGH_TEMPERATURE),
        ("INCREMENTO DE TEMPERATURA EN LA COSTA", Phenomenon.HIGH_TEMPERATURE),
        # The same aviso family *with the article*. SENAMHI publishes both
        # wordings; 56 rows across 9 of the 12 regional pages used the
        # article form on 2026-09-04.
        ("INCREMENTO DE LA TEMPERATURA DIURNA EN LA COSTA", Phenomenon.HIGH_TEMPERATURE),
        ("INCREMENTO DE LA TEMPERATURA DIURNA EN LA SELVA CENTRO Y SUR", Phenomenon.HIGH_TEMPERATURE),
        ("DESCENSO DE TEMPERATURA NOCTURNA EN LA SIERRA", Phenomenon.LOW_TEMPERATURE),
        ("DESCENSO DE TEMPERATURA EN LA SIERRA NORTE", Phenomenon.LOW_TEMPERATURE),
        ("DESCENSO DE LA TEMPERATURA NOCTURNA EN LA SIERRA CENTRO Y SUR", Phenomenon.LOW_TEMPERATURE),
        ("DESCENSO DE LA TEMPERATURA NOCTURNA EN LA SIERRA", Phenomenon.LOW_TEMPERATURE),
        (
            "INCREMENTO DE TEMPERATURA DIURNA EN LA COSTA Y SIERRA (EXTENSION DEL AVISO 335)",
            Phenomenon.HIGH_TEMPERATURE,
        ),
        ("Precipitaciones en la costa norte", Phenomenon.PRECIPITATION),
        # Hazard families that used to fall through to `UNKNOWN`. Each is a
        # real SENAMHI aviso family with a known answer, and every family left
        # unclassified widens the hole `UNKNOWN`-is-relevant leaves open.
        ("LLOVIZNA EN LA COSTA CENTRO Y SUR", Phenomenon.PRECIPITATION),
        ("LLOVIZNAS EN LA COSTA", Phenomenon.PRECIPITATION),
        ("GARUA EN LA COSTA", Phenomenon.PRECIPITATION),
        ("NEVADA EN LA SIERRA CENTRO Y SUR", Phenomenon.SNOW),
        ("NEVADAS EN LA SIERRA SUR", Phenomenon.SNOW),
        ("GRANIZO EN LA SIERRA SUR", Phenomenon.HAIL),
        ("PRIMER FRIAJE EN LA SELVA", Phenomenon.LOW_TEMPERATURE),
        ("VIGESIMO SEGUNDO FRIAJE EN LA SELVA (EXTENSION DEL AVISO 224)", Phenomenon.LOW_TEMPERATURE),
        ("ANOMALIA TERMICA MARINA", Phenomenon.UNKNOWN),  # invented: probes the unknown branch
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_the_phenomenon_is_read_from_the_title(title: str, expected: Phenomenon) -> None:
    assert classify_phenomenon(title) is expected


@pytest.mark.parametrize(
    ("title", "phenomenon"),
    [
        ("INCREMENTO DE LA TEMPERATURA DIURNA EN LA COSTA Y SIERRA", Phenomenon.HIGH_TEMPERATURE),
        ("DESCENSO DE LA TEMPERATURA NOCTURNA EN LA COSTA", Phenomenon.LOW_TEMPERATURE),
    ],
    ids=["heat with the article", "cold with the article"],
)
def test_the_article_variant_of_a_temperature_aviso_is_still_filtered_out(title: str, phenomenon: Phenomenon) -> None:
    """The regression a substring match against a fixed phrase let through.

    `INCREMENTO DE TEMPERATURA` is not a substring of
    `INCREMENTO DE LA TEMPERATURA DIURNA ...`, so the fixed-phrase form
    classified the article variant as `UNKNOWN` — and `UNKNOWN` is
    deliberately treated as flood-relevant. A live heat aviso worded with the
    article therefore reached the evaluator and could raise `imminent`.
    """
    assert classify_phenomenon(title) is phenomenon
    assert is_flood_relevant(classify_phenomenon(title)) is False


def test_a_marker_is_matched_on_word_boundaries_not_as_a_substring() -> None:
    """`TEMPERATURAS` in prose must not be read as the aviso family, and a
    word that merely starts with a marker must not match it either."""
    assert classify_phenomenon("CONTRASTE DE TEMPERATURAS EN LA COSTA") is Phenomenon.UNKNOWN
    assert classify_phenomenon("VIENTOS FUERTES EN LA COSTA") is Phenomenon.WIND


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("PRECIPITACIONES EN LA COSTA", Zone.COAST),
        ("LLUVIA EN LA COSTA NORTE", Zone.COAST),
        ("INCREMENTO DE VIENTO EN LA COSTA NORTE Y CENTRO", Zone.COAST),
        ("PRECIPITACIONES EN LA SIERRA", Zone.HIGHLANDS),
        ("PRECIPITACIONES EN LA SIERRA NORTE Y CENTRO", Zone.HIGHLANDS),
        ("DESCENSO DE TEMPERATURA NOCTURNA EN LA SIERRA NORTE Y SUR", Zone.HIGHLANDS),
        ("PRECIPITACIONES EN LA COSTA NORTE Y SIERRA", Zone.COAST_AND_HIGHLANDS),
        ("PRECIPITACIONES EN COSTA NORTE Y SIERRA", Zone.COAST_AND_HIGHLANDS),
        ("PRECIPITACIONES EN LA SIERRA NORTE, CENTRO Y COSTA NORTE", Zone.COAST_AND_HIGHLANDS),
        ("INCREMENTO DE TEMPERATURA DIURNA EN LA COSTA Y EN LA SIERRA", Zone.COAST_AND_HIGHLANDS),
        ("INCREMENTO DE TEMPERATURA DIURNA EN LA SIERRA Y SELVA", Zone.HIGHLANDS),
        ("Precipitaciones en la Costa", Zone.COAST),
        ("PRECIPITACIONES INTENSAS", Zone.UNKNOWN),  # invented: probes the unknown branch
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_the_zone_is_read_from_the_title(title: str, expected: Zone) -> None:
    assert classify_zone(title) is expected


def test_the_word_costa_is_matched_as_a_word_not_as_a_substring() -> None:
    """`COSTADO` must not be read as `COSTA`; a naive `in` check would."""
    assert classify_zone("PRECIPITACIONES EN EL COSTADO ANDINO Y LA SIERRA") is Zone.HIGHLANDS


class TestRealTitlesEndToEnd:
    """The classification and the domain rules together, on live titles. These
    are the cases the owner-approved contract changes exist for."""

    def test_the_warning_in_force_on_2026_09_04_is_not_flood_relevant(self) -> None:
        """The regression this whole filter exists to prevent: a RED warning
        in force, which the evaluator would have read as imminent flooding."""
        title = "INCREMENTO DE TEMPERATURA DIURNA EN LA COSTA Y SIERRA"

        assert is_flood_relevant(classify_phenomenon(title)) is False

    @pytest.mark.parametrize(
        ("title", "relevant", "imminent_capable"),
        [
            ("PRECIPITACIONES EN LA COSTA NORTE Y SIERRA", True, True),
            ("PRECIPITACIONES EN LA COSTA", True, True),
            ("LLUVIA EN LA COSTA", True, True),
            ("PRECIPITACIONES EN COSTA NORTE Y SIERRA", True, True),
            # Highlands-only rainfall: upstream in the Rio La Leche basin, so
            # relevant as an early signal but never an imminence claim.
            ("PRECIPITACIONES EN LA SIERRA", True, False),
            ("PRECIPITACIONES EN LA SIERRA NORTE Y CENTRO", True, False),
            ("LLUVIA EN LA SIERRA NORTE", True, False),
            ("LLOVIZNA EN LA COSTA CENTRO Y SUR", True, True),
            ("INCREMENTO DE VIENTO EN LA COSTA", False, False),
            ("INCREMENTO DE TEMPERATURA DIURNA EN LA COSTA", False, False),
            ("INCREMENTO DE LA TEMPERATURA DIURNA EN LA COSTA", False, False),
            ("DESCENSO DE TEMPERATURA NOCTURNA EN LA COSTA", False, False),
            ("DESCENSO DE LA TEMPERATURA NOCTURNA EN LA SIERRA", False, False),
            ("NEVADA EN LA SIERRA CENTRO Y SUR", False, False),
            ("PRIMER FRIAJE EN LA SELVA", False, False),
        ],
        ids=lambda value: value if isinstance(value, str) else "",
    )
    def test_live_titles_are_filtered_as_the_owner_approved(
        self, title: str, relevant: bool, imminent_capable: bool
    ) -> None:
        phenomenon, zone = classify_phenomenon(title), classify_zone(title)

        assert is_flood_relevant(phenomenon) is relevant
        assert may_raise_imminent(phenomenon, zone) is imminent_capable
