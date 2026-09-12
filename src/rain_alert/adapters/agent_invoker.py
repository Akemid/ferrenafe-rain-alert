"""The agent invocation seam (design.md D18).

This module deliberately contains no client. It is the shape change 1 already
chose twice for `HtmlFetcher` / `FetchError`:

1. **A `Protocol`, so the offline suite needs no model, no network and no
   credential.** `tests/support/fakes.FakeAgentInvoker` satisfies it
   structurally; the real `BedrockAgentCoreInvoker` will satisfy it the same
   way, from its own module, so nothing here imports boto3.
2. **One translated exception.** No SDK exception type is allowed to escape
   the real invoker: every failure arrives at the composer as
   `AgentInvocationError` carrying a `UnavailableReason` the domain already
   knows. The composer therefore never learns a vendor's exception hierarchy,
   which is the same reason `adapters/http.py` states for `FetchError`.

The four transport faults the fallback table needs — `TIMEOUT`,
`TRANSPORT_ERROR`, `BAD_STATUS`, `MALFORMED_PAYLOAD` — are already
`UnavailableReason` members, so this change introduces no new enum.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from rain_alert.domain.values import UnavailableReason


class AgentInvocationError(Exception):
    """An agent invocation that failed, tagged with the reason to report.

    Deliberately **not** a `FetchError` subclass. The composer catches this
    and nothing else, so an HTML fetch error arriving from somewhere else
    cannot be mistaken for the agent being unreachable — and a new SDK error
    type can never slip past the invoker untranslated.
    """

    def __init__(self, reason: UnavailableReason, detail: str) -> None:
        super().__init__(f"{reason.value}: {detail}")
        self.reason = reason
        self.detail = detail


class AgentInvoker(Protocol):
    """One call to the composition agent, returning its decoded output.

    The return value is a decoded JSON object rather than a string: decoding
    belongs to the implementation that knows the wire format, so a body that
    is not JSON becomes `AgentInvocationError(MALFORMED_PAYLOAD, ...)` at the
    boundary instead of a parse attempt inside the composer.

    Raises:
        AgentInvocationError: on any failure whatsoever. An implementation
            that lets a vendor exception escape is a defect, and the composer
            absorbs one anyway (D15) so that defect degrades the cycle rather
            than aborting it.
    """

    def invoke(self, prompt: str) -> Mapping[str, Any]: ...
