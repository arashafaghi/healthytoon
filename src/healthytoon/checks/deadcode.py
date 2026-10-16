"""Dead-code detection through the external Vulture analyzer.

Vulture is an optional external executable, not a Python dependency (ROADMAP
Phase 2a decision 9). The check shells out to ``vulture --min-confidence`` over
the discovered Python files, parses its ``path:line: message`` report, and
turns each non-whitelisted hit into a finding. A small built-in whitelist
suppresses names that are legitimately "unused" by Python's own rules but
meaningful to frameworks: pytest tests and fixtures, ``visit_*`` AST visitor
methods, ``main`` entry points, and names exported through ``__all__``.
Structural suppression additionally drops members of ``typing.Protocol``
subclasses and ``@dataclass`` classes, functions/classes decorated as nox
sessions, and ``nox.options`` configuration attributes, because those are
consumed reflectively rather than named in source.

When ``vulture`` is not on ``PATH`` (or cannot be launched) the check reports
``SKIP`` with an explanatory note rather than failing the audit.
"""

from __future__ import annotations

import ast
import re
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

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

__all__ = ["DeadCodeCheck"]

#: Vulture confidence percentage below which findings are ignored.
_DEFAULT_MIN_CONFIDENCE = 60
#: Upper bound on emitted dead-code findings per run.
_MAX_FINDINGS = 200

#: Name prefixes treated as framework entry points rather than dead code.
_FRAMEWORK_NAME_PREFIXES: tuple[str, ...] = ("test_", "pytest_", "visit_")
#: Exact names treated as framework entry points rather than dead code.
_FRAMEWORK_NAMES: frozenset[str] = frozenset({"main"})

#: Decorator marking a dataclass; matched by terminal name
#: (``@dataclass`` and ``@dataclasses.dataclass``).
_DATACLASS_DECORATOR = "dataclass"
#: Base class marking a ``typing.Protocol``; matched by terminal name.
_PROTOCOL_BASE = "Protocol"
#: Decorator marking a nox session (``@nox.session`` or ``@session``).
_NOX_SESSION_DECORATOR = "session"
#: Root object and attribute whose assignments configure nox (``nox.options``).
_NOX_OPTIONS_OWNER = "nox"
_NOX_OPTIONS_ATTRIBUTE = "options"

#: Notes surfaced when the backend is unavailable; tests assert on this text.
_VULTURE_MISSING_NOTE = "vulture is not installed; dead-code analysis skipped"

#: Trailing ``(NN% confidence)`` suffix Vulture appends to every message.
_CONFIDENCE_RE = re.compile(r"\s*\((\d+)% confidence\)\s*$")
#: Quoted identifier inside a Vulture message, e.g. ``unused function 'name'``.
_QUOTED_NAME_RE = re.compile(r"'([^']+)'")


@dataclass(frozen=True, slots=True)
class _VultureIssue:
    """One parsed Vulture report line."""

    file: str
    line: int
    message: str
    confidence: int
    symbol: str | None


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


def _string_list_option(options: Mapping[str, object], key: str) -> tuple[str, ...]:
    """Read a list-of-strings option; accept a lone string and ignore the rest."""

    value = options.get(key)
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Iterable):
        return tuple(item for item in value if isinstance(item, str))
    return ()


def _result(check_id: str, findings: list[Finding]) -> CheckResult:
    """Wrap ``findings`` in a :class:`CheckResult` (WARN when anything fired)."""

    status = Status.WARN if findings else Status.PASS
    return CheckResult(check=check_id, findings=tuple(findings), status=status)


def _run_vulture(
    executable: str, min_confidence: int, paths: Sequence[Path], root: Path
) -> subprocess.CompletedProcess[str] | None:
    """Run Vulture over ``paths``, or ``None`` when it cannot be launched."""

    args = [
        executable,
        "--min-confidence",
        str(min_confidence),
        *(str(path) for path in paths),
    ]
    try:
        return subprocess.run(args, cwd=root, capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError):
        return None


def _dead_code_finding(check: DeadCodeCheck, issue: _VultureIssue, root: Path) -> Finding:
    """Build the finding for one parsed, non-whitelisted Vulture issue."""

    return Finding(
        rule_id=check.rule_id,
        check=check.id,
        category=check.category,
        severity=check.default_severity,
        title="Dead code",
        message=issue.message,
        location=Location(file=_relative_posix(Path(issue.file), root), line=issue.line),
        symbol=issue.symbol,
        properties={"confidence": issue.confidence},
        remediation=Remediation(
            summary="Remove the dead code or reference it explicitly",
            detail=(
                "Delete the unused definition, or add it to ``__all__`` / "
                "the configured whitelist when an external framework "
                "imports it dynamically."
            ),
        ),
    )


def _analysis_findings(
    check: DeadCodeCheck,
    issues: Sequence[_VultureIssue],
    whitelist: frozenset[str],
    root: Path,
) -> list[Finding]:
    """Turn parsed Vulture issues into findings, dropping whitelisted symbols."""

    return [
        _dead_code_finding(check, issue, root)
        for issue in issues
        if not _is_whitelisted(issue.symbol, whitelist)
    ]


def _finding_sort_key(finding: Finding) -> tuple[str, int, str, str]:
    """Return the deterministic ordering key for dead-code findings."""

    location = finding.location
    file = location.file if location else ""
    line = location.line if location and location.line else 0
    return (file, line, finding.symbol or "", finding.message)


def _whitelist_for(
    ctx: CheckContext, paths: Sequence[Path], user_whitelist: Sequence[str]
) -> frozenset[str]:
    """Collect the framework and user whitelisted names for ``paths``."""

    modules = [module for module in (ctx.parse_ast(path) for path in paths) if module]
    return frozenset(_framework_whitelist(modules, user_whitelist))


def _parse_confidence(message: str) -> tuple[str, int]:
    """Split a Vulture message into ``(message without suffix, confidence)``."""

    match = _CONFIDENCE_RE.search(message)
    if match is None:
        return message, 100
    return _CONFIDENCE_RE.sub("", message).strip(), int(match.group(1))


def _parse_line(line: str) -> _VultureIssue | None:
    """Parse one ``path:line: message`` Vulture report line, or ``None``."""

    stripped = line.strip()
    if not stripped:
        return None
    file, separator, remainder = stripped.partition(":")
    if not separator:
        return None
    line_field, separator, raw_message = remainder.partition(":")
    if not separator:
        return None
    try:
        line_number = int(line_field)
    except ValueError:
        return None
    message, confidence = _parse_confidence(raw_message.strip())
    # Only "unused <kind> '<name>'" messages reference a definition we could
    # suppress by name; other messages (e.g. unreachable code) quote syntax
    # keywords such as ``return`` rather than a symbol.
    symbol: str | None = None
    if message.startswith("unused "):
        name_match = _QUOTED_NAME_RE.search(message)
        if name_match is not None:
            symbol = name_match.group(1)
    return _VultureIssue(
        file=file,
        line=line_number,
        message=message,
        confidence=confidence,
        symbol=symbol,
    )


def _parse_output(stdout: str) -> list[_VultureIssue]:
    """Parse every Vulture report line, ignoring anything unrecognizable."""

    issues: list[_VultureIssue] = []
    for line in stdout.splitlines():
        issue = _parse_line(line)
        if issue is not None:
            issues.append(issue)
    return issues


def _reference_name(node: ast.expr) -> str | None:
    """Return the terminal name of a decorator or base-class expression.

    Calls, subscriptions, and attribute access resolve to the final identifier,
    so ``@pytest.fixture``, ``Protocol[T]``, and ``nox.session`` become
    ``fixture``, ``Protocol``, and ``session`` respectively.
    """

    if isinstance(node, ast.Call):
        return _reference_name(node.func)
    if isinstance(node, ast.Subscript):
        return _reference_name(node.value)
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _is_fixture(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True when ``node`` looks like a pytest fixture (``@fixture``/``@pytest.fixture``)."""

    return any(_reference_name(decorator) == "fixture" for decorator in node.decorator_list)


def _is_nox_session(node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True when ``node`` carries a nox session decorator (``@nox.session``/``@session``)."""

    return any(
        _reference_name(decorator) == _NOX_SESSION_DECORATOR for decorator in node.decorator_list
    )


def _is_protocol_subclass(node: ast.ClassDef) -> bool:
    """True when ``node`` directly subclasses ``typing.Protocol``."""

    return any(_reference_name(base) == _PROTOCOL_BASE for base in node.bases)


def _is_dataclass(node: ast.ClassDef) -> bool:
    """True when ``node`` is decorated with ``@dataclass`` or ``@dataclasses.dataclass``."""

    return any(
        _reference_name(decorator) == _DATACLASS_DECORATOR for decorator in node.decorator_list
    )


def _class_member_names(node: ast.ClassDef) -> set[str]:
    """Return the attribute, property, and method names declared on ``node``."""

    names: set[str] = set()
    for statement in node.body:
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
            names.add(statement.name)
        elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            names.add(statement.target.id)
        elif isinstance(statement, ast.Assign):
            for target in statement.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def _nox_option_names(node: ast.Assign | ast.AnnAssign) -> set[str]:
    """Return the ``nox.options.<name>`` attribute names assigned by ``node``."""

    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    names: set[str] = set()
    for target in targets:
        if not isinstance(target, ast.Attribute):
            continue
        owner = target.value
        if not isinstance(owner, ast.Attribute) or owner.attr != _NOX_OPTIONS_ATTRIBUTE:
            continue
        root = owner.value
        if isinstance(root, ast.Name) and root.id == _NOX_OPTIONS_OWNER:
            names.add(target.attr)
    return names


def _string_elements(value: ast.expr) -> set[str]:
    """Return the string constants in a list/tuple/set literal."""

    elements: set[str] = set()
    if not isinstance(value, ast.List | ast.Tuple | ast.Set):
        return elements
    for element in value.elts:
        if isinstance(element, ast.Constant) and isinstance(element.value, str):
            elements.add(element.value)
    return elements


def _dunder_all_names(module: ast.Module) -> set[str]:
    """Return the names exported by ``module`` through a top-level ``__all__``."""

    names: set[str] = set()
    for node in module.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    names.update(_string_elements(node.value))
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "__all__"
            and node.value is not None
        ):
            names.update(_string_elements(node.value))
    return names


def _framework_whitelist(modules: Sequence[ast.Module], extra: Sequence[str]) -> set[str]:
    """Collect names the built-in framework rules and the user must not flag."""

    names: set[str] = set(_FRAMEWORK_NAMES)
    names.update(extra)
    for module in modules:
        names.update(_dunder_all_names(module))
        for node in ast.walk(module):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and _is_fixture(node):
                names.add(node.name)
            if isinstance(
                node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
            ) and _is_nox_session(node):
                names.add(node.name)
            if isinstance(node, ast.ClassDef) and (
                _is_protocol_subclass(node) or _is_dataclass(node)
            ):
                names.update(_class_member_names(node))
            if isinstance(node, ast.Assign | ast.AnnAssign):
                names.update(_nox_option_names(node))
    return names


def _is_whitelisted(symbol: str | None, whitelist: frozenset[str]) -> bool:
    """True when ``symbol`` is protected by an exact or prefix rule."""

    if symbol is None:
        return False
    if symbol in whitelist:
        return True
    return symbol.startswith(_FRAMEWORK_NAME_PREFIXES)


class DeadCodeCheck:
    """Flag dead code reported by Vulture, minus framework entry points."""

    id: str = "maintainability.dead_code"
    rule_id: str = "HT-MAINT-007"
    category: Category = Category.MAINTAINABILITY
    default_severity: Severity = Severity.LOW
    api_version: int = CHECK_API_VERSION
    cross_file: bool = True
    file_kinds: frozenset[FileKind] = frozenset({FileKind.PYTHON})

    def supports(self, ctx: CheckContext) -> bool:
        """Run whenever the context contains at least one Python file."""

        return bool(ctx.files_for(self.file_kinds))

    def run(self, ctx: CheckContext) -> CheckResult:
        """Run Vulture over the discovered files, or skip when it is absent."""

        executable = _optional.vulture_executable()
        if executable is None:
            return CheckResult(check=self.id, status=Status.SKIP, notes=_VULTURE_MISSING_NOTE)

        options = ctx.rule_options(
            self.id,
            {"min_confidence": _DEFAULT_MIN_CONFIDENCE, "whitelist": []},
        )
        min_confidence = _int_option(options, "min_confidence", _DEFAULT_MIN_CONFIDENCE)
        user_whitelist = _string_list_option(options, "whitelist")

        paths = sorted(ctx.files_for(self.file_kinds))
        whitelist = _whitelist_for(ctx, paths, user_whitelist)

        completed = _run_vulture(executable, min_confidence, paths, ctx.root)
        if completed is None:
            return CheckResult(
                check=self.id,
                status=Status.SKIP,
                notes="vulture could not be executed; dead-code analysis skipped",
            )

        issues = _parse_output(completed.stdout)
        if not issues and not completed.stdout.strip() and completed.returncode != 0:
            return CheckResult(
                check=self.id,
                status=Status.SKIP,
                notes=(
                    f"vulture exited with code {completed.returncode} and produced "
                    "no output; dead-code analysis skipped"
                ),
            )

        findings = _analysis_findings(self, issues, whitelist, ctx.root)
        findings.sort(key=_finding_sort_key)
        return _result(self.id, findings[:_MAX_FINDINGS])
