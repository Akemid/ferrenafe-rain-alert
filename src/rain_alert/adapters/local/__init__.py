"""Local, file- and constant-backed adapters for the offline alert cycle.

These are real, supported implementations that ship in `src/` and that the
CLI depends on — not test doubles. The recording spies used to assert call
order live in `tests/support/` and are a separate category (design.md
section 10).
"""
