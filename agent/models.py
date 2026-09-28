"""The structured output this unit returns (design.md 7.4, D22).

**This model is a duplicate, and the duplication is the design.** The same
contract exists on the application side as a frozen dataclass read by
`adapters/agent_composer.py::parse_candidate`. It cannot be shared as code:
`agent/` is deployed to AgentCore Runtime, where the `rain_alert` package does
not exist, and an import across that line works on a developer's machine and
fails only after deploy — the most expensive place in this project to discover
one. What guards the duplication today is the architecture test that refuses
the import in both directions — and nothing else. A contract file asserted
from both suites is planned, and writing it means deciding which fields it
pins and where each side asserts it, which belongs to the composer change
rather than here. Until then the two copies can drift silently, and this
paragraph used to claim otherwise:
`docs/blog/2026-09-21-three-docstrings-cited-a-file-that-was-never-written.md`.

**What this model deliberately does not do.** It carries no length cap, no
city rule, no header rule and no rule about numbers. Every one of those lives
in `domain/message_validation.py`, on the application's side of the wire,
because that is the side that has the `MessageRequest` to check a draft
*against* — and a second, weaker copy here would be a rule two places could
disagree about. This model refuses only what the reader on the other side
cannot read at all: a missing field, a level outside the enum, a `valid_until`
that is not an instant, and an unrequested field.

`level` is spelled out as a literal rather than imported from the domain's
`Level`, for the same deploy reason. The three members are a persisted
contract; `domain/values.py` says so.
"""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class CompositionOutput(BaseModel):
    """One composed community message, as the agent returns it.

    The field descriptions are not documentation for a human. They are sent to
    the model as part of the JSON schema `structured_output` builds, so they
    are the last thing it reads before it writes.
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(description="One short line naming the city and the risk level. No line breaks.")
    body: str = Field(description="The community message, in neutral Spanish, as plain text lines.")
    level: Literal["none", "prepare", "imminent"] = Field(
        description="Echo the level given in the data, unchanged. You do not decide it."
    )
    valid_until: AwareDatetime = Field(
        description="Echo the window end given in the data, unchanged, as an ISO 8601 instant with an offset."
    )
