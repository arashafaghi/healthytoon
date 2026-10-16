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


class _ModuleIndex:
    """Resolve dotted module names to discovered files by path suffix.

    A module ``pkg.b`` resolves to the lexicographically smallest discovered
    path equal to ``pkg/b.py`` or ending in ``/pkg/b.py`` (the same rule applies
    to packages via ``__init__.py``). Only paths already in the index are ever
    returned, so the graph never grows a node the runner did not scan.
    """

    def __init__(self, rel_paths: Sequence[str]) -> None:
        self._paths: tuple[str, ...] = tuple(sorted(rel_paths))
        self._cache: dict[tuple[str, ...], str | None] = {}

    def resolve(self, parts: tuple[str, ...]) -> str | None:
        """Return the discovered file for ``parts``, or ``None`` when absent."""

        if not parts:
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


def _import_targets(
    module: ast.Module, dir_parts: tuple[str, ...]
) -> list[tuple[tuple[str, ...], bool]]:
    """Return ``(module parts, skip_self)`` for every import in ``module``.

    ``skip_self`` marks the base of a ``from X import y`` statement, which must
    not become a self-edge when ``X`` is the importing package's ``__init__``;
    a genuine self-import (``import x`` inside ``x.py``) is still kept.
    """

    targets: list[tuple[tuple[str, ...], bool]] = []
    seen: set[tuple[tuple[str, ...], bool]] = set()

    def add(parts: tuple[str, ...], skip_self: bool) -> None:
        if not parts:
            return
        key = (parts, skip_self)
        if key not in seen:
            seen.add(key)
            targets.append(key)

    for node in ast.walk(module):
        if isinstance(node, ast.Import):
            for alias in node.names:
                add(tuple(alias.name.split(".")), False)
        elif isinstance(node, ast.ImportFrom):
            base = _relative_base(dir_parts, node.level, node.module)
            if base is None:
                continue
            if node.module is not None:
                add(base, True)
            for alias in node.names:
                add((*base, *alias.name.split(".")), False)
    return targets


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
        index_of[root] = counter
        lowlink[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
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
                    index_of[neighbour] = counter
                    lowlink[neighbour] = counter
                    counter += 1
                    stack.append(neighbour)
                    on_stack.add(neighbour)
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
                component: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                components.append(component)

    return components


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
        edges: dict[str, set[str]] = {rel: set() for rel in by_rel}
        for rel, path in by_rel.items():
            module = ctx.parse_ast(path)
            if module is None:
                continue
            dir_parts = tuple(PurePosixPath(rel).parts[:-1])
            for parts, skip_self in _import_targets(module, dir_parts):
                target = index.resolve(parts)
                if target is None:
                    continue
                if skip_self and target == rel:
                    continue
                edges[rel].add(target)

        ordered: dict[str, tuple[str, ...]] = {
            rel: tuple(sorted(targets)) for rel, targets in edges.items()
        }
        findings: list[Finding] = []
        for component in _strongly_connected_components(ordered):
            self_loop = len(component) == 1 and component[0] in edges[component[0]]
            if len(component) == 1 and not self_loop:
                continue
            modules = sorted(component)
            symbol = " -> ".join(modules)
            findings.append(
                Finding(
                    rule_id=self.rule_id,
                    check=self.id,
                    category=self.category,
                    severity=self.default_severity,
                    title="Import cycle",
                    message=(f"{len(modules)} modules form an import cycle: {symbol}"),
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
            )

        findings.sort(key=lambda finding: finding.location.file if finding.location else "")
        return _result(self.id, findings)
