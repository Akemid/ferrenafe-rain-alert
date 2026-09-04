"""`AlertPolicy` — the port-bound shell over the pure `should_send` rule
(design.md section 6, alert-dedup spec "Query before decision").

Placement resolved at apply time (tasks.md "Open Items for Apply"): the
design's `# domain/dedup.py — pure` code block header sits directly above
this class, but `AlertPolicy` takes an `AlertRepository` — a port. Importing
a port from `domain/` (even under `TYPE_CHECKING`) fails the architecture
boundary test, so the pure rule stays in `domain/dedup.py` and this shell,
which only fetches state and delegates, lives in `application/`.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from rain_alert.domain.dedup import SendDecision, should_send
from rain_alert.domain.entities import RiskAssessment
from rain_alert.ports import AlertRepository


class AlertPolicy:
    """Reads dedup state from `AlertRepository`, holds none of its own."""

    def __init__(self, alerts: AlertRepository, lookback_hours: int) -> None:
        self._alerts = alerts
        self._lookback_hours = lookback_hours

    def decide(self, city_slug: str, assessment: RiskAssessment, now: datetime) -> SendDecision:
        """Query prior sent alerts in the lookback window, then delegate to
        the pure rule.

        The range start is clamped to the assessment window's own start, not
        just `now - lookback`. An imminent window is the union of the current
        SENAMHI warning windows and can begin well before the lookback, so a
        long-duration aviso would otherwise hide the alert already sent for it
        and the community would be re-alerted every cycle. Clamping only ever
        widens the range, so the lookback still governs short windows.
        """
        lookback_start = now - timedelta(hours=self._lookback_hours)
        earliest_start = min(lookback_start, assessment.window.start)
        prior = self._alerts.alerts_with_window_start_between(city_slug, earliest_start, assessment.window.end)
        return should_send(assessment, prior)
