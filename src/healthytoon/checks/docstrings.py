"""Documentation coverage: docstrings for public modules, classes, and functions."""

from __future__ import annotations

import ast
from collections.abc import Mapping
from pathlib import Path

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

__all__ = ["DocstringCoverageCheck"]

#: Share of documentable items that must carry a docstring by default.
_DEFAULT_MIN_RATIO = 0.8


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


def _documented_flags(module: ast.Module) -> list[bool]:
    """Return docstring presence for the module docstring and public definitions.

    The module docstring is always one entry (documented or not), so a bare
    module without a docstring already starts at ``0/1``. Public classes,
    functions, and methods — by name, at any nesting depth — each add an entry.
    Private names (leading underscore) are excluded.
    """

    flags = [ast.get_docstring(module) is not None]
    for node in ast.walk(module):
        if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name.startswith("_"):
            continue
        flags.append(ast.get_docstring(node) is not None)
    return flags


class DocstringCoverageCheck:
    """Flag modules where too few documentable items carry a docstring."""

    id: str = "documentation.docstring_coverage"
    rule_id: str = "HT-DOC-001"
    category: Category = Category.DOCUMENTATION
    default_severity: Severity = Severity.LOW
    api_version: int = CHECK_API_VERSION
    cross_file: bool = False
    file_kinds: frozenset[FileKind] = frozenset({FileKind.PYTHON})

    def supports(self, ctx: CheckContext) -> bool:
        """Run whenever the context contains at least one Python file."""

        return bool(ctx.files_for(self.file_kinds))

    def run(self, ctx: CheckContext) -> CheckResult:
        """Emit one finding per module below the ``min_ratio`` documentation share."""

        options = ctx.rule_options(self.id, {"min_ratio": _DEFAULT_MIN_RATIO})
        min_ratio = _float_option(options, "min_ratio", _DEFAULT_MIN_RATIO)
        findings: list[Finding] = []
        for path in sorted(ctx.files_for(self.file_kinds)):
            module = ctx.parse_ast(path)
            if module is None:
                continue
            flags = _documented_flags(module)
            total = len(flags)
            documented = sum(flags)
            if total == 0 or documented / total >= min_ratio:
                continue
            file = _relative_posix(path, ctx.root)
            findings.append(
                Finding(
                    rule_id=self.rule_id,
                    check=self.id,
                    category=self.category,
                    severity=self.default_severity,
                    title="Low docstring coverage",
                    message=(
                        f"only {documented}/{total} items have docstrings (min {min_ratio:.0%})"
                    ),
                    location=Location(file=file, line=1),
                    symbol="<module>",
                    properties={
                        "documented": documented,
                        "total": total,
                        "min_ratio": min_ratio,
                    },
                    remediation=Remediation(
                        summary="Document the public API",
                        detail=(
                            "Add docstrings to public modules, classes, functions, "
                            "and methods so the API is self-describing."
                        ),
                    ),
                )
            )
        return _result(self.id, findings)
