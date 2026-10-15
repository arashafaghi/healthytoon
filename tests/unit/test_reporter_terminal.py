"""Unit tests for the terminal reporter."""

from __future__ import annotations

from types import MappingProxyType

from healthytoon.core.types import (
    Category,
    CheckOutcome,
    Finding,
    Location,
    Report,
    Score,
    Severity,
    Status,
    Summary,
    TargetInfo,
    ToolInfo,
    canonical_sort_key,
)
from healthytoon.reporters import get_reporter
from healthytoon.reporters.terminal import TerminalReporter

_DEFAULT_LOCATION = Location(file="src/pkg/mod.py", line=12, column=3)


def _finding(
    *,
    rule_id: str = "HT-MAINT-001",
    severity: Severity = Severity.MEDIUM,
    category: Category = Category.MAINTAINABILITY,
    location: Location | None = _DEFAULT_LOCATION,
    message: str = "Suboptimal construct",
) -> Finding:
    return Finding(
        rule_id=rule_id,
        check="example.check",
        category=category,
        severity=severity,
        title="Suboptimal construct",
        message=message,
        location=location,
        properties=MappingProxyType({}),
        fingerprint="0123456789abcdef",
    )


def _report(
    findings: tuple[Finding, ...] = (),
    *,
    outcomes: tuple[CheckOutcome, ...] = (),
) -> Report:
    by_severity: dict[Severity, int] = {}
    by_category: dict[Category, int] = {}
    for finding in findings:
        by_severity[finding.severity] = by_severity.get(finding.severity, 0) + 1
        by_category[finding.category] = by_category.get(finding.category, 0) + 1
    return Report(
        tool=ToolInfo(version="0.1.0"),
        target=TargetInfo(path=".", files_scanned=3, checks_run=("docs.check",)),
        summary=Summary(
            total=len(findings),
            score=Score(overall=92.0, grade="B"),
            by_severity=by_severity,
            by_category=by_category,
            duration_ms=42,
        ),
        findings=findings,
        outcomes=outcomes,
    )


def test_header_includes_grade_and_score() -> None:
    out = TerminalReporter().render(_report((_finding(),)))

    assert out.splitlines()[0] == "healthytoon 0.1.0 — grade B (92.0/100)"


def test_severity_summary_line() -> None:
    findings = (
        _finding(rule_id="HT-M-001", severity=Severity.MEDIUM, location=Location("a.py", 1)),
        _finding(rule_id="HT-H-001", severity=Severity.HIGH, location=Location("a.py", 2)),
        _finding(rule_id="HT-M-002", severity=Severity.MEDIUM, location=Location("b.py", 5)),
    )

    out = TerminalReporter().render(_report(findings))

    assert out.splitlines()[1] == "3 findings: 1 high, 2 medium"


def test_severity_summary_only_counts_non_zero_severities() -> None:
    out = TerminalReporter().render(_report((_finding(severity=Severity.LOW),)))

    lines = out.splitlines()
    assert lines[1] == "1 finding: 1 low"
    assert "high" not in out
    assert "medium" not in out


def test_no_findings_message() -> None:
    out = TerminalReporter().render(_report())

    assert out.splitlines() == ["healthytoon 0.1.0 — grade B (92.0/100)", "No findings."]
    assert out.endswith("\n")
    assert not out.endswith("\n\n")


def test_zero_ansi_when_color_disabled() -> None:
    report = _report((_finding(severity=Severity.CRITICAL), _finding(rule_id="HT-I-001")))

    assert "\x1b[" not in TerminalReporter().render(report)
    assert "\x1b[" not in TerminalReporter().render(report, color=False)
    assert "\x1b[" not in TerminalReporter().render(_report())


def test_ansi_present_when_color_enabled() -> None:
    findings = (
        _finding(rule_id="HT-C-001", severity=Severity.CRITICAL),
        _finding(rule_id="HT-H-001", severity=Severity.HIGH),
        _finding(rule_id="HT-M-001", severity=Severity.MEDIUM),
        _finding(rule_id="HT-L-001", severity=Severity.LOW),
        _finding(rule_id="HT-I-001", severity=Severity.INFO),
    )

    out = TerminalReporter().render(_report(findings), color=True)

    assert "\x1b[" in out
    assert "\x1b[31mCRITICAL\x1b[0m" in out
    assert "\x1b[31mHIGH\x1b[0m" in out
    assert "\x1b[33mMEDIUM\x1b[0m" in out
    assert "\x1b[39mLOW\x1b[0m" in out
    assert "\x1b[34mINFO\x1b[0m" in out


def test_two_renders_are_byte_identical() -> None:
    report = _report((_finding(rule_id="HT-A-001"), _finding(rule_id="HT-B-001")))

    plain = TerminalReporter().render(report)
    colored = TerminalReporter().render(report, color=True)
    assert plain == TerminalReporter().render(report)
    assert colored == TerminalReporter().render(report, color=True)


def test_findings_grouped_by_canonical_order() -> None:
    findings = (
        _finding(rule_id="HT-B-001", location=Location(file="b.py", line=1)),
        _finding(rule_id="HT-A-002", location=Location(file="a.py", line=30)),
        _finding(rule_id="HT-A-001", location=Location(file="a.py", line=5)),
        _finding(rule_id="HT-C-001", location=Location(file="c.py")),
    )
    ordered = tuple(sorted(findings, key=canonical_sort_key))

    out = TerminalReporter().render(_report(ordered))
    lines = out.splitlines()[2:]

    assert [line.split()[0].split(":")[0] for line in lines] == ["a.py", "a.py", "b.py", "c.py"]
    assert [line.split()[2] for line in lines] == ["HT-A-001", "HT-A-002", "HT-B-001", "HT-C-001"]
    assert lines[0] == "a.py:5 MEDIUM HT-A-001 Suboptimal construct"
    assert lines[3] == "c.py MEDIUM HT-C-001 Suboptimal construct"


def test_finding_line_location_variants() -> None:
    with_line = _finding(
        rule_id="HT-H-001",
        severity=Severity.HIGH,
        location=Location("src/a.py", 7, 2),
        message="Insecure call",
    )
    without_line = _finding(
        rule_id="HT-D-001",
        location=Location("docs/README.md"),
        message="Missing doc",
    )
    without_location = _finding(rule_id="HT-G-001", location=None, message="Global issue")

    out = TerminalReporter().render(_report((with_line, without_line, without_location)))
    lines = out.splitlines()

    assert lines[2] == "src/a.py:7 HIGH HT-H-001 Insecure call"
    assert lines[3] == "docs/README.md MEDIUM HT-D-001 Missing doc"
    assert lines[4] == "MEDIUM HT-G-001 Global issue"


def test_output_ends_with_exactly_one_newline() -> None:
    out = TerminalReporter().render(_report((_finding(),)))

    assert out.endswith("\n")
    assert not out.endswith("\n\n")


def test_checks_line_counts_ran_skipped_and_errored() -> None:
    outcomes = (
        CheckOutcome("a.check", Status.PASS),
        CheckOutcome("b.check", Status.WARN, findings=1),
        CheckOutcome("c.check", Status.FAIL, findings=2),
        CheckOutcome("d.check", Status.PASS),
        CheckOutcome("e.check", Status.SKIP, message="no matching files"),
        CheckOutcome("f.check", Status.ERROR, message="ValueError: boom"),
    )

    out = TerminalReporter().render(_report((_finding(),), outcomes=outcomes))
    lines = out.splitlines()

    assert lines[0] == "healthytoon 0.1.0 — grade B (92.0/100)"
    assert lines[1] == "1 finding: 1 medium"
    assert lines[2] == "src/pkg/mod.py:12 MEDIUM HT-MAINT-001 Suboptimal construct"
    assert lines[3] == "checks: 4 ran, 1 skipped, 1 errored"
    assert lines[4] == "error: f.check: ValueError: boom"


def test_errored_check_is_listed_even_without_findings() -> None:
    out = TerminalReporter().render(
        _report(outcomes=(CheckOutcome("ci.check", Status.ERROR, message="ValueError: boom"),)),
    )

    assert out.splitlines() == [
        "healthytoon 0.1.0 — grade B (92.0/100)",
        "No findings.",
        "checks: 0 ran, 0 skipped, 1 errored",
        "error: ci.check: ValueError: boom",
    ]
    assert out.endswith("\n")
    assert not out.endswith("\n\n")


def test_errored_check_without_message_still_listed() -> None:
    out = TerminalReporter().render(_report(outcomes=(CheckOutcome("ci.check", Status.ERROR),)))

    assert out.splitlines()[-1] == "error: ci.check"


def test_skips_counted_but_not_listed_as_errors() -> None:
    out = TerminalReporter().render(
        _report(
            outcomes=(
                CheckOutcome("a.check", Status.PASS),
                CheckOutcome("docs.check", Status.SKIP, message="no matching files"),
            ),
        ),
    )

    assert out.splitlines()[-1] == "checks: 1 ran, 1 skipped, 0 errored"
    assert "error:" not in out


def test_checks_section_hidden_when_all_checks_ran() -> None:
    with_outcomes = TerminalReporter().render(
        _report(
            (_finding(),),
            outcomes=(
                CheckOutcome("a.check", Status.PASS),
                CheckOutcome("b.check", Status.WARN, findings=1),
            ),
        )
    )

    assert "checks:" not in with_outcomes
    assert with_outcomes == TerminalReporter().render(_report((_finding(),)))


def test_checks_section_is_ansi_free_when_color_disabled() -> None:
    report = _report(
        (_finding(severity=Severity.CRITICAL),),
        outcomes=(
            CheckOutcome("a.check", Status.SKIP, message="not applicable"),
            CheckOutcome("b.check", Status.ERROR, message="ValueError: boom"),
        ),
    )

    out = TerminalReporter().render(report, color=False)

    assert "\x1b[" not in out
    assert "checks: 0 ran, 1 skipped, 1 errored" in out
    assert "error: b.check: ValueError: boom" in out


def test_color_stylizes_errored_and_error_tokens() -> None:
    report = _report(
        outcomes=(
            CheckOutcome("a.check", Status.PASS),
            CheckOutcome("b.check", Status.ERROR, message="ValueError: boom"),
        ),
    )

    out = TerminalReporter().render(report, color=True)

    assert "checks: 1 ran, 0 skipped, 1 \x1b[31merrored\x1b[0m" in out
    assert "\x1b[31merror:\x1b[0m b.check: ValueError: boom" in out


def test_error_message_stays_on_one_line_and_output_ends_with_one_newline() -> None:
    out = TerminalReporter().render(
        _report(
            outcomes=(CheckOutcome("ci.check", Status.ERROR, message="ValueError: line1\nline2"),)
        )
    )

    assert out.splitlines()[-1] == "error: ci.check: ValueError: line1 line2"
    assert out.endswith("\n")
    assert not out.endswith("\n\n")


def test_format_id_and_registry() -> None:
    reporter = TerminalReporter()
    assert reporter.format_id == "terminal"

    registered = get_reporter("terminal")
    assert isinstance(registered, TerminalReporter)
    assert registered.format_id == "terminal"
