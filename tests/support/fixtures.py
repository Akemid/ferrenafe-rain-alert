"""Paths to the pinned offline fixtures.

Centralized so a fixture rename breaks one line instead of every test that
reads it, and so the CLI's `--offline-fixtures` tests and the adapter tests
cannot drift onto different files.
"""

from __future__ import annotations

from pathlib import Path

FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures"

SENAMHI_FIXTURES = FIXTURES_ROOT / "senamhi"
SENAMHI_ACTIVE_WARNING = SENAMHI_FIXTURES / "warnings_table.html"
SENAMHI_PRECIPITATION_COAST = SENAMHI_FIXTURES / "precipitation_coast_current.html"
SENAMHI_HISTORY_ONLY = SENAMHI_FIXTURES / "history_only.html"
SENAMHI_EMPTY_TABLE = SENAMHI_FIXTURES / "empty_table.html"
SENAMHI_BROKEN_STRUCTURE = SENAMHI_FIXTURES / "broken_structure.html"

OPEN_METEO_PAYLOAD = FIXTURES_ROOT / "open_meteo" / "forecast_72h.json"
