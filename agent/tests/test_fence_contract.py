"""The agent's side of `contracts/composer-fence.json` (design.md D51).

The application writes the fence markers into the user turn; the system prompt
(this unit) names them generically. Both read the same JSON by relative path:
this unit must not import `rain_alert`, so the contract file is the only thing
the two sides share. The application's half is
`tests/unit/adapters/test_composer_fence_contract.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app import PROMPT_KEY

CONTRACT_PATH = Path(__file__).resolve().parents[2] / "contracts" / "composer-fence.json"


def _contract() -> dict[str, object]:
    loaded: dict[str, object] = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    return loaded


def test_the_payload_key_matches_the_contract() -> None:
    assert _contract()["prompt_key"] == PROMPT_KEY


@pytest.mark.xfail(
    strict=True, reason="SYSTEM_PROMPT lands in PR 3 (agent-system-prompt tasks 3.2); 3.4 removes this marker"
)
def test_the_system_prompt_names_the_contract_markers() -> None:
    from prompts import SYSTEM_PROMPT  # type: ignore[import-not-found]

    contract = _contract()
    placeholder = str(contract["prompt_placeholder"])
    assert str(contract["open_marker"]).format(token=placeholder) in SYSTEM_PROMPT
    assert str(contract["close_marker"]).format(token=placeholder) in SYSTEM_PROMPT
