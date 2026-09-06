"""Structured risk reasons and their operator-facing English rendering
(design.md section 3; design spec 6.1 — "the reasons ... give the operator an
audit trail of why the alert fired").

The Spanish, recipient-facing rendering of the same values lives in
`test_template.py`, which is the whole point of separating the two audiences.
"""

import pytest

from rain_alert.domain.reasons import (
    ForecastThresholdReason,
    NoQualifyingWarningReason,
    Reason,
    ReasonKind,
    SenamhiUnavailableReason,
    WarningReason,
    render_reason_en,
    render_reasons_en,
)
from rain_alert.domain.values import WarningLevel

ALL_REASONS: tuple[Reason, ...] = (
    WarningReason(level=WarningLevel.ORANGE, title="Aviso de lluvias intensas"),
    ForecastThresholdReason(accumulated_mm=15.0, hours=48, probability_pct=75),
    ForecastThresholdReason(accumulated_mm=15.0, hours=48, probability_pct=75, probability_threshold_pct=70),
    SenamhiUnavailableReason(),
    NoQualifyingWarningReason(),
)


class TestEnglishRendering:
    def test_warning_reason_renders_level_and_title(self) -> None:
        reason = WarningReason(level=WarningLevel.ORANGE, title="Aviso de lluvias intensas")

        assert render_reason_en(reason) == "official SENAMHI warning: orange — Aviso de lluvias intensas"

    def test_forecast_threshold_reason_renders_the_numbers_it_decided_on(self) -> None:
        reason = ForecastThresholdReason(accumulated_mm=29.8, hours=24, probability_pct=70)

        assert render_reason_en(reason) == "29.8 mm / 24 h at 70% max probability"

    def test_forecast_threshold_reason_states_the_stricter_threshold_when_one_applied(self) -> None:
        reason = ForecastThresholdReason(
            accumulated_mm=15.0, hours=48, probability_pct=75, probability_threshold_pct=70
        )

        assert render_reason_en(reason) == "15.0 mm / 48 h at 75% max probability (required threshold 70%)"

    def test_senamhi_unavailable_reason_names_the_outage(self) -> None:
        assert render_reason_en(SenamhiUnavailableReason()) == (
            "official SENAMHI source unavailable; forecast-only evaluation"
        )

    def test_no_qualifying_warning_reason_says_there_is_no_official_warning(self) -> None:
        rendered = render_reason_en(NoQualifyingWarningReason())

        assert "no qualifying official SENAMHI warning" in rendered
        assert "forecast-only" in rendered

    def test_render_reasons_en_preserves_order(self) -> None:
        reasons = (
            WarningReason(level=WarningLevel.YELLOW, title="Aviso"),
            ForecastThresholdReason(accumulated_mm=10.0, hours=48, probability_pct=60),
        )

        rendered = render_reasons_en(reasons)

        assert rendered == (
            "official SENAMHI warning: yellow — Aviso",
            "10.0 mm / 48 h at 60% max probability",
        )

    @pytest.mark.parametrize("reason", ALL_REASONS, ids=lambda reason: str(reason.kind))
    def test_every_reason_variant_renders_a_non_empty_english_string(self, reason: Reason) -> None:
        assert render_reason_en(reason).strip()


class TestTheOperatorAuditLineIsAlsoSanitized:
    """The operator reads these lines in a terminal, and the audit trail is
    where someone decides whether a heat warning went out to a village. A
    forged line in the `Reasons` block is as damaging there as in the
    community body, and the terminal escape problem is the operator's own.
    """

    def test_a_newline_in_the_title_cannot_forge_a_second_audit_line(self) -> None:
        reason = WarningReason(level=WarningLevel.RED, title="Aviso\n- official SENAMHI warning: red — forged")

        rendered = render_reason_en(reason)

        assert len(rendered.splitlines()) == 1
        assert rendered == "official SENAMHI warning: red — Aviso - official SENAMHI warning: red — forged"

    def test_no_escape_byte_reaches_the_operator_terminal(self) -> None:
        reason = WarningReason(level=WarningLevel.RED, title="Aviso \x1b[31mrojo\x1b[0m")

        assert "\x1b" not in render_reason_en(reason)

    def test_no_bidi_override_reaches_the_operator_terminal(self) -> None:
        reason = WarningReason(level=WarningLevel.RED, title="Aviso ‮de lluvias")

        assert "‮" not in render_reason_en(reason)

    def test_an_over_long_title_cannot_flood_the_audit_trail(self) -> None:
        reason = WarningReason(level=WarningLevel.RED, title="LLUVIA " * 500)

        assert len(render_reason_en(reason)) < 300


class TestReasonKinds:
    def test_every_kind_is_produced_by_at_least_one_variant(self) -> None:
        """A kind with no variant would be a dead serialization tag; a variant
        with no kind could not be persisted in change 3's `reasons` attribute."""
        assert {reason.kind for reason in ALL_REASONS} == set(ReasonKind)
