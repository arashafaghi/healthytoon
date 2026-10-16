"""Helpers shared verbatim by the coverage checks.

``typing.annotation_coverage`` and ``documentation.docstring_coverage`` both
need to render repo-relative paths, coerce numeric rule options, iterate the
parsed modules, and wrap findings in a :class:`CheckResult`. Those routines and
the trivial class scaffolding were byte-for-byte identical copies in each module
(HT-MAINT-005), so they live here once and are imported by both checks.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator, Mapping
from pathlib import Path

from healthytoon.core.context import CheckContext
from healthytoon.core.protocols import CHECK_API_VERSION, CheckResult
from healthytoon.core.types import FileKind, Finding, Severity, Status

__all__ = [
    "_RatioCoverageCheck",
    "_float_option",
    "_parsed_modules",
    "_ratio_option",
    "_relative_posix",
    "_result",
]


def _relative_posix(path: Path, root: Path) -> str:
    """Return ``path`` relative to ``root`` as a POSIX path.

    Falls back to the path itself when it cannot be expressed relative to
    ``root`` (for example in hand-built test contexts).
    """

    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _float_option(options: Mapping[str, object], key: str, default: float) -> float:
    """Read numeric option ``key``; fall back to ``default`` when unusable."""

    value = options.get(key, default)
    if isinstance(value, bool):
        return default
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _result(check_id: str, findings: list[Finding]) -> CheckResult:
    """Wrap ``findings`` in a :class:`CheckResult` (WARN when anything fired)."""

    status = Status.WARN if findings else Status.PASS
    return CheckResult(check=check_id, findings=tuple(findings), status=status)


def _supports_python_files(ctx: CheckContext, file_kinds: frozenset[FileKind]) -> bool:
    """Return whether ``ctx`` contains at least one file of ``file_kinds``."""

    return bool(ctx.files_for(file_kinds))


def _ratio_option(ctx: CheckContext, check_id: str, default: float) -> float:
    """Read the ``min_ratio`` rule option for ``check_id``."""

    options = ctx.rule_options(check_id, {"min_ratio": default})
    return _float_option(options, "min_ratio", default)


def _parsed_modules(
    ctx: CheckContext, file_kinds: frozenset[FileKind]
) -> Iterator[tuple[Path, ast.Module]]:
    """Yield ``(path, module)`` for every parseable file of ``file_kinds``."""

    for path in sorted(ctx.files_for(file_kinds)):
        module = ctx.parse_ast(path)
        if module is not None:
            yield path, module


class _RatioCoverageCheck:
    """Shared scaffolding for the per-file ratio coverage checks."""

    default_severity: Severity = Severity.LOW
    api_version: int = CHECK_API_VERSION
    cross_file: bool = False
    file_kinds: frozenset[FileKind] = frozenset({FileKind.PYTHON})

    def supports(self, ctx: CheckContext) -> bool:
        """Run whenever the context contains at least one Python file."""

        return _supports_python_files(ctx, self.file_kinds)
