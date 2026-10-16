"""Tests for attaching source context to findings."""

from __future__ import annotations

from pathlib import Path

from healthytoon.core.source import CONTEXT_CATEGORIES, attach_context
from healthytoon.core.types import (
    Category,
    Finding,
    Location,
    Report,
    Score,
    Severity,
    Summary,
    TargetInfo,
    ToolInfo,
)


def _finding(
    *,
    file: str = "pkg/mod.py",
    line: int | None = 3,
    category: Category = Category.MAINTAINABILITY,
    rule_id: str = "HT-MAINT-001",
) -> Finding:
    location = Location(file=file, line=line) if file else None
    return Finding(
        rule_id=rule_id,
        check="test.check",
        category=category,
        severity=Severity.LOW,
        title="t",
        message="m",
        location=location,
    )


def _report(findings: tuple[Finding, ...]) -> Report:
    return Report(
        tool=ToolInfo(version="0.1.0"),
        target=TargetInfo(path=".", files_scanned=1),
        summary=Summary(total=len(findings), score=Score(overall=100.0, grade="A")),
        findings=findings,
    )


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_context_snippet_marks_target_line(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/mod.py", "one\ntwo\nthree\nfour\nfive\n")

    report = attach_context(_report((_finding(line=3),)), tmp_path, 1)

    evidence = report.findings[0].evidence
    assert evidence is not None
    assert evidence.splitlines() == ["  2 | two", "> 3 | three", "  4 | four"]


def test_zero_context_is_identity(tmp_path: Path) -> None:
    report = _report((_finding(),))
    assert attach_context(report, tmp_path, 0) is report


def test_non_source_categories_are_not_enriched(tmp_path: Path) -> None:
    _write(tmp_path, ".github/workflows/ci.yml", "on: push\npassword: hunter2\n")
    for category in (Category.CI_CD, Category.SECURITY):
        finding = _finding(file=".github/workflows/ci.yml", line=2, category=category)
        enriched = attach_context(_report((finding,)), tmp_path, 1)
        assert enriched.findings[0].evidence is None


def test_missing_file_is_left_unchanged(tmp_path: Path) -> None:
    report = attach_context(_report((_finding(file="nope/missing.py"),)), tmp_path, 2)
    assert report.findings[0].evidence is None


def test_out_of_range_line_is_left_unchanged(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/mod.py", "one\n")
    report = attach_context(_report((_finding(line=99),)), tmp_path, 1)
    assert report.findings[0].evidence is None


def test_finding_without_line_is_left_unchanged(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/mod.py", "one\ntwo\n")
    report = attach_context(_report((_finding(line=None),)), tmp_path, 1)
    assert report.findings[0].evidence is None


def test_context_is_deterministic(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/mod.py", "one\ntwo\nthree\nfour\n")
    report = _report((_finding(line=2),))
    assert attach_context(report, tmp_path, 1) == attach_context(report, tmp_path, 1)


def test_error_returns_original_invalid_file_is_safe(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").mkdir()  # a directory, not a file
    report = attach_context(_report((_finding(),)), tmp_path, 1)
    assert report.findings[0].evidence is None


def test_context_categories_are_source_only() -> None:
    assert {
        Category.MAINTAINABILITY,
        Category.TYPING,
        Category.DOCUMENTATION,
    } == CONTEXT_CATEGORIES
