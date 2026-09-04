"""Architecture boundary test (repo-hygiene spec: "Domain package stays free
of adapter/AWS/LLM imports"; design.md section 10).

Static AST inspection — not import-and-introspect — so it catches imports
nested inside function bodies and `if TYPE_CHECKING:` blocks, and it never
executes module side effects.
"""

import ast
from collections.abc import Callable
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "rain_alert"

# Extended by change 2 and change 3 in one line each (design.md section 10).
DOMAIN_FORBIDDEN_ROOTS = {
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

PORTS_ALLOWED_ROOTS = ("typing", "collections.abc", "rain_alert.domain")


def _imported_roots(tree: ast.AST) -> set[str]:
    """Every dotted import root in a module, wherever it appears."""
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module)
    return roots


def _violations(package_dir: Path, is_forbidden: Callable[[str], bool]) -> dict[str, set[str]]:
    """{relative_file: forbidden_roots_found} for every .py file under package_dir."""
    found: dict[str, set[str]] = {}
    for path in sorted(package_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        bad = {root for root in _imported_roots(tree) if is_forbidden(root)}
        if bad:
            found[str(path.relative_to(package_dir))] = bad
    return found


def _domain_import_is_forbidden(root: str) -> bool:
    if root.split(".")[0] in {forbidden.split(".")[0] for forbidden in DOMAIN_FORBIDDEN_ROOTS}:
        return True
    return root.startswith(DOMAIN_FORBIDDEN_PREFIXES)


def _ports_import_is_forbidden(root: str) -> bool:
    return not any(root == allowed or root.startswith(f"{allowed}.") for allowed in PORTS_ALLOWED_ROOTS)


def test_domain_package_has_no_forbidden_imports() -> None:
    assert _violations(SRC_ROOT / "domain", _domain_import_is_forbidden) == {}


def test_ports_package_imports_only_typing_and_domain() -> None:
    assert _violations(SRC_ROOT / "ports", _ports_import_is_forbidden) == {}


def test_scanner_flags_a_forbidden_import_hidden_inside_a_function(tmp_path: Path) -> None:
    """Triangulation: proves the scanner actually detects a violation
    (including one nested inside a function body), not just that an empty
    package trivially passes."""
    fake_domain = tmp_path / "domain"
    fake_domain.mkdir()
    (fake_domain / "leaky.py").write_text("def do_it():\n    import httpx\n    return httpx\n", encoding="utf-8")
    (fake_domain / "clean.py").write_text("x = 1\n", encoding="utf-8")

    result = _violations(fake_domain, _domain_import_is_forbidden)

    assert result == {"leaky.py": {"httpx"}}
