"""CI/CD configuration engine.

This package owns the tolerant YAML loader, the platform adapters, the
platform-neutral intermediate representation (IR), and the CI/CD checks that
run against it. :func:`builtin_checks` returns the bundled checks in a stable
registration order (P0 rules first, then P1) so reports and tests stay
deterministic.
"""

from __future__ import annotations

from healthytoon.cicd.checks_p0 import (
    ActionsPinnedCheck,
    PermissionsExplicitCheck,
    SecretsCheck,
    TestsPresentCheck,
    WorkflowParseCheck,
    WorkflowPresentCheck,
)
from healthytoon.cicd.checks_p1 import (
    CoverageGateCheck,
    DependencyUpdatesCheck,
    LockfileCheck,
    PythonConsistencyCheck,
    PythonEolCheck,
    PythonMatrixCheck,
    TriggersPullRequestCheck,
)
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
    """Return the bundled CI/CD checks in their stable registration order."""

    return (
        WorkflowPresentCheck(),
        WorkflowParseCheck(),
        TestsPresentCheck(),
        ActionsPinnedCheck(),
        PermissionsExplicitCheck(),
        SecretsCheck(),
        PythonEolCheck(),
        PythonMatrixCheck(),
        PythonConsistencyCheck(),
        CoverageGateCheck(),
        LockfileCheck(),
        DependencyUpdatesCheck(),
        TriggersPullRequestCheck(),
    )
