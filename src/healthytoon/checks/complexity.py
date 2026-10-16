"""Cyclomatic-complexity check backed by the optional Radon analyzer.

Radon is an optional backend (ROADMAP Phase 2a decision 9): it lives in the
``analysis`` extra and is imported at runtime. When it is not installed the
check reports ``SKIP`` with an explanatory note instead of falling back to an
internal McCabe implementation. When it is available, each Python file is
analyzed with ``radon.complexity.cc_visit`` and every block whose complexity
exceeds the configured threshold becomes a finding.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import ModuleType

from healthytoon.checks import _optional
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

__all__ = ["ComplexityCheck"]

#: Cyclomatic complexity at or below which a block is considered acceptable.
_DEFAULT_MAX_CYCLOMATIC = 10

#: Note surfaced when Radon is not importable; tests assert on this text.
_RADON_MISSING_NOTE = "radon is not installed; complexity analysis skipped"


def _relative_posix(path: Path, root: Path) -> str:
    """Return ``path`` relative to ``root`` as a POSIX path.

    Falls back to the path itself when it cannot be expressed relative to
    ``root`` (for example in hand-built test contexts).
    """

    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _int_option(options: Mapping[str, object], key: str, default: int) -> int:
    """Read integer option ``key``; fall back to ``default`` when unusable."""

    value = options.get(key, default)
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _block_symbol(block: object) -> str:
    """Return Radon's qualified name for ``block`` (class/function/method)."""

    fullname = getattr(block, "fullname", None)
    if isinstance(fullname, str) and fullname:
        return fullname
    name = getattr(block, "name", None)
    if isinstance(name, str) and name:
        return name
    return "<unknown>"


def _block_int(block: object, attribute: str, default: int) -> int:
    """Read an integer attribute off a Radon block, falling back on nonsense."""

    value = getattr(block, attribute, default)
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    return default


def _result(check_id: str, findings: list[Finding]) -> CheckResult:
    """Wrap ``findings`` in a :class:`CheckResult` (WARN when anything fired)."""

    status = Status.WARN if findings else Status.PASS
    return CheckResult(check=check_id, findings=tuple(findings), status=status)


def _complexity_finding(
    check: ComplexityCheck,
    block: object,
    file: str,
    max_cyclomatic: int,
) -> Finding:
    """Build the finding for one over-threshold Radon block."""

    complexity = _block_int(block, "complexity", 1)
    symbol = _block_symbol(block)
    return Finding(
        rule_id=check.rule_id,
        check=check.id,
        category=check.category,
        severity=check.default_severity,
        title="High cyclomatic complexity",
        message=(f"{symbol} has cyclomatic complexity {complexity} (max {max_cyclomatic})"),
        location=Location(file=file, line=_block_int(block, "lineno", 1)),
        symbol=symbol,
        properties={"complexity": complexity, "max": max_cyclomatic},
        remediation=Remediation(
            summary=f"Reduce the complexity of {symbol}",
            detail=(
                "Extract branches into named helpers, replace nested "
                "conditionals with guard clauses, or split unrelated "
                "concerns so the block has fewer decision points."
            ),
        ),
    )


def _file_findings(
    check: ComplexityCheck,
    radon: ModuleType,
    ctx: CheckContext,
    path: Path,
    max_cyclomatic: int,
) -> list[Finding]:
    """Return the complexity findings for a single Python file."""

    try:
        blocks = radon.cc_visit(ctx.read_text(path))
    except (SyntaxError, ValueError):
        return []
    file = _relative_posix(path, ctx.root)
    findings: list[Finding] = []
    for block in blocks:
        if _block_int(block, "complexity", 1) > max_cyclomatic:
            findings.append(_complexity_finding(check, block, file, max_cyclomatic))
    return findings


class ComplexityCheck:
    """Flag blocks whose cyclomatic complexity exceeds ``max_cyclomatic``."""

    id: str = "maintainability.complexity"
    rule_id: str = "HT-MAINT-006"
    category: Category = Category.MAINTAINABILITY
    default_severity: Severity = Severity.MEDIUM
    api_version: int = CHECK_API_VERSION
    cross_file: bool = False
    file_kinds: frozenset[FileKind] = frozenset({FileKind.PYTHON})

    def supports(self, ctx: CheckContext) -> bool:
        """Run whenever the context contains at least one Python file."""

        return bool(ctx.files_for(self.file_kinds))

    def run(self, ctx: CheckContext) -> CheckResult:
        """Analyze each Python file with Radon, or skip when Radon is absent."""

        radon = _optional.radon_module()
        if radon is None:
            return CheckResult(check=self.id, status=Status.SKIP, notes=_RADON_MISSING_NOTE)

        options = ctx.rule_options(self.id, {"max_cyclomatic": _DEFAULT_MAX_CYCLOMATIC})
        max_cyclomatic = _int_option(options, "max_cyclomatic", _DEFAULT_MAX_CYCLOMATIC)

        findings: list[Finding] = []
        for path in sorted(ctx.files_for(self.file_kinds)):
            findings.extend(_file_findings(self, radon, ctx, path, max_cyclomatic))

        findings.sort(
            key=lambda finding: (
                finding.location.file if finding.location else "",
                finding.location.line if finding.location and finding.location.line else 0,
                finding.symbol or "",
            )
        )
        return _result(self.id, findings)
