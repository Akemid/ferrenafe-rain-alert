"""should_send — pure alert-dedup rule (alert-dedup spec; design.md section 6).

No `AlertRepository` import here: the port-bound shell that queries the
repository and delegates to this function lives in
`application/policies.py` — importing a port from `domain/` would fail the
architecture boundary test (tasks.md "Open Items for Apply").
"""

from __future__ import annotations

from dataclasses import dataclass

from rain_alert.domain.entities import RiskAssessment
from rain_alert.domain.messages import AlertRecord
from rain_alert.domain.values import LEVEL_RANK, Level, is_escalation


@dataclass(frozen=True, slots=True)
class SendDecision:
    """Whether to send, and why — printed by the CLI, logged in change 3."""

    send: bool
    reason: str


def should_send(assessment: RiskAssessment, prior: tuple[AlertRecord, ...]) -> SendDecision:
    """Decide whether `assessment` authorizes a new community send.

    Rules: `level == NONE` never sends; no overlapping prior record sends
    (new window); an overlapping prior record sends only on escalation over
    the highest prior level for that window; equal or lower level does not
    send.
    """
    if assessment.level == Level.NONE:
        return SendDecision(send=False, reason="level none")

    overlapping = [record for record in prior if record.window.overlaps(assessment.window)]
    if not overlapping:
        return SendDecision(send=True, reason="new level for window")

    prior_max_level = max((record.level for record in overlapping), key=lambda level: LEVEL_RANK[level])
    if is_escalation(prior_max_level, assessment.level):
        return SendDecision(send=True, reason=f"escalation from {prior_max_level.value} to {assessment.level.value}")
    return SendDecision(send=False, reason=f"not an escalation over prior level {prior_max_level.value}")
