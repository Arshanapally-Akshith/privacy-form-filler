"""Enforces Invariant P1 / I1 (ARCHITECTURE.md §3): the privacy policy engine is applied
only at the LLM-call boundary, never before retrieval, parsing, or storage. `app.privacy`
must therefore never be reachable from `app.ingest` or `app.retrieval`, in either direction.

Written before any content exists in those packages, per BUILD.md Phase 1: "must exist
before there is anything to violate it."
"""

import ast
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent / "app"

# Packages that must never import app.privacy, directly or transitively.
BOUNDARY_PACKAGES = ["ingest", "retrieval"]


def _imported_modules(python_file: Path) -> set[str]:
    tree = ast.parse(python_file.read_text(encoding="utf-8"), filename=str(python_file))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _package_import_graph(package_root: Path) -> dict[str, set[str]]:
    """Maps each `app.*` module in the tree to the modules it imports directly."""
    graph: dict[str, set[str]] = {}
    for python_file in package_root.rglob("*.py"):
        relative = python_file.relative_to(APP_ROOT.parent)
        module_name = ".".join(relative.with_suffix("").parts)
        graph[module_name] = _imported_modules(python_file)
    return graph


def _reaches_privacy(module: str, graph: dict[str, set[str]], visited: set[str]) -> bool:
    if module in visited:
        return False
    visited.add(module)
    for imported in graph.get(module, set()):
        if imported == "app.privacy" or imported.startswith("app.privacy."):
            return True
        if imported.startswith("app.") and _reaches_privacy(imported, graph, visited):
            return True
    return False


def test_privacy_not_reachable_from_boundary_packages() -> None:
    for package in BOUNDARY_PACKAGES:
        package_root = APP_ROOT / package
        graph = _package_import_graph(package_root)
        for module in graph:
            assert not _reaches_privacy(module, graph, set()), (
                f"{module} reaches app.privacy — violates Invariant P1 (ARCHITECTURE.md §3): "
                "the policy engine must apply only at the LLM-call boundary."
            )
