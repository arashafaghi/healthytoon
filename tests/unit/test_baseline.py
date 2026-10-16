"""Unit tests for :mod:`healthytoon.core.baseline`."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType

import pytest

from healthytoon.core.baseline import apply_baseline, load_baseline, write_baseline
from healthytoon.core.scoring import build_score
from healthytoon.core.types import (
    Category,
    CheckOutcome,
    Finding,
    Location,
    Report,
    Severity,
    Status,
    Summary,
    TargetInfo,
    ToolInfo,
    compute_fingerprint,
)
from healthytoon.errors import ConfigError


def _finding(
    rule_id: str,
    file: str,
    *,
    severity: Severity = Severity.HIGH,
    symbol: str | None = None,
) -> Finding:
    return Finding(
        rule_id=rule_id,
        check="fake.check",
        category=Category.MAINTAINABILITY,
        severity=severity,
        title=f"title {rule_id}",
        message=f"message {rule_id}",
        location=Location(file=file),
        symbol=symbol,
        fingerprint=compute_fingerprint(rule_id=rule_id, file=file, symbol=symbol),
    )


def _build_report(findings: Sequence[Finding]) -> Report:
    severity_counts = Counter(finding.severity for finding in findings)
    category_counts = Counter(finding.category for finding in findings)
    summary = Summary(
        total=len(findings),
        score=build_score(findings),
        by_severity=MappingProxyType(
            {
                severity: severity_counts[severity]
                for severity in Severity
                if severity in severity_counts
            }
        ),
        by_category=MappingProxyType(
            {
                category: category_counts[category]
                for category in Category
                if category in category_counts
            }
        ),
        duration_ms=42,
    )
    return Report(
        tool=ToolInfo(version="0.0.0"),
        target=TargetInfo(path=".", files_scanned=1),
        summary=summary,
        findings=tuple(findings),
        outcomes=(CheckOutcome("fake.check", Status.WARN, findings=len(findings)),),
        generated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_write_then_load_round_trip(tmp_path: Path) -> None:
    first = _finding("rule.one", "a.py")
    second = _finding("rule.two", "b.py", symbol="symbol")
    report = _build_report([first, second])
    path = tmp_path / "baseline.json"

    write_baseline(path, report)

    assert load_baseline(path) == frozenset({first.fingerprint, second.fingerprint})


def test_write_baseline_is_deterministic(tmp_path: Path) -> None:
    first = _finding("rule.one", "a.py")
    second = _finding("rule.two", "b.py")
    report = _build_report([first, second])
    path = tmp_path / "baseline.json"

    write_baseline(path, report)
    text = path.read_text(encoding="utf-8")
    write_baseline(path, report)

    assert path.read_text(encoding="utf-8") == text
    assert text.endswith("\n")
    assert not text.endswith("\n\n")
    payload = json.loads(text)
    assert payload["algorithm"] == "healthytoon/v2"
    assert payload["fingerprints"] == sorted([first.fingerprint, second.fingerprint])


def test_write_baseline_skips_empty_fingerprints(tmp_path: Path) -> None:
    finding = _finding("rule.one", "a.py")
    empty = Finding(
        rule_id="rule.empty",
        check="fake.check",
        category=Category.MAINTAINABILITY,
        severity=Severity.LOW,
        title="no fingerprint",
        message="no fingerprint",
    )
    report = _build_report([finding, empty])
    path = tmp_path / "baseline.json"

    write_baseline(path, report)

    assert load_baseline(path) == frozenset({finding.fingerprint})


def test_load_accepts_bare_list(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(["abc", "def"]), encoding="utf-8")

    assert load_baseline(path) == frozenset({"abc", "def"})


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "42",
        '"a string"',
        "{}",
        '{"fingerprints": 3}',
        '{"fingerprints": [1, 2]}',
        "null",
    ],
)
def test_load_invalid_content_raises_config_error(tmp_path: Path, content: str) -> None:
    path = tmp_path / "baseline.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ConfigError):
        load_baseline(path)


def test_load_missing_file_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_baseline(tmp_path / "missing.json")


def test_load_directory_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_baseline(tmp_path)


def test_apply_removes_matching_and_recomputes_summary() -> None:
    keep = _finding("rule.keep", "keep.py", severity=Severity.HIGH)
    drop = _finding("rule.drop", "drop.py", severity=Severity.MEDIUM)
    report = _build_report([keep, drop])

    updated = apply_baseline(report, frozenset({drop.fingerprint}))

    assert updated.findings == (keep,)
    assert updated.summary.total == 1
    assert updated.summary.score == build_score([keep])
    assert updated.summary.by_severity == {Severity.HIGH: 1}
    assert updated.summary.by_category == {Category.MAINTAINABILITY: 1}
    assert updated.summary.duration_ms == 42


def test_apply_preserves_outcomes_and_generated_at() -> None:
    keep = _finding("rule.keep", "keep.py")
    drop = _finding("rule.drop", "drop.py")
    report = _build_report([keep, drop])

    updated = apply_baseline(report, frozenset({drop.fingerprint}))

    assert updated.outcomes == report.outcomes
    assert updated.generated_at == report.generated_at
    assert updated.tool == report.tool
    assert updated.target == report.target


def test_apply_empty_baseline_is_identity() -> None:
    finding = _finding("rule.one", "a.py")
    report = _build_report([finding])

    updated = apply_baseline(report, frozenset())

    assert updated.findings == report.findings
    assert updated.summary == report.summary


def test_apply_all_findings_yields_perfect_score() -> None:
    finding = _finding("rule.one", "a.py")
    report = _build_report([finding])

    updated = apply_baseline(report, frozenset({finding.fingerprint}))

    assert updated.findings == ()
    assert updated.summary.total == 0
    assert updated.summary.score == build_score(())
    assert updated.summary.by_severity == {}
    assert updated.summary.by_category == {}


def test_apply_is_deterministic() -> None:
    keep = _finding("rule.keep", "keep.py")
    drop = _finding("rule.drop", "drop.py")
    report = _build_report([keep, drop])
    suppressed = frozenset({drop.fingerprint})

    assert apply_baseline(report, suppressed) == apply_baseline(report, suppressed)
