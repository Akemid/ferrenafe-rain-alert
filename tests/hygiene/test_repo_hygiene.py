"""Repo hygiene checks (specs/repo-hygiene/spec.md): license, README
disclaimers, .gitignore excluding local secrets, and a values-free
.env.example. These guard the public-repository constraints from
design.md section 11.
"""

import re
import subprocess
from dataclasses import dataclass
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
#: An allow-listed file is forgiven for the *matches* this string produces and
#: for nothing else, so a real number sharing the same line still fails.
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

#: Per pattern, the exact substrings `HOSTILE_TITLE_PLACEHOLDER` produces under
#: that pattern. Forgiveness is granted for these strings and for nothing else,
#: so a pattern the placeholder never triggers — every shape that is not
#: phone-shaped — has an empty entry here and is forgiven nowhere.
FORGIVEN_MATCHES: dict[str, frozenset[str]] = {
    label: frozenset(match.group() for match in re.finditer(pattern, HOSTILE_TITLE_PLACEHOLDER))
    for label, pattern in SECRET_PATTERNS
}


@dataclass(frozen=True)
class Hit:
    """One `git grep` result line: where it was found, and what was found."""

    path: str
    number: str
    text: str

    def __str__(self) -> str:
        return f"{self.path}:{self.number}: {self.text.strip()}"


def _grep(repo_root: Path = REPO_ROOT) -> list[Hit]:
    r"""Every tracked, non-binary line matching any secret pattern.

    `git grep` rather than a filesystem walk: the requirement is about what is
    *committed*, so the file set has to be git's, and `-I` skips binaries.

    One pass with every pattern as its own `-e`, rather than one pass per
    pattern: which pattern matched is decided afterwards in Python, which is
    what makes per-pattern forgiveness possible. `-P` rather than `-E` because
    each expression is applied twice — once by git to find the line, once by
    `re` to find the matches inside it — and only PCRE agrees with `re` on
    `\b` and on lookaround. Under `-E`, `\b[0-9]{12}\b` silently matched
    nothing at all.
    """
    args = ["git", "grep", "-nIP"]
    for _, pattern in SECRET_PATTERNS:
        args += ["-e", pattern]
    result = subprocess.run(args, cwd=repo_root, capture_output=True, text=True, check=False)
    # `git grep` exits 1 when there is no match, which is the healthy case.
    assert result.returncode in (0, 1), result.stderr
    hits = []
    for line in result.stdout.splitlines():
        if not line:
            continue
        path, number, text = line.split(":", 2)
        hits.append(Hit(path, number, text))
    return hits


def _offending_labels(path: str, text: str) -> list[str]:
    """The pattern labels `text` offends, given that it was found at `path`.

    Forgiveness is per match and per pattern. An allow-listed file is excused
    only for a pattern the placeholder itself triggers, and only when *every*
    match that pattern makes on the line is one the placeholder produces.
    Anything else sharing the line still offends, under its own label.
    """
    offended = []
    for label, pattern in SECRET_PATTERNS:
        matches = {match.group() for match in re.finditer(pattern, text)}
        if not matches:
            continue
        if path in PLACEHOLDER_ALLOW_LIST and matches <= FORGIVEN_MATCHES[label]:
            continue
        offended.append(label)
    return offended


def _scan(repo_root: Path = REPO_ROOT) -> list[str]:
    """Every offending `label: location: line` in the repository, sorted."""
    return sorted({f"{label}: {hit}" for hit in _grep(repo_root) for label in _offending_labels(hit.path, hit.text)})


def _sample(*fragments: str) -> str:
    """One secret-shaped sample, assembled from fragments at run time.

    This file is itself scanned by the check below, so a sample written whole
    would be a committed secret shape. Each fragment is split so that no
    pattern matches any fragment where it is written.
    """
    return "".join(fragments)


SAMPLE_AWS_ACCESS_KEY_ID = _sample("AKIA", "IOSFODNN7EXAMPLE")
SAMPLE_MOBILE_GROUPED = _sample("98", "7 654 321")
SAMPLE_MOBILE_INTERNATIONAL = _sample("+51", " ", SAMPLE_MOBILE_GROUPED)


class TestTheAllowListForgivesTheMatchNotTheLine:
    """The allow-list exists for one phone-shaped placeholder, and must forgive
    that match alone. A substring test over the whole matched line, consulted
    identically for every pattern, excuses everything else sharing the line —
    including shapes the allow-list was never granted for.
    """

    PLACEHOLDER_LINE = f'HOSTILE_TITLE = "{HOSTILE_TITLE_PLACEHOLDER}"'
    ALLOW_LISTED = "tests/unit/domain/test_template.py"

    def test_the_documented_placeholder_line_is_forgiven(self) -> None:
        assert _offending_labels(self.ALLOW_LISTED, self.PLACEHOLDER_LINE) == []

    def test_the_same_line_outside_the_allow_list_still_offends(self) -> None:
        assert _offending_labels("src/rain_alert/domain/template.py", self.PLACEHOLDER_LINE) != []

    def test_another_secret_shape_sharing_the_line_is_not_forgiven(self) -> None:
        line = f"{self.PLACEHOLDER_LINE}  # {SAMPLE_AWS_ACCESS_KEY_ID}"
        assert _offending_labels(self.ALLOW_LISTED, line) == ["AWS access key id"]

    def test_a_real_number_sharing_the_line_is_not_forgiven(self) -> None:
        line = f"{self.PLACEHOLDER_LINE}  # {SAMPLE_MOBILE_INTERNATIONAL}"
        assert "Peruvian phone number" in _offending_labels(self.ALLOW_LISTED, line)


def test_forgiveness_is_granted_only_for_the_shape_the_placeholder_has() -> None:
    """The allow-list was granted for a phone-shaped placeholder. Any other
    pattern must be unforgivable everywhere, allow-listed file or not.
    """
    granted = {label for label, matches in FORGIVEN_MATCHES.items() if matches}

    assert granted == {"Peruvian phone number"}


def test_no_secret_or_personal_data_pattern_is_committed() -> None:
    """W3: the highest-stakes hygiene requirement had no automated test.

    It was verified by hand at tasks 1.5 and 3.16 and again during
    verification, but a public repository needs the regression guard, not a
    record that someone once looked. The allow-list is deliberately narrow —
    named files, one placeholder, and only the pattern that placeholder
    triggers — because loosening the pattern instead would disarm the check
    for every future file.
    """
    offenders = _scan()

    assert offenders == [], "committed secrets or personal data:\n" + "\n".join(offenders)


def test_every_allow_listed_placeholder_line_still_exists() -> None:
    """An allow-list that outlives what it forgives silently stops protecting.

    If the attack payload is renamed or moved, this fails and the entry has to
    be revisited rather than left behind as a permanent hole.
    """
    covered = {
        hit.path for hit in _grep() if hit.path in PLACEHOLDER_ALLOW_LIST and HOSTILE_TITLE_PLACEHOLDER in hit.text
    }

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
