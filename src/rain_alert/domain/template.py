"""MessageComposer — the deterministic template that satisfies the
`MessageComposer` port in this change (design.md D11; alert-cycle spec).

Recipient-facing text is neutral, professional Spanish — the one deliberate
exception in this codebase, since the composed message is read by the
Ferreñafe community. Everything else (identifiers, comments, tests) stays
English.
"""

from __future__ import annotations

from collections.abc import Mapping

from rain_alert.domain.messages import AlertMessage, MessageRequest
from rain_alert.domain.values import Level

_LEVEL_LABELS_ES: Mapping[Level, str] = {
    Level.NONE: "sin riesgo",
    Level.PREPARE: "prepárate",
    Level.IMMINENT: "riesgo inminente",
}


class MessageComposer:
    """Deterministic, structural implementation of the `MessageComposer`
    port. Invoked by `RunAlertCycle` only after `AlertPolicy` has already
    authorized the send; its return value never feeds back into any
    decision (the LLM composer invariant, preserved here structurally)."""

    def compose(self, request: MessageRequest) -> AlertMessage:
        level_label = _LEVEL_LABELS_ES[request.level]
        title = f"Alerta de lluvias — {request.city} — {level_label}"

        lines = [
            f"Ciudad: {request.city}",
            f"Nivel: {level_label}",
            f"Ventana: {request.window.start.isoformat()} a {request.window.end.isoformat()}",
        ]
        if request.reasons:
            lines.append("Motivos:")
            lines.extend(f"- {reason}" for reason in request.reasons)
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
