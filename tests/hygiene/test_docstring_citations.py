r"""Every repository path cited inside backticks either exists in git, or it
is a lie.

`docs/blog/2026-09-21-three-docstrings-cited-a-file-that-was-never-written.md`
proposed this check and stopped short of writing it. Then the branch that
carried that blog entry added seven more phantom citations. Three occurrences
of the same defect is where a habit stops being enough.

**Why prose needs a test at all.** A wrong test fails. A wrong annotation gets
flagged. A sentence asserting that a guard exists is inert: nothing in
`pytest`, `ruff` or `mypy` has an opinion about whether a path named in a
comment is a real path. The reader spends their suspicion on the citation and
gets nothing back.

**Two failures, not one.** A citation of a path that does not exist is the
obvious one. A citation of a path that exists only on the author's machine —
gitignored, or simply never added — is the same failure wearing a disguise:
anyone who clones the repository sees exactly what git has, so an uncommitted
path is as unreadable to them as a missing one. That second shape is what just
happened six times. Git's index is therefore the authority here, not the
filesystem.

**Deliberately narrow.** A hygiene check that cries wolf gets deleted, so the
false-positive rate matters more than the catch rate. Everything this check
declines to look at is listed on `_cited_path`, and each exclusion is there
because a real citation in this repository would otherwise have been called a
lie.
"""

from __future__ import annotations

import ast
import io
import re
import subprocess
import tokenize
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Anything between two backticks on one line. Prose citations in this project
#: are written that way throughout, and a backtick span never wraps a line.
BACKTICKED = re.compile(r"`([^`\n]+)`")

#: The extensions a cited path must end in to be treated as a file. A closed
#: list rather than "anything with a dot": `domain/hazards.may_raise_imminent`
#: and `application/json` both look like paths under an open rule, and neither
#: is one.
SOURCE_EXTENSIONS = frozenset(
    {
        ".astro",
        ".cfg",
        ".css",
        ".drawio",
        ".html",
        ".ini",
        ".js",
        ".json",
        ".lock",
        ".md",
        ".mjs",
        ".py",
        ".sh",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".yaml",
        ".yml",
    }
)

#: Characters that say "this backtick span is not a bare path". Whitespace
#: rules out shell commands and prose; brackets, quotes and operators rule out
#: code expressions; `*` and `?` rule out globs. `:` is here too, which is why
#: the `path::symbol` form is split off before this runs.
NOT_IN_A_PATH = re.compile(r"""[\s()\[\]{}<>=,;:"'$|!\\&^%#@~+*?]""")

#: The `path.py::symbol` form this project uses everywhere. Only the path half
#: is a path.
SYMBOL_SEPARATOR = "::"

#: A line that is entirely comment, in the C-family syntax the `web/` and
#: `infra/` sources use. Matching whole lines rather than parsing the language
#: is the conservative choice: a `//` inside a string literal is never at the
#: start of a line, and a citation this misses is a citation left unchecked
#: rather than a false accusation.
C_FAMILY_COMMENT_LINE = re.compile(r"^\s*(//|/\*|\*)")

PYTHON_SUFFIX = ".py"
C_FAMILY_SUFFIXES = (".ts", ".tsx", ".astro")


@dataclass(frozen=True)
class Citation:
    """One backticked path, and where it was written."""

    source: str
    line: int
    token: str

    def __str__(self) -> str:
        return f"{self.source}:{self.line}: `{self.token}`"


class RepositoryIndex:
    """What paths this repository has, as git sees them.

    Built from `git ls-files` rather than a filesystem walk, because the
    question is what someone who clones the repository can open. A file
    sitting in the working tree and not in the index answers "nothing".
    """

    def __init__(self, root: Path, tracked: Sequence[str]) -> None:
        self._root = root
        files = {path for path in tracked if path}
        directories = {str(parent) for path in files for parent in Path(path).parents if str(parent) != "."}
        #: Every path-segment-aligned suffix of every tracked path, so the
        #: relative citations this project prefers (`adapters/http.py` for
        #: `src/rain_alert/adapters/http.py`) resolve without the writer
        #: having to spell the whole tree.
        self._suffixes = {suffix for path in files | directories for suffix in _suffixes(path)}
        self._segments = {segment for path in files | directories for segment in path.split("/")}
        self._top_level = {entry.name for entry in root.iterdir()}

    def resolves(self, token: str) -> bool:
        return token in self._suffixes

    def is_ours_to_check(self, token: str) -> bool:
        """Whether the first segment names something this repository has.

        A path whose root segment this repository has never heard of belongs
        to somebody else's tree — `bedrock_agentcore/runtime/app.py` cites the
        AgentCore SDK's own source, which is a real and useful citation and is
        not this repository's to verify. The filesystem is consulted for the
        first segment alone, so a directory that exists but is gitignored
        still brings its contents under the check.
        """
        first = token.split("/", 1)[0]
        return first in self._top_level or first in self._segments

    def is_present_but_uncommitted(self, token: str) -> bool:
        """Whether the path is on this machine and not in git.

        Only the failure *message* depends on this; both shapes fail.
        """
        return (self._root / token).exists()


def _suffixes(path: str) -> Iterator[str]:
    segments = path.split("/")
    for index in range(len(segments)):
        yield "/".join(segments[index:])


def _cited_path(span: str) -> str | None:
    """The repository path this backtick span cites, or `None`.

    `None` is the answer for everything the check deliberately declines to
    judge, and the list is the whole false-positive argument:

    - **Anything without a `/`.** `senamhi.html` in `entrypoints/wiring.py` is
      the name a caller-supplied fixture directory must contain, not a path in
      this repository; `package.json` is four different files. A bare filename
      cannot be resolved without guessing, so it is not checked. This is the
      largest thing given up, and it is given up on purpose.
    - **Anything whose last segment has no known source extension.**
      `application/json` is a media type. `domain/hazards.may_raise_imminent`
      is a symbol reference written with a dot. A trailing `/` is the one way
      to cite a directory and be checked.
    - **Anything carrying whitespace, brackets, quotes, operators, `*` or
      `?`.** Shell commands, code expressions, glob patterns and prose all
      land here.
    - **Anything absolute**, which in a docstring is a machine's path, not the
      repository's.
    - **Anything whose first segment this repository does not have**, which is
      another project's tree. See `RepositoryIndex.is_ours_to_check`.
    """
    token = span.split(SYMBOL_SEPARATOR, 1)[0]
    if not token or NOT_IN_A_PATH.search(token):
        return None
    if "/" not in token or token.startswith("/"):
        return None
    if not token.endswith("/") and Path(token).suffix not in SOURCE_EXTENSIONS:
        return None
    return token.rstrip("/")


def _python_prose(text: str) -> Iterator[tuple[int, str]]:
    """Every docstring and every comment in one Python module.

    Docstrings come from `ast`, comments from `tokenize`. Other string
    literals are left alone: an error message or a test fixture is not a
    claim about the repository's shape, and reading them would add
    false positives for nothing.
    """
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            docstring = ast.get_docstring(node, clean=False)
            if docstring:
                yield getattr(node, "lineno", 1), docstring
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type == tokenize.COMMENT:
            yield token.start[0], token.string


def _c_family_prose(text: str) -> Iterator[tuple[int, str]]:
    for number, line in enumerate(text.splitlines(), start=1):
        if C_FAMILY_COMMENT_LINE.match(line):
            yield number, line


def _prose_of(path: str, text: str) -> Iterator[tuple[int, str]]:
    if path.endswith(PYTHON_SUFFIX):
        yield from _python_prose(text)
    elif path.endswith(C_FAMILY_SUFFIXES):
        yield from _c_family_prose(text)


def _tracked(repo_root: Path) -> list[str]:
    result = subprocess.run(["git", "ls-files"], cwd=repo_root, capture_output=True, text=True, check=True)
    return result.stdout.split("\n")


def _citations(repo_root: Path, tracked: Sequence[str]) -> Iterator[Citation]:
    for path in tracked:
        if not path.endswith((PYTHON_SUFFIX, *C_FAMILY_SUFFIXES)):
            continue
        text = (repo_root / path).read_text(encoding="utf-8")
        for line, prose in _prose_of(path, text):
            for span in BACKTICKED.finditer(prose):
                token = _cited_path(span.group(1))
                if token is not None:
                    yield Citation(path, line, token)


def phantom_citations(repo_root: Path = REPO_ROOT) -> list[str]:
    """Every cited path git does not have, as `location: reason` lines."""
    tracked = _tracked(repo_root)
    index = RepositoryIndex(repo_root, tracked)
    offenders = set()
    for citation in _citations(repo_root, tracked):
        if not index.is_ours_to_check(citation.token) or index.resolves(citation.token):
            continue
        reason = (
            "exists here but is not committed, so nobody who clones can open it"
            if index.is_present_but_uncommitted(citation.token)
            else "does not exist"
        )
        offenders.add(f"{citation}: {reason}")
    return sorted(offenders)


def test_no_docstring_or_comment_cites_a_path_git_does_not_have() -> None:
    """The check itself. Three files in this repository once described a drift
    guard that had never been written, and nothing failed."""
    offenders = phantom_citations()

    assert offenders == [], "cited paths that are not in this repository:\n" + "\n".join(offenders)


class TestTheCheckDeclinesToJudgeWhatItCannotJudge:
    """The false-positive argument, pinned. Every span below is a real one
    from this repository's own sources, and each would be reported as a lie
    under a rule one notch wider."""

    @pytest.mark.parametrize(
        ("span", "why"),
        [
            ("application/json", "a media type, not a path"),
            ("text/event-stream", "a media type with a hyphen"),
            ("domain/hazards.may_raise_imminent", "a symbol written with a dot"),
            ("entrypoints/wiring.select_composer", "the same shape, one directory deep"),
            ("senamhi.html", "a caller-supplied fixture's required filename"),
            ("uv run pytest", "a shell command"),
            ("openspec/changes/**/spec.md", "a glob pattern"),
            ("https://docs.astro.build", "a URL"),
            ("/tmp/state.json", "an absolute path on somebody's machine"),
            ("dict[str, Any]", "a type expression"),
        ],
    )
    def test_the_span_is_not_treated_as_a_repository_path(self, span: str, why: str) -> None:
        assert _cited_path(span) is None, why

    @pytest.mark.parametrize(
        ("span", "expected"),
        [
            ("contracts/public-snapshot.json", "contracts/public-snapshot.json"),
            ("adapters/http.py", "adapters/http.py"),
            ("tests/support/fakes.py", "tests/support/fakes.py"),
            ("web/src/lib/snapshot.ts", "web/src/lib/snapshot.ts"),
            ("docs/blog/", "docs/blog"),
            ("agentcore_invoker.py::invoke", None),
            ("adapters/agentcore_invoker.py::CONNECT_TIMEOUT", "adapters/agentcore_invoker.py"),
        ],
    )
    def test_a_real_citation_is_read_as_the_path_it_names(self, span: str, expected: str | None) -> None:
        assert _cited_path(span) == expected


def _repository(root: Path, tracked: tuple[str, ...]) -> RepositoryIndex:
    return RepositoryIndex(root, tracked)


class TestTheCheckHasTeeth:
    """Both failure shapes, against a throwaway tree rather than this one, so
    the proof does not depend on this repository staying broken.
    """

    TRACKED = ("src/rain_alert/adapters/http.py", "contracts/public-snapshot.json", "docs/blog/entry.md")

    def test_a_relative_citation_of_a_committed_file_resolves(self, tmp_path: Path) -> None:
        index = _repository(tmp_path, self.TRACKED)

        assert index.resolves("adapters/http.py")

    def test_a_directory_citation_resolves(self, tmp_path: Path) -> None:
        index = _repository(tmp_path, self.TRACKED)

        assert index.resolves("docs/blog")

    def test_a_path_that_was_never_written_does_not_resolve(self, tmp_path: Path) -> None:
        index = _repository(tmp_path, self.TRACKED)

        assert not index.resolves("contracts/agent-composition.json")
        assert index.is_ours_to_check("contracts/agent-composition.json")

    def test_a_path_present_on_this_machine_but_not_committed_does_not_resolve(self, tmp_path: Path) -> None:
        """The disguise: the author can open it, so it reads as correct, and
        it is invisible to every clone. This is the shape that got past review
        six times on one branch."""
        report = tmp_path / ".notes" / "task-8-report.md"
        report.parent.mkdir(parents=True)
        report.write_text("uncommitted\n", encoding="utf-8")
        index = _repository(tmp_path, self.TRACKED)

        assert not index.resolves(".notes/task-8-report.md")
        assert index.is_ours_to_check(".notes/task-8-report.md")
        assert index.is_present_but_uncommitted(".notes/task-8-report.md")

    def test_another_projects_tree_is_left_alone(self, tmp_path: Path) -> None:
        index = _repository(tmp_path, self.TRACKED)

        assert not index.is_ours_to_check("bedrock_agentcore/runtime/app.py")

    def test_a_suffix_that_is_not_segment_aligned_does_not_resolve(self, tmp_path: Path) -> None:
        """`ttp.py` is a suffix of `http.py` as a string and not as a path."""
        index = _repository(tmp_path, self.TRACKED)

        assert not index.resolves("adapters/ttp.py")


class TestTheProseReaderSeesWhatTheDefectLivedIn:
    """The three known phantom citations were one module docstring and two
    `#:` comments. A reader that took only docstrings would have caught one of
    the three.
    """

    def test_a_module_docstring_is_read(self) -> None:
        module = '"""See `contracts/nothing.json`."""\n'

        assert [span for _, prose in _python_prose(module) for span in BACKTICKED.findall(prose)] == [
            "contracts/nothing.json"
        ]

    def test_a_comment_is_read(self) -> None:
        module = "#: pinned by `contracts/nothing.json`\nX = 1\n"

        assert [span for _, prose in _python_prose(module) for span in BACKTICKED.findall(prose)] == [
            "contracts/nothing.json"
        ]

    def test_a_plain_string_literal_is_not_read(self) -> None:
        """An error message is not a claim about the repository's shape."""
        module = 'X = "see `contracts/nothing.json`"\n'

        assert [span for _, prose in _python_prose(module) for span in BACKTICKED.findall(prose)] == []

    def test_a_typescript_comment_is_read(self) -> None:
        source = " * (`contracts/public-snapshot.json`), published by the cycle\n"

        assert [span for _, prose in _c_family_prose(source) for span in BACKTICKED.findall(prose)] == [
            "contracts/public-snapshot.json"
        ]

    def test_typescript_code_is_not_read(self) -> None:
        source = "const contract = read('`contracts/nothing.json`');\n"

        assert [span for _, prose in _c_family_prose(source) for span in BACKTICKED.findall(prose)] == []
