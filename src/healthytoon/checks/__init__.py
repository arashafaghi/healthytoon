"""Built-in Healthytoon checks.

:mod:`healthytoon.core.registry` imports this package lazily and calls
:func:`builtin_checks`, which must return the bundled checks in a stable order
so reports and tests stay deterministic.
"""

from __future__ import annotations

from healthytoon.checks.annotations import AnnotationCoverageCheck
from healthytoon.checks.complexity import ComplexityCheck
from healthytoon.checks.cycles import ImportCycleCheck
from healthytoon.checks.deadcode import DeadCodeCheck
from healthytoon.checks.docstrings import DocstringCoverageCheck
from healthytoon.checks.duplication import DuplicationCheck
from healthytoon.checks.structure import (
    FunctionLengthCheck,
    ModuleLengthCheck,
    NestingDepthCheck,
)
from healthytoon.core.protocols import Check

__all__ = ["builtin_checks"]


def builtin_checks() -> tuple[Check, ...]:
    """Return the bundled checks in their stable registration order."""

    return (
        FunctionLengthCheck(),
        NestingDepthCheck(),
        ModuleLengthCheck(),
        AnnotationCoverageCheck(),
        DocstringCoverageCheck(),
        ImportCycleCheck(),
        DuplicationCheck(),
        ComplexityCheck(),
        DeadCodeCheck(),
    )
