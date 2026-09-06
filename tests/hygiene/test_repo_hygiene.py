"""Repo hygiene checks (specs/repo-hygiene/spec.md): license, README
disclaimers, .gitignore excluding local secrets, and a values-free
.env.example. These guard the public-repository constraints from
design.md section 11.
"""

import re
import subprocess
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
    """S6: the substring scan this replaced would pass on a `.gitignore` that
    only mentioned `.env.example`, or only carried the comment "never commit
    .env". The scenario is about what git would track, so it asks git.

    `git check-ignore` answers from the ignore rules alone; the paths need not
    exist, so nothing is created and the local environment file is never read.
    """
    for path in (".env", ".local-state/alerts.json", ".venv/pyvenv.cfg"):
        result = subprocess.run(
            ["git", "check-ignore", "-q", "--", path],
            cwd=REPO_ROOT,
            check=False,
        )
        assert result.returncode == 0, f"{path} is not ignored by .gitignore"


#: The literal placeholder inside the deliberate hostile-title attack payload.
#: An allow-listed file is forgiven only for lines carrying exactly this
#: string, so a real number in the same file still fails the scan.
HOSTILE_TITLE_PLACEHOLDER = "+51 999 000 111"

#: Where that placeholder legitimately appears, and why. The `repo-hygiene`
#: scenario forgives "documented fixture placeholders"; this is that
#: documentation, kept beside the check rather than in prose somewhere else.
PLACEHOLDER_ALLOW_LIST: dict[str, str] = {
    "tests/hygiene/test_repo_hygiene.py": "this check's own declaration of the placeholder it forgives",
    "tests/unit/domain/test_template.py": "the hostile-title attack payload that proves the sanitizer strips it",
    "openspec/changes/core-alert-cycle/apply-progress.md": "the end-to-end reproduction transcript of that attack",
    "openspec/changes/core-alert-cycle/verify-report.md": "the verification quoting the same transcript",
}

#: What must never be committed to a public repository (`repo-hygiene` →
#: "No secrets or personal data ever committed"). Shapes rather than values:
#: the point is to fail before a real one is ever written down.
SECRET_PATTERNS: tuple[tuple[str, str], ...] = (
    ("Peruvian phone number", r"\+51[0-9 ()-]{7,}"),
    ("email address", r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    ("AWS access key id", r"AKIA[0-9A-Z]{16}"),
    ("AWS account id", r"\b[0-9]{12}\b"),
    ("GitHub token", r"gh[pousr]_[A-Za-z0-9]{20,}"),
    ("API secret key", r"\bsk-[A-Za-z0-9]{20,}"),
)


def _grep(pattern: str) -> list[str]:
    """Every tracked, non-binary line matching `pattern`, as `path:line:text`.

    `git grep` rather than a filesystem walk: the requirement is about what is
    *committed*, so the file set has to be git's, and `-I` skips binaries.
    """
    result = subprocess.run(
        ["git", "grep", "-nIE", pattern],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    # `git grep` exits 1 when there is no match, which is the healthy case.
    assert result.returncode in (0, 1), result.stderr
    return [line for line in result.stdout.splitlines() if line]


def _is_allowed(hit: str) -> bool:
    path, _, rest = hit.partition(":")
    return path in PLACEHOLDER_ALLOW_LIST and HOSTILE_TITLE_PLACEHOLDER in rest


def test_no_secret_or_personal_data_pattern_is_committed() -> None:
    """W3: the highest-stakes hygiene requirement had no automated test.

    It was verified by hand at tasks 1.5 and 3.16 and again during
    verification, but a public repository needs the regression guard, not a
    record that someone once looked. The allow-list is deliberately narrow —
    named files plus the exact placeholder string — because loosening the
    pattern instead would disarm the check for every future file.
    """
    offenders = [
        f"{label}: {hit}" for label, pattern in SECRET_PATTERNS for hit in _grep(pattern) if not _is_allowed(hit)
    ]

    assert offenders == [], "committed secrets or personal data:\n" + "\n".join(offenders)


def test_every_allow_listed_placeholder_line_still_exists() -> None:
    """An allow-list that outlives what it forgives silently stops protecting.

    If the attack payload is renamed or moved, this fails and the entry has to
    be revisited rather than left behind as a permanent hole.
    """
    hits = _grep(SECRET_PATTERNS[0][1])
    covered = {hit.partition(":")[0] for hit in hits if _is_allowed(hit)}

    assert covered == set(PLACEHOLDER_ALLOW_LIST), (
        f"allow-list entries with no matching line: {sorted(set(PLACEHOLDER_ALLOW_LIST) - covered)}"
    )


def test_env_example_lists_variable_names_with_no_values() -> None:
    lines = (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    var_lines = [line for line in lines if line and not line.startswith("#")]
    assert var_lines, "expected at least one documented variable"
    for line in var_lines:
        name, _, value = line.partition("=")
        assert ENV_VAR_NAME.fullmatch(name), f"not a variable name: {line}"
        assert value == "", f"{name} must not carry a value"
