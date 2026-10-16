"""CI/CD configuration engine.

This package owns the tolerant YAML loader, the platform adapters, and the
platform-neutral intermediate representation (IR) that CI/CD checks run
against. Checks themselves land in a later phase; :func:`builtin_checks`
currently returns an empty tuple so the registry integration is already wired.
"""

from __future__ import annotations

from healthytoon.cicd.ir import (
    ActionRef,
    CIArtifacts,
    CIContext,
    Job,
    PipelineFile,
    Step,
)
from healthytoon.core.protocols import Check

__all__ = [
    "ActionRef",
    "CIArtifacts",
    "CIContext",
    "Job",
    "PipelineFile",
    "Step",
    "builtin_checks",
]


def builtin_checks() -> tuple[Check, ...]:
    """Return the bundled CI/CD checks in their stable registration order.

    Empty in this phase; the CI/CD checks are added by a later task.
    """

    return ()
