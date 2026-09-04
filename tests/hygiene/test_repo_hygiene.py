"""Repo hygiene checks (specs/repo-hygiene/spec.md): license, README
disclaimers, and .gitignore excluding local secrets. These guard the
public-repository constraints from design.md section 11.

NOTE: the ".env.example has no values" scenario is intentionally not yet
covered here — see apply-progress.md for why `.env.example` could not be
created in this batch and the follow-up test to add once it exists.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_license_file_contains_apache_2_0_text() -> None:
    text = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "Apache License" in text
    assert "Version 2.0" in text


def test_readme_contains_the_required_disclaimers() -> None:
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "SENAMHI" in text
    assert "INDECI" in text
    assert "without warranty" in text.lower()
    assert "open-meteo" in text.lower()
    assert "non-commercial" in text.lower()


def test_gitignore_excludes_local_secrets_and_state() -> None:
    text = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in (".env", ".local-state/", ".venv/"):
        assert pattern in text
