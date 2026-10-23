"""The real Strands event loop, with a scripted model.

`test_app.py` fakes the whole agent. This file keeps the real `Agent`, the real
tool and the real structured-output machinery, and fakes only the model: a
hand-written `Model` that replays two turns, first asking for the tool and then
returning the structured output. It is what proves the call shape in design D5 —
`agent(prompt, structured_output_model=...)` runs tools before the structured
output, where the legacy `Agent.structured_output()` would not.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from app import build_request_agent as real_build_request_agent
from app import compose
from history import AlertRow, HistoryBinding
from models import CompositionOutput
from strands.models import Model

OUTPUT = {
    "title": "Alerta de lluvias — Ferreñafe — 3 días consecutivos",
    "body": "Ciudad: Ferreñafe\n3 días consecutivos de alerta.",
    "level": "prepare",
    "valid_until": "2026-10-13T12:00:00Z",
}


def _tool_use_turn(name: str, tool_input: dict[str, Any], tool_use_id: str) -> list[dict[str, Any]]:
    return [
        {"messageStart": {"role": "assistant"}},
        {"contentBlockStart": {"start": {"toolUse": {"toolUseId": tool_use_id, "name": name}}}},
        {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(tool_input)}}}},
        {"contentBlockStop": {}},
        {"messageStop": {"stopReason": "tool_use"}},
        {"metadata": {"usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2}, "metrics": {"latencyMs": 1}}},
    ]


class ScriptedModel(Model):
    """Replays scripted turns and records the messages and tools of each call."""

    def __init__(self, turns: list[list[dict[str, Any]]]) -> None:
        self._turns = iter(turns)
        self.messages_seen: list[list[Any]] = []
        self.tool_names_seen: list[list[str]] = []

    def update_config(self, **model_config: Any) -> None:
        return None

    def get_config(self) -> Any:
        return {}

    def structured_output(self, output_model: Any, prompt: Any, system_prompt: Any = None, **kwargs: Any) -> Any:
        raise AssertionError("the legacy structured_output entry point must not be used")

    async def stream(self, messages: Any, tool_specs: Any = None, system_prompt: Any = None, **kwargs: Any) -> Any:
        self.messages_seen.append(json.loads(json.dumps(messages, default=str)))
        self.tool_names_seen.append([spec["name"] for spec in tool_specs or []])
        for event in next(self._turns):
            yield event


class CountingSource:
    def __init__(self, rows: list[AlertRow]) -> None:
        self.rows = rows
        self.reads: list[str] = []

    def read(self, partition: str) -> list[AlertRow]:
        self.reads.append(partition)
        return self.rows


BINDING = HistoryBinding("ferrenafe", datetime(2026, 10, 12, 15, tzinfo=UTC), "prepare", None)
ROWS = [
    AlertRow(datetime(2026, 10, 11, 13, tzinfo=UTC), "prepare", None),
    AlertRow(datetime(2026, 10, 10, 13, tzinfo=UTC), "prepare", None),
]


def _run(model: ScriptedModel, source: CountingSource, binding: HistoryBinding | None = BINDING) -> Any:
    agent = real_build_request_agent(binding, source, model=model)
    return agent("compose", structured_output_model=CompositionOutput)


def test_the_tool_runs_exactly_once_and_the_structured_output_is_returned() -> None:
    model = ScriptedModel(
        [
            _tool_use_turn("get_recent_alerts", {"n": 3}, "t1"),
            _tool_use_turn("CompositionOutput", OUTPUT, "t2"),
        ]
    )
    source = CountingSource(ROWS)

    result = _run(model, source)

    assert source.reads == ["ferrenafe"]
    assert isinstance(result.structured_output, CompositionOutput)
    assert result.structured_output.title == OUTPUT["title"]
    assert "get_recent_alerts" in model.tool_names_seen[0]


def test_the_model_sees_the_computed_facts_in_the_tool_result() -> None:
    model = ScriptedModel(
        [
            _tool_use_turn("get_recent_alerts", {"n": 3}, "t1"),
            _tool_use_turn("CompositionOutput", OUTPUT, "t2"),
        ]
    )

    _run(model, CountingSource(ROWS))

    second_turn = json.dumps(model.messages_seen[1])
    assert "consecutive_days" in second_turn
    assert "11/10/2026 08:00" in second_turn


def test_the_second_invocation_sees_nothing_from_the_first() -> None:
    first = ScriptedModel(
        [
            _tool_use_turn("get_recent_alerts", {"n": 3}, "t1"),
            _tool_use_turn("CompositionOutput", OUTPUT, "t2"),
        ]
    )
    _run(first, CountingSource(ROWS))
    second = ScriptedModel([_tool_use_turn("CompositionOutput", OUTPUT, "t3")])

    _run(second, CountingSource([]), binding=None)

    assert "11/10/2026" not in json.dumps(second.messages_seen)
    assert len(second.messages_seen[0]) == 1


def test_compose_runs_the_tool_loop_end_to_end_through_the_entrypoint_seam() -> None:
    model = ScriptedModel([_tool_use_turn("CompositionOutput", OUTPUT, "t1")])

    result = compose(
        {"prompt": "compose"},
        lambda binding: real_build_request_agent(binding, CountingSource([]), model=model),
    )

    assert result["level"] == "prepare"
    assert set(result) == {"title", "body", "level", "valid_until"}


@pytest.mark.parametrize("n", [100, 0, -3])
def test_a_hostile_n_from_the_model_still_reads_only_the_bound_partition(n: int) -> None:
    model = ScriptedModel(
        [
            _tool_use_turn("get_recent_alerts", {"n": n}, "t1"),
            _tool_use_turn("CompositionOutput", OUTPUT, "t2"),
        ]
    )
    source = CountingSource(ROWS)

    _run(model, source)

    assert source.reads == ["ferrenafe"]
