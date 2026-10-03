"""Architecture boundary test (repo-hygiene spec: "Domain package stays free
of adapter/AWS/LLM imports"; design.md section 10).

Static AST inspection — not import-and-introspect — so it catches imports
nested inside function bodies and `if TYPE_CHECKING:` blocks, and it never
executes module side effects.
"""

import ast
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "rain_alert"
AGENT_ROOT = REPO_ROOT / "agent"
ENTRYPOINTS_ROOT = SRC_ROOT / "entrypoints"

#: The scheduled path's own boundary (design.md D29): a cloud invocation that
#: reached the CLI's argument parsing or either of the CLI-only local
#: adapters would inherit `JsonFileAlertRepository` on `/tmp` or
#: `StaticConfigRepository`'s env-var defaults — dedup and configuration
#: would go silently inert in production, with nothing failing, logging, or
#: looking wrong. Stated as full dotted module paths, not bare roots, so this
#: scan does not also forbid `entrypoints.wiring` or `entrypoints.run_once`,
#: which `lambda_handler.py` legitimately imports.
LAMBDA_HANDLER_FORBIDDEN_MODULES = {
    "rain_alert.entrypoints.cli",
    "rain_alert.adapters.local.json_alert_repository",
    "rain_alert.adapters.local.static_config_repository",
}

#: The deployment boundary, in the direction that protects the Lambda: nothing
#: the alert path ships may reach the agent unit or a model SDK (D22). Stated
#: at `src/` rather than at `domain/`, which is what
#: `state.yaml → resolved_decisions.pydantic-boundary` actually requires.
SRC_FORBIDDEN_ROOTS = {"agent", "pydantic", "strands", "bedrock_agentcore"}

#: The same boundary in the direction that protects the deploy. See the test
#: class for why this one is not inferable from the first.
AGENT_FORBIDDEN_ROOTS = {"rain_alert", "src"}

# Extended by change 2 and change 3 in one line each (design.md section 10).
DOMAIN_FORBIDDEN_ROOTS = {
    "pydantic",
    "httpx",
    "requests",
    "urllib3",
    "urllib.request",
    "boto3",
    "botocore",
    "bs4",
    "soupsieve",
    "lxml",
    "selectolax",
    "strands",
    "bedrock_agentcore",
    "anthropic",
    "openai",
    "langchain",
}
DOMAIN_FORBIDDEN_PREFIXES = ("rain_alert.adapters", "rain_alert.entrypoints", "rain_alert.application")

# "datetime" added in change 2: every port signature that carries a `now` or
# a timestamp needs it (design.md section 4). design.md section 10 describes
# this list as {typing, collections.abc, rain_alert.domain}; datetime is a
# stdlib type carrying no I/O or third-party surface, so it is added here
# rather than reopened as a design question.
PORTS_ALLOWED_ROOTS = ("typing", "collections.abc", "datetime", "rain_alert.domain")


def _module_package(package: str, relative_path: Path) -> str:
    """Absolute package that owns `relative_path` (a .py file under the scanned package)."""
    parts = list(relative_path.parent.parts)
    return ".".join([package, *parts]) if parts else package


def _resolve_relative(node: ast.ImportFrom, module_package: str) -> set[str]:
    """Absolute dotted names for `from .x import y` / `from .. import z`."""
    base_parts = module_package.split(".")
    if node.level > 1:
        base_parts = base_parts[: -(node.level - 1)] or base_parts[:1]
    base = ".".join(base_parts)
    if node.module:
        return {f"{base}.{node.module}"}
    return {f"{base}.{alias.name}" for alias in node.names}


def _imported_roots(tree: ast.AST, module_package: str) -> set[str]:
    """Every dotted import root in a module, wherever it appears, relative imports resolved."""
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                roots.update(_resolve_relative(node, module_package))
            elif node.module:
                roots.add(node.module)
    return roots


def _violations(package_dir: Path, is_forbidden: Callable[[str], bool], *, package: str) -> dict[str, set[str]]:
    """{relative_file: forbidden_roots_found} for every .py file under package_dir.

    Dot-directories are skipped. `agent/` carries its own `.venv`, and every
    dependency installed in it imports something one of these scans forbids —
    without the skip the boundary tests would report the SDK rather than
    anything a person in this repository wrote.
    """
    found: dict[str, set[str]] = {}
    for path in sorted(package_dir.rglob("*.py")):
        relative = path.relative_to(package_dir)
        if any(part.startswith(".") for part in relative.parts):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        roots = _imported_roots(tree, _module_package(package, relative))
        bad = {root for root in roots if is_forbidden(root)}
        if bad:
            found[str(relative)] = bad
    return found


def _domain_import_is_forbidden(root: str) -> bool:
    if root.split(".")[0] in {forbidden.split(".")[0] for forbidden in DOMAIN_FORBIDDEN_ROOTS}:
        return True
    return root.startswith(DOMAIN_FORBIDDEN_PREFIXES)


def _src_import_is_forbidden(root: str) -> bool:
    return root.split(".")[0] in SRC_FORBIDDEN_ROOTS


def _agent_import_is_forbidden(root: str) -> bool:
    return root.split(".")[0] in AGENT_FORBIDDEN_ROOTS


def _ports_import_is_forbidden(root: str) -> bool:
    return not any(root == allowed or root.startswith(f"{allowed}.") for allowed in PORTS_ALLOWED_ROOTS)


def test_domain_package_has_no_forbidden_imports() -> None:
    assert _violations(SRC_ROOT / "domain", _domain_import_is_forbidden, package="rain_alert.domain") == {}


def test_ports_package_imports_only_typing_and_domain() -> None:
    assert _violations(SRC_ROOT / "ports", _ports_import_is_forbidden, package="rain_alert.ports") == {}


def test_scanner_flags_a_forbidden_import_hidden_inside_a_function(tmp_path: Path) -> None:
    """Triangulation: proves the scanner actually detects a violation
    (including one nested inside a function body), not just that an empty
    package trivially passes."""
    fake_domain = tmp_path / "domain"
    fake_domain.mkdir()
    (fake_domain / "leaky.py").write_text("def do_it():\n    import httpx\n    return httpx\n", encoding="utf-8")
    (fake_domain / "clean.py").write_text("x = 1\n", encoding="utf-8")

    result = _violations(fake_domain, _domain_import_is_forbidden, package="rain_alert.domain")

    assert result == {"leaky.py": {"httpx"}}


class TestTheAgentDeploymentUnitIsASeparateArtifact:
    """D22, enforced in both directions.

    **Why the second direction needs a test at all**, since it is the part a
    reader cannot infer: an `agent/` module importing `rain_alert.domain`
    **works on a developer's machine**, because both trees sit on `sys.path`
    during local development. It fails only after deploy, inside AgentCore
    Runtime, where the `rain_alert` package does not exist. That is the most
    expensive place in this project to discover an import.

    The first direction protects the Lambda change 3 ships: `pydantic`,
    `strands` and `bedrock_agentcore` are deployment weight the alert path
    must never carry, and `state.yaml → resolved_decisions.pydantic-boundary`
    puts the line at `src/` rather than at `domain/`.
    """

    def test_the_application_never_imports_the_agent_unit_or_a_model_sdk(self) -> None:
        assert _violations(SRC_ROOT, _src_import_is_forbidden, package="rain_alert") == {}

    def test_the_agent_unit_never_imports_the_application_package(self) -> None:
        assert _violations(AGENT_ROOT, _agent_import_is_forbidden, package="agent") == {}

    def test_both_scans_actually_reach_a_file(self) -> None:
        """A scan over a directory that does not exist reports no violation and
        reads as a pass. Mutation-checked by pointing `AGENT_ROOT` at a name
        that is not there: without this, nothing turned red."""
        assert sorted(path.name for path in AGENT_ROOT.glob("*.py")) == ["app.py", "models.py", "prompts.py"]
        assert (SRC_ROOT / "adapters" / "agent_prompt.py").is_file()

    def test_the_application_side_scanner_detects_a_planted_import(self, tmp_path: Path) -> None:
        """Triangulation. A clean tree passing proves nothing about a scanner
        that was never asked a question it could answer wrongly."""
        (tmp_path / "composer.py").write_text(
            "from strands import Agent\nimport pydantic\nfrom agent.models import CompositionOutput\n",
            encoding="utf-8",
        )
        (tmp_path / "clean.py").write_text("from rain_alert.domain import template\n", encoding="utf-8")

        result = _violations(tmp_path, _src_import_is_forbidden, package="rain_alert")

        assert result == {"composer.py": {"strands", "pydantic", "agent.models"}}

    def test_the_agent_side_scanner_detects_a_planted_import(self, tmp_path: Path) -> None:
        (tmp_path / "leaky.py").write_text(
            "def build():\n    from rain_alert.domain.sanitize import sanitize_source_text\n", encoding="utf-8"
        )
        (tmp_path / "clean.py").write_text("from models import CompositionOutput\n", encoding="utf-8")

        result = _violations(tmp_path, _agent_import_is_forbidden, package="agent")

        assert result == {"leaky.py": {"rain_alert.domain.sanitize"}}

    def test_the_scan_ignores_the_virtual_environment_it_would_otherwise_walk(self, tmp_path: Path) -> None:
        """`agent/` carries its own `.venv`, and every dependency in it imports
        something this scan forbids. Skipping dot-directories is what makes the
        scan about *this* project's code; without it the test fails on the SDK
        rather than on anything anyone wrote."""
        (tmp_path / ".venv").mkdir()
        (tmp_path / ".venv" / "installed.py").write_text("import rain_alert\n", encoding="utf-8")
        (tmp_path / "clean.py").write_text("x = 1\n", encoding="utf-8")

        assert _violations(tmp_path, _agent_import_is_forbidden, package="agent") == {}


def _forbidden_module_imports(path: Path, forbidden: frozenset[str], *, package: str) -> set[str]:
    """Every import root in one module that exactly names, or is a
    submodule of, one of `forbidden`'s full dotted paths.

    Distinct from `_violations`/`_domain_import_is_forbidden`'s style, which
    matches on a bare top-level root (`"boto3"`, `"strands"`) — this scan is
    about specific *modules* within `rain_alert.entrypoints`/
    `rain_alert.adapters.local`, not a whole third-party package, so it needs
    the full dotted name rather than only the first segment.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module_package = _module_package(package, Path(path.name))
    roots = _imported_roots(tree, module_package)
    return {root for root in roots if any(root == mod or root.startswith(f"{mod}.") for mod in forbidden)}


class TestLambdaHandlerNeverImportsLocalOnlyModules:
    """design.md D29: a scheduled-cycle invocation that reached the CLI's
    argument parsing or either of its two local-only adapters would inherit
    `JsonFileAlertRepository` on `/tmp` or `StaticConfigRepository`'s env-var
    defaults — dedup and configuration would go silently inert in
    production, with nothing failing, logging, or looking wrong."""

    def test_lambda_handler_never_imports_local_only_modules(self) -> None:
        path = ENTRYPOINTS_ROOT / "lambda_handler.py"
        assert path.is_file()

        found = _forbidden_module_imports(path, LAMBDA_HANDLER_FORBIDDEN_MODULES, package="rain_alert.entrypoints")

        assert found == set()

    def test_the_scan_detects_a_planted_violation(self, tmp_path: Path) -> None:
        """Triangulation: a clean file passing proves nothing about a
        scanner that was never asked a question it could answer wrongly."""
        planted = tmp_path / "lambda_handler.py"
        planted.write_text(
            "import rain_alert.entrypoints.cli\n"
            "from rain_alert.adapters.local.json_alert_repository import JsonFileAlertRepository\n"
            "from rain_alert.adapters.local.static_config_repository import StaticConfigRepository\n"
            "from rain_alert.entrypoints.wiring import build_cloud_deps  # allowed, must not be flagged\n",
            encoding="utf-8",
        )

        found = _forbidden_module_imports(planted, LAMBDA_HANDLER_FORBIDDEN_MODULES, package="rain_alert.entrypoints")

        assert found == {
            "rain_alert.entrypoints.cli",
            "rain_alert.adapters.local.json_alert_repository",
            "rain_alert.adapters.local.static_config_repository",
        }


def test_scanner_resolves_relative_imports_before_checking_them(tmp_path: Path) -> None:
    """Triangulation: `from ..adapters import x` and `from .. import adapters`
    inside the domain must resolve to `rain_alert.adapters` and be flagged,
    while intra-package relative imports stay allowed."""
    fake_domain = tmp_path / "domain"
    (fake_domain / "rules").mkdir(parents=True)
    (fake_domain / "sneaky.py").write_text("from ..adapters import thing\n", encoding="utf-8")
    (fake_domain / "rules" / "deeper.py").write_text("from ... import adapters\n", encoding="utf-8")
    (fake_domain / "clean.py").write_text("from .entities import Warning\nfrom . import rules\n", encoding="utf-8")

    result = _violations(fake_domain, _domain_import_is_forbidden, package="rain_alert.domain")

    assert result == {
        "sneaky.py": {"rain_alert.adapters"},
        "rules/deeper.py": {"rain_alert.adapters"},
    }


class TestRelayAdaptersNeverImportEntrypoints:
    """design.md D44: the relay provider is an adapter. Wiring chooses it; it
    must never reach back into `entrypoints`, or the layering inverts and
    `lambda_handler` could pull in the CLI through the back door."""

    RELAY_ADAPTERS = ("senamhi_relay.py", "s3_relay_reader.py")

    def test_the_relay_adapters_import_nothing_from_entrypoints(self) -> None:
        for name in self.RELAY_ADAPTERS:
            path = SRC_ROOT / "adapters" / name
            assert path.is_file(), f"{name} is not where this scan looks for it"

            found = _forbidden_module_imports(
                path, frozenset({"rain_alert.entrypoints"}), package="rain_alert.adapters"
            )

            assert found == set(), f"{name} imports {sorted(found)}"

    def test_the_scan_detects_a_planted_violation(self, tmp_path: Path) -> None:
        planted = tmp_path / "senamhi_relay.py"
        planted.write_text(
            "from rain_alert.entrypoints.wiring import build_cloud_deps\n"
            "import rain_alert.entrypoints\n"
            "from rain_alert.domain.sources import Available  # allowed, must not be flagged\n",
            encoding="utf-8",
        )

        found = _forbidden_module_imports(planted, frozenset({"rain_alert.entrypoints"}), package="rain_alert.adapters")

        assert found == {"rain_alert.entrypoints.wiring", "rain_alert.entrypoints"}
