"""Failure gate policy: turn a report into a CI verdict and process exit code.

The policy is a pure function of the report and the configured ``fail_on``
threshold so CI behaviour can be reasoned about (and tested) without running
any checks.
"""

from __future__ import annotations

from healthytoon.core.types import Report, Severity
from healthytoon.errors import EXIT_FINDINGS, EXIT_INTERNAL, EXIT_OK

__all__ = ["analysis_incomplete", "exit_code", "fails"]


def analysis_incomplete(report: Report) -> bool:
    """True when a check errored, so the audit cannot be trusted as complete."""

    return report.analysis_incomplete


def fails(report: Report, fail_on: Severity | None) -> bool:
    """Return True when any finding is at least as severe as ``fail_on``.

    ``fail_on=None`` means the gate is disabled: nothing ever fails, no matter
    how severe the findings are. The comparison uses :class:`Severity`'s
    ``IntEnum`` ordering, so a finding exactly equal to ``fail_on`` fails.
    """

    if fail_on is None:
        return False
    return any(finding.severity >= fail_on for finding in report.findings)


def exit_code(report: Report, *, fail_on: Severity | None) -> int:
    """Return the process exit code for ``report``.

    Incomplete analysis (a crashed check) takes precedence and returns
    ``EXIT_INTERNAL`` so a broken check can never produce a silent green result.
    Otherwise the finding gate decides: ``EXIT_FINDINGS`` or ``EXIT_OK``.
    """

    if analysis_incomplete(report):
        return EXIT_INTERNAL
    return EXIT_FINDINGS if fails(report, fail_on) else EXIT_OK
