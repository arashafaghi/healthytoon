"""Structural maintainability checks: function length, nesting, module size.

Every check here works file by file over the standard-library AST: Python files
are visited in sorted order, files that fail to parse are skipped, and locations
use repo-relative POSIX paths so reports are deterministic across machines.
"""

from __future__ import annotations

import ast
import io
import tokenize
from collections.abc import Mapping, Sequence
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

__all__ = ["FunctionLengthCheck", "ModuleLengthCheck", "NestingDepthCheck"]

#: Lines before ``maintainability.function_length`` fires.
_DEFAULT_MAX_LENGTH = 50
#: Control-flow nesting levels before ``maintainability.nesting_depth`` fires.
_DEFAULT_MAX_DEPTH = 5
#: Source lines before ``maintainability.module_length`` fires.
_DEFAULT_MAX_LINES = 1000

_CONTROL_FLOW: tuple[type[ast.stmt], ...] = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.With,
    ast.AsyncWith,
    ast.Try,
    ast.TryStar,
    ast.Match,
)

_COMPREHENSIONS: tuple[type[ast.expr], ...] = (
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)

_SCOPE_NODES: tuple[type[ast.AST], ...] = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
)


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


def _bool_option(options: Mapping[str, object], key: str, default: bool) -> bool:
    """Read boolean option ``key``; fall back to ``default`` when unusable."""

    value = options.get(key, default)
    return value if isinstance(value, bool) else default


def _result(check_id: str, findings: list[Finding]) -> CheckResult:
    """Wrap ``findings`` in a :class:`CheckResult` (WARN when anything fired)."""

    status = Status.WARN if findings else Status.PASS
    return CheckResult(check=check_id, findings=tuple(findings), status=status)


#: Token types that carry no source-line content of their own.
_NON_SOURCE_TOKENS = frozenset(
    {
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENCODING,
        tokenize.ENDMARKER,
    }
)


def _non_blank_lines(text: str) -> int:
    """Count lines that contain at least one non-whitespace character."""

    return sum(1 for line in text.splitlines() if line.strip())


def _source_lines(text: str) -> int:
    """Count source lines: physical lines covered by tokens that carry code.

    Counting from tokens (rather than by stripped line prefixes) keeps ``#``
    characters that appear inside triple-quoted strings or other multi-line
    tokens from being mistaken for comments. Multi-line tokens such as strings
    contribute every physical line they span. Text that cannot be tokenized
    falls back to a plain non-blank line count.
    """

    lines: set[int] = set()
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type in _NON_SOURCE_TOKENS:
                continue
            lines.update(range(token.start[0], token.end[0] + 1))
    except (tokenize.TokenError, IndentationError):
        return _non_blank_lines(text)
    return len(lines)


class _FunctionCollector(ast.NodeVisitor):
    """Collect ``(qualified name, node)`` for every function, in source order.

    Qualified names are dotted through enclosing classes and functions, so a
    method is ``Outer.method`` and a closure helper is ``outer.helper``. The
    module name is excluded because the location already names the file.
    """

    def __init__(self) -> None:
        self._scope: list[str] = []
        self.functions: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._enter_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._enter_function(node)

    def _enter_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        name = ".".join((*self._scope, node.name))
        self.functions.append((name, node))
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()


def _disambiguate_functions(
    functions: Sequence[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]],
) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Append an occurrence index to repeated qualified names within one file.

    Two definitions can share a qualified name (conditional definitions or an
    ``@overload`` stub next to its implementation). Fingerprints are built from
    rule id, file, and symbol, so repeated names must be disambiguated or the
    runner would collapse one finding into the other. The first occurrence keeps
    its name; later ones become ``name#2``, ``name#3``, and so on.
    """

    seen: dict[str, int] = {}
    unique: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    for name, node in functions:
        index = seen.get(name, 0) + 1
        seen[name] = index
        unique.append((name if index == 1 else f"{name}#{index}", node))
    return unique


def _is_elif_continuation(parent: ast.AST, child: ast.AST) -> bool:
    """True when ``child`` is the ``If`` that an ``elif`` desugars to.

    Python represents ``elif`` as an :class:`ast.If` sitting as the sole element
    of the parent ``If``'s ``orelse``. That is a sibling branch, not a deeper
    nesting, so it must be measured at the parent's depth.
    """

    return (
        isinstance(parent, ast.If)
        and isinstance(child, ast.If)
        and len(parent.orelse) == 1
        and parent.orelse[0] is child
    )


def _max_nesting_depth(
    node: ast.AST,
    depth: int,
    *,
    count_comprehensions: bool,
    count_lambdas: bool,
) -> int:
    """Return the deepest control-flow nesting under ``node``, inclusive.

    Nested functions and classes are separate scopes: they are not descended
    into, because each is measured from its own definition instead.
    """

    deepest = depth
    for child in ast.iter_child_nodes(node):
        if isinstance(child, _SCOPE_NODES):
            continue
        child_depth = depth
        counts_as_control_flow = (
            isinstance(child, _CONTROL_FLOW)
            or (count_comprehensions and isinstance(child, _COMPREHENSIONS))
            or (count_lambdas and isinstance(child, ast.Lambda))
        )
        if counts_as_control_flow and not _is_elif_continuation(node, child):
            child_depth += 1
        deepest = max(
            deepest,
            _max_nesting_depth(
                child,
                child_depth,
                count_comprehensions=count_comprehensions,
                count_lambdas=count_lambdas,
            ),
        )
    return deepest


def _nesting_finding(
    check: NestingDepthCheck,
    symbol: str,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    depth: int,
    max_depth: int,
    file: str,
) -> Finding:
    """Build the excessive-nesting finding for one function."""

    end_line = node.end_lineno if node.end_lineno is not None else node.lineno
    return Finding(
        rule_id=check.rule_id,
        check=check.id,
        category=check.category,
        severity=check.default_severity,
        title="Excessive nesting",
        message=f"{symbol} nests {depth} levels deep (max {max_depth})",
        location=Location(file=file, line=node.lineno, end_line=end_line),
        symbol=symbol,
        properties={"depth": depth, "max": max_depth},
        remediation=Remediation(
            summary=f"Flatten {symbol}",
            detail=(
                "Replace deeply nested conditionals with guard clauses, "
                "early returns, or extracted helper functions."
            ),
        ),
    )


def _file_nesting_findings(
    check: NestingDepthCheck,
    module: ast.Module,
    file: str,
    max_depth: int,
    *,
    count_comprehensions: bool,
    count_lambdas: bool,
) -> list[Finding]:
    """Return the excessive-nesting findings for one parsed module."""

    collector = _FunctionCollector()
    collector.visit(module)
    findings: list[Finding] = []
    for symbol, node in _disambiguate_functions(collector.functions):
        depth = _max_nesting_depth(
            node,
            0,
            count_comprehensions=count_comprehensions,
            count_lambdas=count_lambdas,
        )
        if depth > max_depth:
            findings.append(_nesting_finding(check, symbol, node, depth, max_depth, file))
    return findings


class FunctionLengthCheck:
    """Flag functions longer than a configurable number of source lines."""

    id: str = "maintainability.function_length"
    rule_id: str = "HT-MAINT-001"
    category: Category = Category.MAINTAINABILITY
    default_severity: Severity = Severity.MEDIUM
    api_version: int = CHECK_API_VERSION
    cross_file: bool = False
    file_kinds: frozenset[FileKind] = frozenset({FileKind.PYTHON})

    def supports(self, ctx: CheckContext) -> bool:
        """Run whenever the context contains at least one Python file."""

        return bool(ctx.files_for(self.file_kinds))

    def run(self, ctx: CheckContext) -> CheckResult:
        """Emit a finding for every function exceeding ``max_length`` lines."""

        options = ctx.rule_options(self.id, {"max_length": _DEFAULT_MAX_LENGTH})
        max_length = _int_option(options, "max_length", _DEFAULT_MAX_LENGTH)
        findings: list[Finding] = []
        for path in sorted(ctx.files_for(self.file_kinds)):
            module = ctx.parse_ast(path)
            if module is None:
                continue
            file = _relative_posix(path, ctx.root)
            collector = _FunctionCollector()
            collector.visit(module)
            for symbol, node in _disambiguate_functions(collector.functions):
                end_line = node.end_lineno if node.end_lineno is not None else node.lineno
                length = end_line - node.lineno + 1
                if length <= max_length:
                    continue
                findings.append(
                    Finding(
                        rule_id=self.rule_id,
                        check=self.id,
                        category=self.category,
                        severity=self.default_severity,
                        title="Function too long",
                        message=f"{symbol} is {length} lines long (max {max_length})",
                        location=Location(file=file, line=node.lineno, end_line=end_line),
                        symbol=symbol,
                        properties={"length": length, "max": max_length},
                        remediation=Remediation(
                            summary=f"Split {symbol} into smaller functions",
                            detail=(
                                "Extract cohesive blocks into named helpers so each "
                                "function stays short enough to read and test at a glance."
                            ),
                        ),
                    )
                )
        return _result(self.id, findings)


class NestingDepthCheck:
    """Flag functions whose control-flow nesting exceeds ``max_depth``."""

    id: str = "maintainability.nesting_depth"
    rule_id: str = "HT-MAINT-002"
    category: Category = Category.MAINTAINABILITY
    default_severity: Severity = Severity.MEDIUM
    api_version: int = CHECK_API_VERSION
    cross_file: bool = False
    file_kinds: frozenset[FileKind] = frozenset({FileKind.PYTHON})

    def supports(self, ctx: CheckContext) -> bool:
        """Run whenever the context contains at least one Python file."""

        return bool(ctx.files_for(self.file_kinds))

    def run(self, ctx: CheckContext) -> CheckResult:
        """Emit a finding for every function exceeding ``max_depth`` levels."""

        options = ctx.rule_options(
            self.id,
            {
                "max_depth": _DEFAULT_MAX_DEPTH,
                "count_comprehensions": False,
                "count_lambdas": False,
            },
        )
        max_depth = _int_option(options, "max_depth", _DEFAULT_MAX_DEPTH)
        count_comprehensions = _bool_option(options, "count_comprehensions", False)
        count_lambdas = _bool_option(options, "count_lambdas", False)
        findings: list[Finding] = []
        for path in sorted(ctx.files_for(self.file_kinds)):
            module = ctx.parse_ast(path)
            if module is None:
                continue
            findings.extend(
                _file_nesting_findings(
                    self,
                    module,
                    _relative_posix(path, ctx.root),
                    max_depth,
                    count_comprehensions=count_comprehensions,
                    count_lambdas=count_lambdas,
                )
            )
        return _result(self.id, findings)


class ModuleLengthCheck:
    """Flag modules longer than a configurable number of source lines."""

    id: str = "maintainability.module_length"
    rule_id: str = "HT-MAINT-003"
    category: Category = Category.MAINTAINABILITY
    default_severity: Severity = Severity.LOW
    api_version: int = CHECK_API_VERSION
    cross_file: bool = False
    file_kinds: frozenset[FileKind] = frozenset({FileKind.PYTHON})

    def supports(self, ctx: CheckContext) -> bool:
        """Run whenever the context contains at least one Python file."""

        return bool(ctx.files_for(self.file_kinds))

    def run(self, ctx: CheckContext) -> CheckResult:
        """Emit one finding per file exceeding ``max_lines`` source lines."""

        options = ctx.rule_options(self.id, {"max_lines": _DEFAULT_MAX_LINES})
        max_lines = _int_option(options, "max_lines", _DEFAULT_MAX_LINES)
        findings: list[Finding] = []
        for path in sorted(ctx.files_for(self.file_kinds)):
            module = ctx.parse_ast(path)
            if module is None:
                continue
            lines = _source_lines(ctx.read_text(path))
            if lines <= max_lines:
                continue
            file = _relative_posix(path, ctx.root)
            findings.append(
                Finding(
                    rule_id=self.rule_id,
                    check=self.id,
                    category=self.category,
                    severity=self.default_severity,
                    title="Module too long",
                    message=f"{file} has {lines} source lines (max {max_lines})",
                    location=Location(file=file, line=1),
                    symbol="<module>",
                    properties={"lines": lines, "max": max_lines},
                    remediation=Remediation(
                        summary="Split this module into smaller modules",
                        detail=(
                            "Move cohesive groups of definitions into submodules so "
                            "each file stays navigable."
                        ),
                    ),
                )
            )
        return _result(self.id, findings)
