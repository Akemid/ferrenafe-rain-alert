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
| `MessageRequest.city`, `.timezone` | `ConfigRepository` | operator-owned |
| `MessageRequest.checklist` | `ConfigRepository` | **yes**, see below |

`checklist` was the exception until an adversarial review pointed out that
"operator-owned" answers the wrong question. The audit above is not about
*trust*, it is about whether a string can write the body's line grammar, and
a checklist item can: an item containing a newline produced a second
`Recomendaciones:` section in the community body — the same defect change 1
closed for aviso titles, differing only in that the author is trusted. An
item with a tab or a bidi override did the same to the rendered line. Four
configurations the operator can reach today made the template's own output
fail its own validator, which is more expensive than an attack: this output
is the fallback, so if it cannot pass, the system has nothing left to send.

The list is also **bounded**, not only cleaned. Thirty realistic items put
the body at 1 906 characters against a 1 500-character cap. Items that do
not fit are dropped and the cut is stated in the body rather than made
silently, on the same reasoning as `sanitize.TRUNCATION_MARKER`: a list that
just stops reads as a complete list.

`MessageRequest.warning` and `.forecast` are not rendered by this composer,
but `WarningSummary.title` is the same scraped string, so change 2's composer
inherits the same obligation. It is stated on the port for that reason.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import assert_never
from zoneinfo import ZoneInfo

from rain_alert.domain.message_validation import MAX_BODY_LENGTH
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

#: Recipient-facing, therefore neutral professional Spanish. Visible on
#: purpose: a checklist that simply stops reads as a complete checklist, and
#: a reader in a flood would have no way to know an instruction was missing.
CHECKLIST_TRUNCATED_ES = "- (lista de recomendaciones recortada por su longitud)"


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


def _checklist_lines(items: tuple[str, ...], budget: int) -> list[str]:
    """The checklist as `- ` bullets: sanitized, and cut to `budget`.

    Sanitized because an item is free text rendered into a line-oriented
    format, exactly like an aviso title, and a newline in one wrote a second
    `Recomendaciones:` section into the community body. That the author is
    trusted changes who to talk to about it, not what the reader sees.

    Cut because the body has a cap the deterministic template must never
    breach: its output is the fallback, and a fallback the validator rejects
    leaves the system with nothing to send. Thirty realistic items put the
    body 400 characters over. The cut is stated in the body rather than made
    silently.

    Args:
        items: The operator's checklist, as configured.
        budget: Characters available for these lines, newline separators
            included.

    Returns:
        One `- ` bullet per item that fits, in order, followed by
        `CHECKLIST_TRUNCATED_ES` when any item had to be dropped. An item
        that renders as the marker itself is one of the dropped ones, so the
        marker is always the template's own and always the final line.
    """
    bullets = [f"- {sanitize_source_text(item)}" for item in items]
    # An item whose sanitized text is the marker's own wording rendered a
    # byte-identical marker line in the middle of the list, with real
    # instructions printed underneath it and nothing actually cut. The
    # marker is the reader's only signal that instructions were dropped, and
    # a signal that can appear when it is not true is not a signal.
    #
    # Refused rather than asserted against. Asserting the marker's position
    # would make this template *raise* on a configuration the operator can
    # reach, and its output is the fallback — a fallback that cannot compose
    # leaves the system with nothing to send, which is the one failure
    # invariant V0 exists to prevent. Refusing degrades instead, and it
    # keeps the marker honest for free: the list really was cut, by exactly
    # this item, so the marker below says something true and says it last.
    honest = [bullet for bullet in bullets if bullet != CHECKLIST_TRUNCATED_ES]
    if len(honest) == len(bullets) and sum(len(bullet) + 1 for bullet in honest) <= budget:
        return honest

    bullets = honest
    kept: list[str] = []
    used = len(CHECKLIST_TRUNCATED_ES) + 1
    for bullet in bullets:
        if used + len(bullet) + 1 > budget:
            break
        kept.append(bullet)
        used += len(bullet) + 1
    kept.append(CHECKLIST_TRUNCATED_ES)
    return kept


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
            # Measured against what is already written, so the cap holds for
            # the finished body and not for the checklist in isolation.
            budget = MAX_BODY_LENGTH - len("\n".join(lines))
            lines.extend(_checklist_lines(request.checklist, budget))

        body = "\n".join(lines)
        return AlertMessage(title=title, body=body, level=request.level, valid_until=request.window.end)
