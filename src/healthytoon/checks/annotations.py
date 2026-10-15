"""Typing coverage: public functions with completely annotated signatures."""

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

__all__ = ["AnnotationCoverageCheck"]

#: Share of public functions that must be fully annotated by default.
_DEFAULT_MIN_RATIO = 0.8

#: Parameter names exempt from annotation because the type is implied.
_IMPLICIT_SELF_NAMES = frozenset({"self", "cls"})


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


def _public_functions(module: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Return every function whose name does not start with an underscore."""

    public: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in ast.walk(module):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name.startswith("_"):
            continue
        public.append(node)
    return public


def _is_annotated(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True when every parameter (except ``self``/``cls``) and the return is annotated."""

    args = node.args
    positional = [*args.posonlyargs, *args.args]
    parameters = [*positional, *args.kwonlyargs]
    if args.vararg is not None:
        parameters.append(args.vararg)
    if args.kwarg is not None:
        parameters.append(args.kwarg)
    if positional and positional[0].arg in _IMPLICIT_SELF_NAMES:
        parameters = parameters[1:]
    if node.returns is None:
        return False
    return all(parameter.annotation is not None for parameter in parameters)


class AnnotationCoverageCheck:
    """Flag modules where too few public functions have fully annotated signatures."""

    id: str = "typing.annotation_coverage"
    rule_id: str = "HT-TYPE-001"
    category: Category = Category.TYPING
    default_severity: Severity = Severity.LOW
    api_version: int = CHECK_API_VERSION
    cross_file: bool = False
    file_kinds: frozenset[FileKind] = frozenset({FileKind.PYTHON})

    def supports(self, ctx: CheckContext) -> bool:
        """Run whenever the context contains at least one Python file."""

        return bool(ctx.files_for(self.file_kinds))

    def run(self, ctx: CheckContext) -> CheckResult:
        """Emit one finding per module below the ``min_ratio`` annotation share."""

        options = ctx.rule_options(self.id, {"min_ratio": _DEFAULT_MIN_RATIO})
        min_ratio = _float_option(options, "min_ratio", _DEFAULT_MIN_RATIO)
        findings: list[Finding] = []
        for path in sorted(ctx.files_for(self.file_kinds)):
            module = ctx.parse_ast(path)
            if module is None:
                continue
            public = _public_functions(module)
            if not public:
                continue
            annotated = sum(1 for node in public if _is_annotated(node))
            total = len(public)
            if annotated / total >= min_ratio:
                continue
            file = _relative_posix(path, ctx.root)
            findings.append(
                Finding(
                    rule_id=self.rule_id,
                    check=self.id,
                    category=self.category,
                    severity=self.default_severity,
                    title="Low annotation coverage",
                    message=(
                        f"only {annotated}/{total} public functions are annotated "
                        f"(min {min_ratio:.0%})"
                    ),
                    location=Location(file=file, line=1),
                    symbol="<module>",
                    properties={
                        "annotated": annotated,
                        "total": total,
                        "min_ratio": min_ratio,
                    },
                    remediation=Remediation(
                        summary="Annotate public function signatures",
                        detail=(
                            "Annotate every parameter and the return type so readers "
                            "and static checkers can verify usage."
                        ),
                    ),
                )
            )
        return _result(self.id, findings)
