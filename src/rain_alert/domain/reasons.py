"""Structured risk reasons, plus the operator-facing English rendering
(design.md section 3; design spec 6.1).

A reason serves two audiences whose words cannot be the same string:

- the **operator** needs an audit trail with the exact numbers that drove the
  verdict, in English like the rest of this codebase (design.md section 9
  shows the CLI printing them);
- the **community** needs neutral, professional Spanish prose (design spec
  7.4), with no internal threshold values and no raw UTC timestamps.

A pre-formatted English string cannot serve both, and using one leaked
operator wording into the recipient body. So `RiskEvaluator` emits `Reason`
values carrying the numbers it decided on — the audit trail still cannot
drift from the verdict, because the branch that decided also builds the
reason — and each audience renders them: English here, Spanish in
`domain/template.py`.

The variants are a tagged union rather than one record with optional fields,
for the same reason `SourceResult` is (D2): a `WarningReason` has no
millimetre reading to misread, and `match` narrows cleanly.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import assert_never

from rain_alert.domain.values import WarningLevel


class ReasonKind(StrEnum):
    """Serializable tag for a reason variant.

    Needed because reasons are persisted: `AlertRecord.reasons` becomes the
    `reasons` attribute of the `ALERT#<slug>` item in change 3, and the CLI's
    `--json` output in change 3 emits them too.
    """

    OFFICIAL_WARNING = "official_warning"
    FORECAST_THRESHOLD = "forecast_threshold"
    SENAMHI_UNAVAILABLE = "senamhi_unavailable"
    NO_QUALIFYING_WARNING = "no_qualifying_warning"


@dataclass(frozen=True, slots=True)
class WarningReason:
    """An official SENAMHI warning drove (part of) the verdict."""

    level: WarningLevel
    title: str

    @property
    def kind(self) -> ReasonKind:
        return ReasonKind.OFFICIAL_WARNING


@dataclass(frozen=True, slots=True)
class ForecastThresholdReason:
    """A forecast accumulation/probability reading drove (part of) the verdict.

    `hours` is the horizon actually evaluated, never the horizon requested, so
    a short forecast is never reported as a longer one.
    `probability_threshold_pct` is set only when a stricter, forecast-only
    threshold applied, and is operator-only detail.
    """

    accumulated_mm: float
    hours: int
    probability_pct: int
    probability_threshold_pct: int | None = None

    @property
    def kind(self) -> ReasonKind:
        return ReasonKind.FORECAST_THRESHOLD


@dataclass(frozen=True, slots=True)
class SenamhiUnavailableReason:
    """The official source could not be read, so evaluation was forecast-only."""

    @property
    def kind(self) -> ReasonKind:
        return ReasonKind.SENAMHI_UNAVAILABLE


@dataclass(frozen=True, slots=True)
class NoQualifyingWarningReason:
    """The official source was healthy but published no qualifying warning, so
    the forecast alone had to clear the stricter threshold."""

    @property
    def kind(self) -> ReasonKind:
        return ReasonKind.NO_QUALIFYING_WARNING


type Reason = WarningReason | ForecastThresholdReason | SenamhiUnavailableReason | NoQualifyingWarningReason


def render_reason_en(reason: Reason) -> str:
    """The operator audit line for one reason (English, with the numbers)."""
    match reason:
        case WarningReason(level=level, title=title):
            return f"official SENAMHI warning: {level.value} — {title}"
        case ForecastThresholdReason(
            accumulated_mm=accumulated,
            hours=hours,
            probability_pct=probability,
            probability_threshold_pct=threshold,
        ):
            reading = f"{accumulated:.1f} mm / {hours} h at {probability}% max probability"
            return reading if threshold is None else f"{reading} (required threshold {threshold}%)"
        case SenamhiUnavailableReason():
            return "official SENAMHI source unavailable; forecast-only evaluation"
        case NoQualifyingWarningReason():
            return "no qualifying official SENAMHI warning; forecast-only evaluation at the stricter threshold"
        case _:  # pragma: no cover - exhaustiveness guard for mypy
            assert_never(reason)


def render_reasons_en(reasons: tuple[Reason, ...]) -> tuple[str, ...]:
    """The operator audit trail, in the order the evaluator produced it."""
    return tuple(render_reason_en(reason) for reason in reasons)
