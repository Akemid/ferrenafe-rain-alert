"""Repo hygiene checks (specs/repo-hygiene/spec.md): license, README
disclaimers, .gitignore excluding local secrets, and a values-free
.env.example. These guard the public-repository constraints from
design.md section 11.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_VAR_NAME = re.compile(r"[A-Z][A-Z0-9_]*")


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


def test_env_example_lists_variable_names_with_no_values() -> None:
    lines = (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    var_lines = [line for line in lines if line and not line.startswith("#")]
    assert var_lines, "expected at least one documented variable"
    for line in var_lines:
        name, _, value = line.partition("=")
        assert ENV_VAR_NAME.fullmatch(name), f"not a variable name: {line}"
        assert value == "", f"{name} must not carry a value"
