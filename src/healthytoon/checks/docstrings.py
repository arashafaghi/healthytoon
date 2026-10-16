"""Documentation coverage: docstrings for public modules, classes, and functions."""

from __future__ import annotations

import ast

from healthytoon.checks._shared import (
    _parsed_modules,
    _ratio_option,
    _RatioCoverageCheck,
    _relative_posix,
    _result,
)
from healthytoon.core.context import CheckContext
from healthytoon.core.protocols import CheckResult
from healthytoon.core.types import Category, Finding, Location, Remediation

__all__ = ["DocstringCoverageCheck"]

#: Share of documentable items that must carry a docstring by default.
_DEFAULT_MIN_RATIO = 0.8


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


class DocstringCoverageCheck(_RatioCoverageCheck):
    """Flag modules where too few documentable items carry a docstring."""

    id: str = "documentation.docstring_coverage"
    rule_id: str = "HT-DOC-001"
    category: Category = Category.DOCUMENTATION

    def run(self, ctx: CheckContext) -> CheckResult:
        """Emit one finding per module below the ``min_ratio`` documentation share."""

        min_ratio = _ratio_option(ctx, self.id, _DEFAULT_MIN_RATIO)
        findings: list[Finding] = []
        for path, module in _parsed_modules(ctx, self.file_kinds):
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
