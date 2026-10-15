"""Tests for the SARIF 2.1.0 reporter."""

from __future__ import annotations

import json
from typing import Any

import pytest

from healthytoon.core.types import (
    FINGERPRINT_ALGORITHM,
    Category,
    CategoryScore,
    Finding,
    Location,
    Remediation,
    Report,
    Score,
    Severity,
    Summary,
    TargetInfo,
    ToolInfo,
)
from healthytoon.reporters import get_reporter
from healthytoon.reporters.sarif import SarifReporter


def _finding(
    rule_id: str = "HT-TEST-001",
    *,
    severity: Severity = Severity.MEDIUM,
    title: str = "A test finding",
    message: str = "something looks wrong",
    file: str | None = "pkg/mod.py",
    line: int | None = 12,
    column: int | None = None,
    remediation: Remediation | None = None,
    fingerprint: str = "fp00000000000001",
    category: Category = Category.MAINTAINABILITY,
) -> Finding:
    location = Location(file=file, line=line, column=column) if file is not None else None
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
) -> Report:
    return Report(
        tool=ToolInfo(name="healthytoon", version="0.1.0"),
        target=TargetInfo(path=".", files_scanned=1),
        summary=Summary(
            total=len(findings),
            score=Score(overall=overall, grade=grade, by_category=by_category),
        ),
        findings=findings,
    )


def _payload(findings: tuple[Finding, ...]) -> Any:
    return json.loads(SarifReporter().render(_report(findings)))


def _run(findings: tuple[Finding, ...]) -> Any:
    return _payload(findings)["runs"][0]


def test_top_level_document_shape() -> None:
    report = _report([_finding()])
    payload = json.loads(SarifReporter().render(report))
    assert payload["$schema"] == "https://json.schemastore.org/sarif-2.1.0.json"
    assert payload["version"] == "2.1.0"
    assert len(payload["runs"]) == 1
    driver = payload["runs"][0]["tool"]["driver"]
    assert driver["name"] == "healthytoon"
    assert driver["version"] == "0.1.0"


@pytest.mark.parametrize(
    ("severity", "expected"),
    [
        (Severity.CRITICAL, "error"),
        (Severity.HIGH, "error"),
        (Severity.MEDIUM, "warning"),
        (Severity.LOW, "note"),
        (Severity.INFO, "note"),
    ],
)
def test_severity_level_mapping(severity: Severity, expected: str) -> None:
    findings = tuple(_finding(f"HT-{item.name}", severity=item) for item in Severity)
    results = {result["ruleId"]: result for result in _run(findings)["results"]}
    assert results[f"HT-{severity.name}"]["level"] == expected
    rules = {rule["id"]: rule for rule in _run(findings)["tool"]["driver"]["rules"]}
    assert rules[f"HT-{severity.name}"]["defaultConfiguration"]["level"] == expected


def test_partial_fingerprints_present() -> None:
    finding = _finding(fingerprint="deadbeefdeadbeef")
    result = _run((finding,))["results"][0]
    assert FINGERPRINT_ALGORITHM == "healthytoon/v2"
    assert result["partialFingerprints"] == {"healthytoon/v2": "deadbeefdeadbeef"}


def test_partial_fingerprint_key_is_not_stale_v1() -> None:
    result = _run((_finding(fingerprint="deadbeefdeadbeef"),))["results"][0]
    assert "healthytoon/v1" not in result["partialFingerprints"]
    assert list(result["partialFingerprints"]) == [FINGERPRINT_ALGORITHM]


def test_finding_without_location_omits_locations() -> None:
    result = _run((_finding(file=None),))["results"][0]
    assert "locations" not in result


def test_location_region_defaults_to_one() -> None:
    result = _run((_finding(line=None, column=None),))["results"][0]
    region = result["locations"][0]["physicalLocation"]["region"]
    assert region == {"startLine": 1, "startColumn": 1}


def test_windows_path_is_normalized_to_posix() -> None:
    result = _run((_finding(file="pkg\\sub\\mod.py"),))["results"][0]
    uri = result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
    assert uri == "pkg/sub/mod.py"


def test_rules_are_deduplicated_sorted_and_first_wins() -> None:
    findings = (
        _finding("HT-B", title="second"),
        _finding("HT-A", title="first"),
        _finding(
            "HT-B",
            title="third",
            remediation=Remediation(summary="fix it", url="https://example.com/fix"),
        ),
    )
    rules = _run(findings)["tool"]["driver"]["rules"]
    assert [rule["id"] for rule in rules] == ["HT-A", "HT-B"]
    by_id = {rule["id"]: rule for rule in rules}
    assert by_id["HT-A"]["shortDescription"] == {"text": "first"}
    assert by_id["HT-B"]["shortDescription"] == {"text": "second"}
    assert "helpUri" not in by_id["HT-B"]


def test_rule_help_uri_from_remediation() -> None:
    finding = _finding(remediation=Remediation(summary="fix it", url="https://example.com/x"))
    rule = _run((finding,))["tool"]["driver"]["rules"][0]
    assert rule["helpUri"] == "https://example.com/x"


def test_results_preserve_findings_order() -> None:
    findings = (_finding("HT-1"), _finding("HT-2"), _finding("HT-3"))
    results = _run(findings)["results"]
    assert [result["ruleId"] for result in results] == ["HT-1", "HT-2", "HT-3"]


def test_single_trailing_newline() -> None:
    text = SarifReporter().render(_report([_finding()]))
    assert text.endswith("\n")
    assert not text.endswith("\n\n")


def test_two_renders_are_byte_identical() -> None:
    report = _report(
        (
            _finding("HT-1", category=Category.SECURITY),
            _finding("HT-2", remediation=Remediation(summary="fix", url="https://e.x/y")),
        ),
        grade="B",
        overall=87.5,
        by_category=(CategoryScore(Category.SECURITY, 80.0, 0.2, 1),),
    )
    reporter = SarifReporter()
    assert reporter.render(report) == reporter.render(report)


def test_color_is_ignored() -> None:
    report = _report([_finding()])
    reporter = SarifReporter()
    assert reporter.render(report, color=True) == reporter.render(report, color=False)


def test_registry_exposes_sarif_reporter() -> None:
    reporter = get_reporter("sarif")
    assert isinstance(reporter, SarifReporter)
    assert reporter.format_id == "sarif"
