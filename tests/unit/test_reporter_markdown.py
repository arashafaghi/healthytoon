"""Tests for the Markdown reporter."""

from __future__ import annotations

from healthytoon.core.types import (
    Category,
    CategoryScore,
    CheckOutcome,
    Finding,
    Location,
    Remediation,
    Report,
    Score,
    Severity,
    Status,
    Summary,
    TargetInfo,
    ToolInfo,
)
from healthytoon.reporters import get_reporter
from healthytoon.reporters.markdown import MarkdownReporter


def _finding(
    rule_id: str = "HT-TEST-001",
    *,
    severity: Severity = Severity.MEDIUM,
    title: str = "A test finding",
    message: str = "something looks wrong",
    file: str | None = "pkg/mod.py",
    line: int | None = 12,
    remediation: Remediation | None = None,
    fingerprint: str = "fp00000000000001",
    category: Category = Category.MAINTAINABILITY,
) -> Finding:
    location = Location(file=file, line=line) if file is not None else None
    return Finding(
        rule_id=rule_id,
        check="test.check",
        category=category,
        severity=severity,
        title=title,
        message=message,
        location=location,
        remediation=remediation,
        fingerprint=fingerprint,
    )


def _report(
    findings: tuple[Finding, ...] = (),
    *,
    grade: str = "A",
    overall: float = 100.0,
    by_category: tuple[CategoryScore, ...] = (),
    outcomes: tuple[CheckOutcome, ...] = (),
) -> Report:
    return Report(
        tool=ToolInfo(name="healthytoon", version="0.1.0"),
        target=TargetInfo(path=".", files_scanned=1),
        summary=Summary(
            total=len(findings),
            score=Score(overall=overall, grade=grade, by_category=by_category),
        ),
        findings=findings,
        outcomes=outcomes,
    )


def _render(
    findings: tuple[Finding, ...] = (),
    *,
    grade: str = "A",
    overall: float = 100.0,
    by_category: tuple[CategoryScore, ...] = (),
    outcomes: tuple[CheckOutcome, ...] = (),
) -> str:
    return MarkdownReporter().render(
        _report(
            findings,
            grade=grade,
            overall=overall,
            by_category=by_category,
            outcomes=outcomes,
        )
    )


def test_heading_contains_grade_and_score() -> None:
    text = _render((), grade="B", overall=87.5)
    assert "# Healthytoon Report — Grade B (87.5/100)" in text


def test_summary_table_lists_categories() -> None:
    text = _render(
        (),
        grade="C",
        overall=70.0,
        by_category=(
            CategoryScore(Category.SECURITY, 80.0, 0.25, 1),
            CategoryScore(Category.TESTING, 95.0, 0.2, 0),
        ),
    )
    assert "| Category | Score | Findings |" in text
    assert "| security | 80 | 1 |" in text
    assert "| testing | 95 | 0 |" in text


def test_findings_include_rule_id_severity_and_file() -> None:
    findings = (
        _finding("HT-1", file="pkg/a.py", severity=Severity.HIGH),
        _finding("HT-2", file="pkg/b.py", severity=Severity.INFO),
    )
    text = _render(findings)
    assert "### pkg/a.py" in text
    assert "### pkg/b.py" in text
    assert "| high | HT-1 | pkg/a.py:12 |" in text
    assert "| info | HT-2 | pkg/b.py:12 |" in text


def test_findings_grouped_by_file_in_first_seen_order() -> None:
    findings = (
        _finding("HT-A1", file="pkg/a.py"),
        _finding("HT-B1", file="pkg/b.py"),
        _finding("HT-A2", file="pkg/a.py"),
    )
    text = _render(findings)
    assert text.count("### pkg/a.py") == 1
    assert text.index("### pkg/a.py") < text.index("### pkg/b.py")


def test_message_pipes_are_escaped() -> None:
    text = _render((_finding(message="left | right"),))
    assert "left \\| right" in text


def test_multi_line_message_cannot_break_the_row() -> None:
    text = _render((_finding(message="first\nsecond\r\nthird"),))

    rows = [line for line in text.splitlines() if line.startswith("| medium |")]
    assert rows == ["| medium | HT-TEST-001 | pkg/mod.py:12 | first second third |  |"]
    assert "\r" not in text


def test_checks_section_lists_skipped_and_errored_checks() -> None:
    text = _render(
        (_finding(),),
        outcomes=(
            CheckOutcome("a.check", Status.PASS),
            CheckOutcome("docs.check", Status.SKIP, message="no matching files"),
            CheckOutcome("ci.check", Status.ERROR, message="ValueError: boom"),
        ),
    )

    assert text.endswith("\n")
    assert not text.endswith("\n\n")
    assert "## Checks" in text
    assert text.index("## Checks") < text.index("## Findings")
    assert "- skip: `docs.check` — no matching files" in text
    assert "- error: `ci.check` — ValueError: boom" in text
    assert "a.check" not in text


def test_checks_bullet_stays_on_one_line_with_multi_line_message() -> None:
    text = _render(outcomes=(CheckOutcome("ci.check", Status.ERROR, message="boom\nmore"),))

    bullets = [line for line in text.splitlines() if line.startswith("- error:")]
    assert bullets == ["- error: `ci.check` — boom more"]


def test_checks_section_hidden_when_no_check_was_skipped_or_errored() -> None:
    outcomes = (
        CheckOutcome("a.check", Status.PASS),
        CheckOutcome("b.check", Status.WARN, findings=1),
        CheckOutcome("c.check", Status.FAIL, findings=2),
    )

    text = _render(outcomes=outcomes)

    assert "## Checks" not in text
    assert text == _render()


def test_checks_section_is_deterministic() -> None:
    outcomes = (
        CheckOutcome("ci.check", Status.ERROR, message="ValueError: boom"),
        CheckOutcome("docs.check", Status.SKIP, message="no matching files"),
    )
    reporter = MarkdownReporter()

    assert reporter.render(_report(outcomes=outcomes)) == reporter.render(
        _report(outcomes=outcomes)
    )


def test_remediation_summary_is_included() -> None:
    finding = _finding(remediation=Remediation(summary="Add tests | coverage"))
    text = _render((finding,))
    assert "Add tests \\| coverage" in text


def test_finding_without_location_renders_under_placeholder() -> None:
    text = _render((_finding("HT-1", file=None),))
    assert "### (no location)" in text
    assert "| medium | HT-1 |" in text


def test_single_trailing_newline() -> None:
    text = _render((_finding(),))
    assert text.endswith("\n")
    assert not text.endswith("\n\n")


def test_two_renders_are_byte_identical() -> None:
    findings = (
        _finding("HT-1", file="pkg/a.py", category=Category.SECURITY),
        _finding("HT-2", file="pkg/b.py", message="x | y"),
    )
    reporter = MarkdownReporter()
    report = _report(findings, grade="B", overall=87.5)
    assert reporter.render(report) == reporter.render(report)


def test_empty_report_has_no_findings_table() -> None:
    text = _render(())
    assert "## Findings" not in text
    assert "| Severity |" not in text
    assert "No findings." in text


def test_color_is_ignored() -> None:
    report = _report([_finding()])
    reporter = MarkdownReporter()
    assert reporter.render(report, color=True) == reporter.render(report, color=False)


def test_registry_exposes_markdown_reporter() -> None:
    reporter = get_reporter("markdown")
    assert isinstance(reporter, MarkdownReporter)
    assert reporter.format_id == "markdown"


def test_evidence_renders_fenced_code_block() -> None:
    import dataclasses

    finding = dataclasses.replace(_finding(), evidence="> 12 | boom")
    out = MarkdownReporter().render(_report((finding,)))
    assert "```python" in out
    assert "> 12 | boom" in out
