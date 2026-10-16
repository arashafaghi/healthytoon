"""Import-cycle detection over the repository's Python file graph.

Imports are extracted from each module's AST (absolute and relative) and
resolved to discovered files by matching the dotted module against a POSIX path
suffix. Cycles are the strongly connected components of that graph, computed
with an iterative Tarjan pass so a deep repository cannot exhaust the recursion
limit. Findings are deterministic: nodes are visited in sorted order and the
lexicographically smallest member of each cycle carries the location.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath

from healthytoon.core.context import CheckContext
from healthytoon.core.protocols import CHECK_API_VERSION, CheckResult
from healthytoon.core.types import (
    Category,
    FileKind,
    Finding,
    Location,
    Remediation,
    Severity,
    Status,
)

__all__ = ["ImportCycleCheck"]


def _relative_posix(path: Path, root: Path) -> str:
    """Return ``path`` relative to ``root`` as a POSIX path.

    Falls back to the path itself when it cannot be expressed relative to
    ``root`` (for example in hand-built test contexts).
    """

    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _result(check_id: str, findings: list[Finding]) -> CheckResult:
    """Wrap ``findings`` in a :class:`CheckResult` (WARN when anything fired)."""

    status = Status.WARN if findings else Status.PASS
    return CheckResult(check=check_id, findings=tuple(findings), status=status)


def _project_top_level(rel_paths: Sequence[str]) -> frozenset[str]:
    """Return the top-level module/package names of ``rel_paths``.

    A top-level directory (``pkg/...``) or a root module (``pkg.py``)
    contributes ``pkg``. Used to decide whether a standard-library import name
    is shadowed by a project package of the same name.
    """

    names: set[str] = set()
    for rel in rel_paths:
        parts = PurePosixPath(rel).parts
        if not parts:
            continue
        head = parts[0]
        if len(parts) > 1:
            names.add(head)
        elif head.endswith(".py"):
            names.add(head[:-3])
    return frozenset(names)


class _ModuleIndex:
    """Resolve dotted module names to discovered files by path suffix.

    A module ``pkg.b`` resolves to the lexicographically smallest discovered
    path equal to ``pkg/b.py`` or ending in ``/pkg/b.py`` (the same rule applies
    to packages via ``__init__.py``). Only paths already in the index are ever
    returned, so the graph never grows a node the runner did not scan. A
    standard-library name (``import json``) is ignored unless the project has a
    top-level package of that name, so a file such as ``reporters/json.py`` is
    not mistaken for the standard library.
    """

    def __init__(self, rel_paths: Sequence[str]) -> None:
        self._paths: tuple[str, ...] = tuple(sorted(rel_paths))
        self._cache: dict[tuple[str, ...], str | None] = {}
        self._project_names: frozenset[str] = _project_top_level(self._paths)

    def resolve(self, parts: tuple[str, ...]) -> str | None:
        """Return the discovered file for ``parts``, or ``None`` when absent."""

        if not parts:
            return None
        if parts[0] in sys.stdlib_module_names and parts[0] not in self._project_names:
            return None
        if parts in self._cache:
            return self._cache[parts]
        found = self._lookup(parts)
        self._cache[parts] = found
        return found

    def _lookup(self, parts: tuple[str, ...]) -> str | None:
        dotted = "/".join(parts)
        for suffix in (f"{dotted}.py", f"{dotted}/__init__.py"):
            for path in self._paths:
                if path == suffix or path.endswith(f"/{suffix}"):
                    return path
        return None


def _relative_base(
    dir_parts: tuple[str, ...], level: int, module: str | None
) -> tuple[str, ...] | None:
    """Resolve the base module of an ``import from`` statement.

    ``dir_parts`` is the dotted package of the importing file (its directory
    components). For an absolute import (``level == 0``) the base is simply the
    dotted ``module``. For a relative import, ``level - 1`` leading packages are
    stripped before the dotted ``module`` (when any) is appended. Returns
    ``None`` when the level escapes the repository root.
    """

    if level == 0:
        return tuple(module.split(".")) if module else None
    drop = level - 1
    if drop > len(dir_parts):
        return None
    base = dir_parts[: len(dir_parts) - drop] if drop else dir_parts
    if module:
        base = (*base, *module.split("."))
    return tuple(base)


def _from_import_targets(
    base: tuple[str, ...], node: ast.ImportFrom, index: _ModuleIndex, rel: str
) -> set[str]:
    """Resolve a ``from base import name`` statement to discovered files.

    When ``base.name`` is a discovered submodule, only the submodule edge is
    returned: importing a submodule through its package must not also create an
    edge to the package ``__init__``, which would fabricate a cycle from every
    submodule back to the package. The package edge is kept only when none of
    the imported names resolves as a submodule (a genuine ``from pkg import x``
    where ``x`` is defined in ``pkg/__init__.py``), and never as a self-edge.
    """

    submodules = {index.resolve((*base, *alias.name.split("."))) for alias in node.names}
    found = {target for target in submodules if target is not None}
    if found:
        return found
    package = index.resolve(base)
    if package is None or package == rel:
        return set()
    return {package}


def _module_targets(
    module: ast.Module, dir_parts: tuple[str, ...], rel: str, index: _ModuleIndex
) -> set[str]:
    """Return the discovered files every import in ``module`` resolves to."""

    targets: set[str] = set()
    for node in ast.walk(module):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = index.resolve(tuple(alias.name.split(".")))
                if target is not None:
                    targets.add(target)
        elif isinstance(node, ast.ImportFrom):
            base = _relative_base(dir_parts, node.level, node.module)
            if base is not None:
                targets.update(_from_import_targets(base, node, index, rel))
    return targets


def _open_node(
    node: str,
    index_of: dict[str, int],
    lowlink: dict[str, int],
    on_stack: set[str],
    stack: list[str],
    counter: int,
) -> int:
    """Push ``node`` onto Tarjan's stack with the next available index."""

    index_of[node] = counter
    lowlink[node] = counter
    stack.append(node)
    on_stack.add(node)
    return counter + 1


def _pop_component(node: str, stack: list[str], on_stack: set[str]) -> list[str]:
    """Pop ``node`` and everything above it off the stack as one component."""

    component: list[str] = []
    while True:
        member = stack.pop()
        on_stack.discard(member)
        component.append(member)
        if member == node:
            break
    return component


def _strongly_connected_components(
    edges: Mapping[str, Sequence[str]],
) -> list[list[str]]:
    """Return Tarjan's strongly connected components of ``edges``.

    Iterative so arbitrarily deep import chains stay within the C stack. The
    component list order is deterministic for a sorted node traversal.
    """

    index_of: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    counter = 0
    components: list[list[str]] = []

    for root in sorted(edges):
        if root in index_of:
            continue
        counter = _open_node(root, index_of, lowlink, on_stack, stack, counter)
        work: list[tuple[str, int]] = [(root, 0)]
        while work:
            node, next_index = work[-1]
            neighbours = edges[node]
            advanced = False
            while next_index < len(neighbours):
                neighbour = neighbours[next_index]
                next_index += 1
                if neighbour not in index_of:
                    work[-1] = (node, next_index)
                    counter = _open_node(neighbour, index_of, lowlink, on_stack, stack, counter)
                    work.append((neighbour, 0))
                    advanced = True
                    break
                if neighbour in on_stack:
                    lowlink[node] = min(lowlink[node], index_of[neighbour])
            if advanced:
                continue
            work[-1] = (node, next_index)
            work.pop()
            if work:
                parent = work[-1][0]
                lowlink[parent] = min(lowlink[parent], lowlink[node])
            if lowlink[node] == index_of[node]:
                components.append(_pop_component(node, stack, on_stack))

    return components


def _build_edges(
    ctx: CheckContext, index: _ModuleIndex, file_kinds: frozenset[FileKind]
) -> dict[str, set[str]]:
    """Resolve every discovered Python file's imports to discovered files."""

    edges: dict[str, set[str]] = {}
    for path in sorted(ctx.files_for(file_kinds)):
        rel = _relative_posix(path, ctx.root)
        module = ctx.parse_ast(path)
        if module is None:
            edges[rel] = set()
            continue
        dir_parts = tuple(PurePosixPath(rel).parts[:-1])
        edges[rel] = _module_targets(module, dir_parts, rel, index)
    return edges


def _cycle_finding(
    check: ImportCycleCheck, component: Sequence[str], edges: Mapping[str, set[str]]
) -> Finding | None:
    """Build the finding for ``component``, or ``None`` for a non-cycle singleton."""

    self_loop = len(component) == 1 and component[0] in edges[component[0]]
    if len(component) == 1 and not self_loop:
        return None
    modules = sorted(component)
    symbol = " -> ".join(modules)
    return Finding(
        rule_id=check.rule_id,
        check=check.id,
        category=check.category,
        severity=check.default_severity,
        title="Import cycle",
        message=f"{len(modules)} modules form an import cycle: {symbol}",
        location=Location(file=modules[0], line=1),
        symbol=symbol,
        properties={"modules": modules},
        remediation=Remediation(
            summary="Break the import cycle",
            detail=(
                "Move the shared dependency into a separate module or "
                "defer one import into a function so the modules no "
                "longer reference each other at import time."
            ),
        ),
    )


class ImportCycleCheck:
    """Flag groups of modules that import each other in a cycle."""

    id: str = "maintainability.import_cycles"
    rule_id: str = "HT-MAINT-004"
    category: Category = Category.MAINTAINABILITY
    default_severity: Severity = Severity.MEDIUM
    api_version: int = CHECK_API_VERSION
    cross_file: bool = True
    file_kinds: frozenset[FileKind] = frozenset({FileKind.PYTHON})

    def supports(self, ctx: CheckContext) -> bool:
        """Run whenever the context contains at least one Python file."""

        return bool(ctx.files_for(self.file_kinds))

    def run(self, ctx: CheckContext) -> CheckResult:
        """Emit one finding per import cycle of two or more modules (or a self-loop)."""

        by_rel: dict[str, Path] = {}
        for path in sorted(ctx.files_for(self.file_kinds)):
            by_rel[_relative_posix(path, ctx.root)] = path

        index = _ModuleIndex(tuple(by_rel))
        edges = _build_edges(ctx, index, self.file_kinds)
        ordered: dict[str, tuple[str, ...]] = {
            rel: tuple(sorted(targets)) for rel, targets in edges.items()
        }

        findings: list[Finding] = []
        for component in _strongly_connected_components(ordered):
            finding = _cycle_finding(self, component, edges)
            if finding is not None:
                findings.append(finding)

        findings.sort(key=lambda finding: finding.location.file if finding.location else "")
        return _result(self.id, findings)
