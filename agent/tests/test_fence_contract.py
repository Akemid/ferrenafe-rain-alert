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

from app import HISTORY_KEY, PROMPT_KEY
from prompts import SYSTEM_PROMPT

CONTRACT_PATH = Path(__file__).resolve().parents[2] / "contracts" / "composer-fence.json"


def _contract() -> dict[str, object]:
    loaded: dict[str, object] = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    return loaded


def test_the_payload_key_matches_the_contract() -> None:
    assert _contract()["prompt_key"] == PROMPT_KEY


def test_the_system_prompt_names_the_contract_markers() -> None:
    contract = _contract()
    placeholder = str(contract["prompt_placeholder"])
    assert str(contract["open_marker"]).format(token=placeholder) in SYSTEM_PROMPT
    assert str(contract["close_marker"]).format(token=placeholder) in SYSTEM_PROMPT


def test_the_contract_is_schema_version_2_and_names_the_history_key() -> None:
    """Schema 2 adds `history_key`: the payload key under which the application
    binds the continuity context (partition, as_of, current level). It is a
    sibling of `prompt`, never part of the fenced prompt, so scraped text
    cannot reach it."""
    assert _contract()["schema_version"] == 2
    assert _contract()["history_key"] == "history"


def test_the_agent_reads_the_history_under_the_contract_key() -> None:
    assert _contract()["history_key"] == HISTORY_KEY
