"""Typing coverage: public functions with completely annotated signatures."""

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

__all__ = ["AnnotationCoverageCheck"]

#: Share of public functions that must be fully annotated by default.
_DEFAULT_MIN_RATIO = 0.8

#: Parameter names exempt from annotation because the type is implied.
_IMPLICIT_SELF_NAMES = frozenset({"self", "cls"})


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


class AnnotationCoverageCheck(_RatioCoverageCheck):
    """Flag modules where too few public functions have fully annotated signatures."""

    id: str = "typing.annotation_coverage"
    rule_id: str = "HT-TYPE-001"
    category: Category = Category.TYPING

    def run(self, ctx: CheckContext) -> CheckResult:
        """Emit one finding per module below the ``min_ratio`` annotation share."""

        min_ratio = _ratio_option(ctx, self.id, _DEFAULT_MIN_RATIO)
        findings: list[Finding] = []
        for path, module in _parsed_modules(ctx, self.file_kinds):
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
