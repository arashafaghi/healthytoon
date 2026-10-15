"""Unit tests for :mod:`healthytoon.core.policy`."""

from __future__ import annotations

import pytest

from healthytoon.core.policy import analysis_incomplete, exit_code, fails
from healthytoon.core.types import (
    Category,
    CheckOutcome,
    Finding,
    Report,
    Score,
    Severity,
    Status,
    Summary,
    TargetInfo,
    ToolInfo,
)
from healthytoon.errors import EXIT_FINDINGS, EXIT_INTERNAL, EXIT_OK


def _report(*severities: Severity, outcomes: tuple[CheckOutcome, ...] = ()) -> Report:
    findings = tuple(
        Finding(
            rule_id=f"HT-TEST-{index}",
            check="test.check",
            category=Category.MAINTAINABILITY,
            severity=severity,
            title="title",
            message="message",
        )
        for index, severity in enumerate(severities)
    )
    return Report(
        tool=ToolInfo(version="0.0.0"),
        target=TargetInfo(path=".", files_scanned=0),
        summary=Summary(total=len(findings), score=Score(overall=100.0, grade="A")),
        findings=findings,
        outcomes=outcomes,
    )


def test_fail_on_none_never_fails() -> None:
    report = _report(Severity.CRITICAL, Severity.HIGH)

    assert fails(report, None) is False
    assert exit_code(report, fail_on=None) == EXIT_OK


@pytest.mark.parametrize(
    ("fail_on", "severity", "expected"),
    [
        (Severity.INFO, Severity.INFO, True),
        (Severity.LOW, Severity.INFO, False),
        (Severity.LOW, Severity.LOW, True),
        (Severity.MEDIUM, Severity.LOW, False),
        (Severity.MEDIUM, Severity.MEDIUM, True),
        (Severity.HIGH, Severity.MEDIUM, False),
        (Severity.HIGH, Severity.HIGH, True),
        (Severity.CRITICAL, Severity.HIGH, False),
        (Severity.CRITICAL, Severity.CRITICAL, True),
    ],
)
def test_fail_on_boundary(fail_on: Severity, severity: Severity, expected: bool) -> None:
    report = _report(severity)

    assert fails(report, fail_on) is expected
    assert exit_code(report, fail_on=fail_on) == (EXIT_FINDINGS if expected else EXIT_OK)


def test_any_qualifying_finding_fails() -> None:
    report = _report(Severity.LOW, Severity.MEDIUM, Severity.LOW)

    assert fails(report, Severity.MEDIUM) is True
    assert fails(report, Severity.HIGH) is False


def test_empty_report_returns_ok_at_any_level() -> None:
    report = _report()

    assert fails(report, Severity.INFO) is False
    assert exit_code(report, fail_on=Severity.INFO) == EXIT_OK


def test_error_outcome_returns_internal_even_when_gate_disabled() -> None:
    report = _report(
        Severity.CRITICAL,
        outcomes=(CheckOutcome("broken", Status.ERROR, message="RuntimeError: boom"),),
    )

    assert analysis_incomplete(report) is True
    assert exit_code(report, fail_on=None) == EXIT_INTERNAL
    assert exit_code(report, fail_on=Severity.CRITICAL) == EXIT_INTERNAL


def test_error_outcome_without_findings_returns_internal() -> None:
    report = _report(outcomes=(CheckOutcome("broken", Status.ERROR, message="boom"),))

    assert exit_code(report, fail_on=Severity.INFO) == EXIT_INTERNAL


def test_multiple_error_outcomes_still_return_internal() -> None:
    report = _report(
        Severity.LOW,
        outcomes=(
            CheckOutcome("a", Status.ERROR, message="boom"),
            CheckOutcome("b", Status.ERROR, message="bang"),
        ),
    )

    assert exit_code(report, fail_on=Severity.LOW) == EXIT_INTERNAL


def test_skip_outcomes_do_not_change_exit_code() -> None:
    skipped = CheckOutcome("skipped", Status.SKIP, message="not applicable")
    with_findings = _report(Severity.HIGH, outcomes=(skipped,))
    without_findings = _report(outcomes=(skipped,))

    assert analysis_incomplete(with_findings) is False
    assert exit_code(with_findings, fail_on=Severity.HIGH) == EXIT_FINDINGS
    assert exit_code(with_findings, fail_on=None) == EXIT_OK
    assert exit_code(without_findings, fail_on=Severity.INFO) == EXIT_OK
    assert exit_code(without_findings, fail_on=None) == EXIT_OK
