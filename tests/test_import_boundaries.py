"""Enforces Invariant P1 / I1 (ARCHITECTURE.md §3): the privacy policy engine is applied
only at the LLM-call boundary, never before retrieval, parsing, or storage. `app.privacy`
must therefore never be reachable from `app.ingest` or `app.retrieval`, in either direction.

Written before any content exists in those packages, per BUILD.md Phase 1: "must exist
before there is anything to violate it."

Also enforces the one-directional dependency CLAUDE.md §6 requires between the new Phase 4
boundary layer and the privacy engine it composes (BUILD.md Phase 4, commit 3):
`app.boundary` may depend on `app.privacy`, never the reverse. Trivially true today since
`app.privacy` is untouched by that commit, but worth asserting now, as a regression the
boundary package could otherwise introduce silently as it grows.

Also enforces Invariant I3 (BUILD.md Phase 4, commit 4): `app.boundary.llm` is the single
call site for the generative LLM. This check is name-level, not module-level, unlike the
two above -- other modules (e.g. `app.extraction.extractor`, `app.api.cases`) legitimately
import `LLMProviderError` from `app.extraction.llm_client` for error handling, and that must
stay allowed. Only importing `generate_structured` itself is what I3 forbids outside the
boundary.
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


def _reaches(module: str, graph: dict[str, set[str]], target_prefix: str, visited: set[str]) -> bool:
    if module in visited:
        return False
    visited.add(module)
    for imported in graph.get(module, set()):
        if imported == target_prefix or imported.startswith(f"{target_prefix}."):
            return True
        if imported.startswith("app.") and _reaches(imported, graph, target_prefix, visited):
            return True
    return False


def test_privacy_not_reachable_from_boundary_packages() -> None:
    for package in BOUNDARY_PACKAGES:
        package_root = APP_ROOT / package
        graph = _package_import_graph(package_root)
        for module in graph:
            assert not _reaches(module, graph, "app.privacy", set()), (
                f"{module} reaches app.privacy — violates Invariant P1 (ARCHITECTURE.md §3): "
                "the policy engine must apply only at the LLM-call boundary."
            )


def test_boundary_not_reachable_from_privacy() -> None:
    graph = _package_import_graph(APP_ROOT / "privacy")
    for module in graph:
        assert not _reaches(module, graph, "app.boundary", set()), (
            f"{module} reaches app.boundary — app.privacy must depend on nothing in app/ "
            "(CLAUDE.md §6); the boundary layer composes app.privacy, never the reverse."
        )


def _imports_generate_structured_by_name(python_file: Path) -> bool:
    tree = ast.parse(python_file.read_text(encoding="utf-8"), filename=str(python_file))
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == "app.extraction.llm_client"
        and any(alias.name == "generate_structured" for alias in node.names)
        for node in ast.walk(tree)
    )


def test_generate_structured_imported_only_by_the_boundary_layer() -> None:
    allowed = {APP_ROOT / "boundary" / "llm.py", APP_ROOT / "extraction" / "llm_client.py"}
    for python_file in APP_ROOT.rglob("*.py"):
        if python_file in allowed:
            continue
        assert not _imports_generate_structured_by_name(python_file), (
            f"{python_file} imports generate_structured directly from "
            "app.extraction.llm_client, bypassing the boundary layer — violates Invariant "
            "I3 (BUILD.md Phase 4, commit 4)."
        )
