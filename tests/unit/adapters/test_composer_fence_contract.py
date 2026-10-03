"""`contracts/composer-fence.json` and the app-side fence code must agree (D51).

The fence marker format is written by `agent_prompt.py` (this side) and named,
generically, by the agent's system prompt (the other deployment unit, which
must not import `rain_alert`). A one-character drift between the two makes the
agent's rules refer to a fence the user turn never contains, and nothing else
notices. The agent-side half lives in `agent/tests/test_fence_contract.py` and
reads the same JSON by relative path.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from rain_alert.adapters.agent_prompt import _fence_token, build_prompt
from rain_alert.adapters.agentcore_invoker import PROMPT_PAYLOAD_KEY
from rain_alert.adapters.local.static_config_repository import CHECKLIST
from rain_alert.domain.messages import MessageRequest
from rain_alert.domain.values import Level, TimeWindow

CONTRACT_PATH = Path(__file__).resolve().parents[3] / "contracts" / "composer-fence.json"


def _contract() -> dict[str, object]:
    loaded: dict[str, object] = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    return loaded


def _request() -> MessageRequest:
    return MessageRequest(
        city="Ferreñafe",
        timezone="America/Lima",
        level=Level.PREPARE,
        window=TimeWindow(start=datetime(2026, 9, 3, 12, tzinfo=UTC), end=datetime(2026, 9, 5, 12, tzinfo=UTC)),
        reasons=(),
        forecast=None,
        warning=None,
        senamhi_status="available",
        open_meteo_status="available",
        checklist=CHECKLIST,
    )


def _markers(contract: dict[str, object], token: str) -> tuple[str, str]:
    return (
        str(contract["open_marker"]).format(token=token),
        str(contract["close_marker"]).format(token=token),
    )


def _prompt_contains_the_contract_markers(contract: dict[str, object], prompt: str, token: str) -> bool:
    open_marker, close_marker = _markers(contract, token)
    return open_marker in prompt and close_marker in prompt


def test_the_contract_pins_the_values_the_design_chose() -> None:
    """Guards the file itself: a test comparing two copies of a wrong value passes."""
    assert _contract() == {
        "schema_version": 1,
        "tag": "datos",
        "open_marker": "<datos:{token}>",
        "close_marker": "</datos:{token}>",
        "token_hex_chars": 32,
        "prompt_placeholder": "TOKEN",
        "prompt_key": "prompt",
    }


def test_the_user_turn_carries_the_contract_markers() -> None:
    contract = _contract()

    prompt = build_prompt(_request(), token_factory=lambda: "abc")

    assert _prompt_contains_the_contract_markers(contract, prompt, "abc")


def test_the_fence_token_has_the_contract_length() -> None:
    assert len(_fence_token()) == _contract()["token_hex_chars"]


def test_the_invoker_payload_key_matches_the_contract() -> None:
    assert _contract()["prompt_key"] == PROMPT_PAYLOAD_KEY


def test_a_drifted_tag_is_caught() -> None:
    """Teeth: the comparison must fail when the contract's tag is not the code's."""
    drifted = {
        **_contract(),
        "open_marker": "<data:{token}>",
        "close_marker": "</data:{token}>",
    }

    prompt = build_prompt(_request(), token_factory=lambda: "abc")

    assert not _prompt_contains_the_contract_markers(drifted, prompt, "abc")
