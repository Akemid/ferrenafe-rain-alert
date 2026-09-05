"""MessageComposer — the deterministic template that satisfies the
`MessageComposer` port in this change (design.md D11; alert-cycle spec).

Recipient-facing text is neutral, professional Spanish — the one deliberate
exception in this codebase, since the composed message is read by the
Ferreñafe community (design spec 7.4). Everything else (identifiers,
comments, tests, the operator audit trail in `domain/reasons.py`) stays
English.

Two consequences of that split are load-bearing here:

- The evaluator's reasons arrive as structured `Reason` values, and this
  module renders them into Spanish. It never prints the operator's English
  audit line, and never quotes an internal threshold value.
- Timestamps are converted to the configured local timezone for display
  (D7). Domain datetimes are UTC, but a community reader must not be handed
  an ISO-8601 string with a `+00:00` offset.

A third rule was added after the security review, and it is the one a new
composer is most likely to drop: **every source-derived free text this module
renders goes through `domain.sanitize.sanitize_source_text` first.** The body
is assembled with `"\\n".join` into a line-oriented format whose grammar is
`- ` bullets and `Motivos:` / `Recomendaciones:` headers, so any string the
system did not write itself can write that grammar. The audit of what is
source-derived, field by field:

| Field reaching the body | Origin | Sanitized |
|---|---|---|
| `WarningReason.title` | scraped aviso title | **yes** |
| `WarningReason.level` | `WarningLevel`, a fixed token map | not free text |
| `ForecastThresholdReason.*` | numbers, rendered with format specifiers | not free text |
| `SenamhiUnavailableReason`, `NoQualifyingWarningReason` | no fields | n/a |
| `MessageRequest.city`, `.timezone`, `.checklist` | `ConfigRepository` | operator-owned |

`MessageRequest.warning` and `.forecast` are not rendered by this composer,
but `WarningSummary.title` is the same scraped string, so change 2's composer
inherits the same obligation. It is stated on the port for that reason.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import assert_never
from zoneinfo import ZoneInfo

from rain_alert.domain.messages import AlertMessage, MessageRequest
from rain_alert.domain.reasons import (
    ForecastThresholdReason,
    NoQualifyingWarningReason,
    Reason,
    SenamhiUnavailableReason,
    WarningReason,
)
from rain_alert.domain.sanitize import sanitize_source_text
from rain_alert.domain.values import Level, WarningLevel

_LEVEL_LABELS_ES: Mapping[Level, str] = {
    Level.NONE: "sin riesgo",
    Level.PREPARE: "prepárate",
    Level.IMMINENT: "riesgo inminente",
}

_WARNING_LEVEL_LABELS_ES: Mapping[WarningLevel, str] = {
    WarningLevel.YELLOW: "amarillo",
    WarningLevel.ORANGE: "naranja",
    WarningLevel.RED: "rojo",
}

_LOCAL_TIME_FORMAT = "%d/%m/%Y %H:%M"


def _local_time(moment: datetime, timezone: str) -> str:
    """`moment` (UTC, D7) as a readable local time string."""
    return moment.astimezone(ZoneInfo(timezone)).strftime(_LOCAL_TIME_FORMAT)


def _reason_line_es(reason: Reason) -> str | None:
    """One reason as neutral Spanish prose, or `None` when the body already
    states the same fact in its own dedicated sentence."""
    match reason:
        case WarningReason(level=level, title=title):
            # `title` is scraped free text. It is the only source-derived
            # string this body interpolates, and it must not be able to write
            # the body's own line grammar. See `domain/sanitize.py`.
            clean_title = sanitize_source_text(title)
            return f"Aviso oficial del SENAMHI, nivel {_WARNING_LEVEL_LABELS_ES[level]}: {clean_title}."
        case ForecastThresholdReason(accumulated_mm=accumulated, hours=hours, probability_pct=probability):
            # The internal threshold value is deliberately not mentioned: it is
            # operator detail, not information a recipient can act on.
            return (
                f"Se pronostican {accumulated:.1f} mm de lluvia acumulada en {hours} horas, "
                f"con una probabilidad máxima de {probability} %."
            )
        case SenamhiUnavailableReason():
            return None  # stated once by the degraded sentence below
        case NoQualifyingWarningReason():
            return (
                "No hay un aviso oficial vigente del SENAMHI para la zona; "
                "esta alerta se basa únicamente en el pronóstico del tiempo."
            )
        case _:  # pragma: no cover - a new reason variant must be given Spanish wording
            assert_never(reason)


class MessageComposer:
    """Deterministic, structural implementation of the `MessageComposer`
    port. Invoked by `RunAlertCycle` only after `AlertPolicy` has already
    authorized the send; its return value never feeds back into any
    decision (the LLM composer invariant, preserved here structurally)."""

    def compose(self, request: MessageRequest) -> AlertMessage:
        level_label = _LEVEL_LABELS_ES[request.level]
        title = f"Alerta de lluvias — {request.city} — {level_label}"

        window_start = _local_time(request.window.start, request.timezone)
        window_end = _local_time(request.window.end, request.timezone)
        lines = [
            f"Ciudad: {request.city}",
            f"Nivel: {level_label}",
            f"Ventana: del {window_start} al {window_end} (hora local, {request.timezone})",
        ]

        reason_lines = [line for line in (_reason_line_es(reason) for reason in request.reasons) if line is not None]
        if reason_lines:
            lines.append("Motivos:")
            lines.extend(f"- {line}" for line in reason_lines)

        if request.senamhi_status == "unavailable":
            lines.append(
                "La fuente oficial SENAMHI no estuvo disponible durante esta evaluación; "
                "esta alerta se generó solo con el pronóstico de Open-Meteo."
            )
        if request.open_meteo_status == "unavailable":
            lines.append("La fuente Open-Meteo no estuvo disponible durante esta evaluación.")
        if request.checklist:
            lines.append("Recomendaciones:")
            lines.extend(f"- {item}" for item in request.checklist)

        body = "\n".join(lines)
        return AlertMessage(title=title, body=body, level=request.level, valid_until=request.window.end)
