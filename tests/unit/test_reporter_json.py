"""Unit tests for the JSON reporter."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

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
from healthytoon.reporters.json import JsonReporter

_DEFAULT_LOCATION = Location(file="src/pkg/mod.py", line=12, column=3)


def _finding(
    *,
    rule_id: str = "HT-MAINT-001",
    severity: Severity = Severity.MEDIUM,
    category: Category = Category.MAINTAINABILITY,
    location: Location | None = _DEFAULT_LOCATION,
    message: str = "Suboptimal construct",
    remediation: Remediation | None = None,
    properties: Mapping[str, object] | None = None,
) -> Finding:
    return Finding(
        rule_id=rule_id,
        check="example.check",
        category=category,
        severity=severity,
        title="Suboptimal construct",
        message=message,
        location=location,
        remediation=remediation,
        properties=properties if properties is not None else MappingProxyType({}),
        fingerprint="0123456789abcdef",
    )


def _report(
    findings: tuple[Finding, ...] = (),
    *,
    generated_at: datetime | None = None,
    duration_ms: int | None = 42,
    category_scores: tuple[CategoryScore, ...] = (),
    outcomes: tuple[CheckOutcome, ...] = (),
) -> Report:
    by_severity: dict[Severity, int] = {}
    by_category: dict[Category, int] = {}
    for finding in findings:
        by_severity[finding.severity] = by_severity.get(finding.severity, 0) + 1
        by_category[finding.category] = by_category.get(finding.category, 0) + 1
    return Report(
        tool=ToolInfo(version="0.1.0"),
        target=TargetInfo(path=".", files_scanned=3, checks_run=("docs.check", "ci.check")),
        summary=Summary(
            total=len(findings),
            score=Score(overall=92.0, grade="B", by_category=category_scores),
            by_severity=by_severity,
            by_category=by_category,
            duration_ms=duration_ms,
        ),
        findings=findings,
        outcomes=outcomes,
        generated_at=generated_at,
    )


def _assert_tool_and_target(payload: dict[str, Any]) -> None:
    assert payload["tool"] == {"name": "healthytoon", "version": "0.1.0", "schema_version": "1.0"}
    assert payload["target"] == {
        "path": ".",
        "files_scanned": 3,
        "checks_run": ["docs.check", "ci.check"],
    }


def _assert_summary(payload: dict[str, Any]) -> None:
    assert payload["summary"]["total"] == 1
    assert payload["summary"]["duration_ms"] == 42
    assert payload["summary"]["score"]["overall"] == 92.0
    assert payload["summary"]["score"]["grade"] == "B"
    assert payload["summary"]["by_severity"] == {"medium": 1}
    assert payload["summary"]["by_category"] == {"maintainability": 1}


def _assert_finding_entry(entry: dict[str, Any]) -> None:
    assert entry["rule_id"] == "HT-MAINT-001"
    assert entry["check"] == "example.check"
    assert entry["category"] == "maintainability"
    assert entry["symbol"] is None
    assert entry["evidence"] is None
    assert entry["remediation"] is None
    assert entry["confidence"] == 1.0
    assert entry["fingerprint"] == "0123456789abcdef"
    assert entry["location"] == {
        "file": "src/pkg/mod.py",
        "line": 12,
        "column": 3,
        "end_line": None,
    }


def test_output_parses_as_json() -> None:
    payload = json.loads(JsonReporter().render(_report((_finding(),))))

    assert isinstance(payload, dict)
    _assert_tool_and_target(payload)
    _assert_summary(payload)
    _assert_finding_entry(payload["findings"][0])


def test_severity_serialized_in_lower_case() -> None:
    report = _report(
        (
            _finding(rule_id="HT-C-001", severity=Severity.CRITICAL),
            _finding(rule_id="HT-I-001", severity=Severity.INFO),
        )
    )

    payload = json.loads(JsonReporter().render(report))

    assert [entry["severity"] for entry in payload["findings"]] == ["critical", "info"]
    assert set(payload["summary"]["by_severity"]) == {"critical", "info"}


def test_location_is_null_when_finding_has_none() -> None:
    out = JsonReporter().render(_report((_finding(location=None),)))

    payload = json.loads(out)
    assert payload["findings"][0]["location"] is None
    assert '"location": null' in out


def test_output_ends_with_exactly_one_newline() -> None:
    out = JsonReporter().render(_report((_finding(),)))

    assert out.endswith("\n")
    assert not out.endswith("\n\n")


def test_two_renders_are_byte_identical() -> None:
    def build() -> Report:
        return _report((_finding(), _finding(rule_id="HT-SEC-001", severity=Severity.HIGH)))

    first = JsonReporter().render(build())
    second = JsonReporter().render(build())
    assert first == second


def test_generated_at_none_serializes_as_json_null() -> None:
    out = JsonReporter().render(_report(generated_at=None, duration_ms=None))

    payload = json.loads(out)
    assert payload["generated_at"] is None
    assert payload["summary"]["duration_ms"] is None
    assert '"generated_at": null' in out


def test_generated_at_serialized_as_iso_timestamp() -> None:
    instant = datetime(2026, 10, 15, 12, 30, tzinfo=UTC)

    payload = json.loads(JsonReporter().render(_report(generated_at=instant)))

    assert payload["generated_at"] == "2026-10-15T12:30:00+00:00"


def test_remediation_and_properties_serialize_as_nested_objects() -> None:
    finding = _finding(
        remediation=Remediation(
            summary="Add a timeout",
            detail="subprocess.run without timeout can hang forever.",
            url="https://example.com/timeout",
        ),
        properties={"note": "heuristic", "count": 3, "nested": {"tags": ["ci", "flakey"]}},
    )

    payload = json.loads(JsonReporter().render(_report((finding,))))
    entry = payload["findings"][0]

    assert entry["remediation"] == {
        "summary": "Add a timeout",
        "detail": "subprocess.run without timeout can hang forever.",
        "url": "https://example.com/timeout",
    }
    assert entry["properties"] == {
        "note": "heuristic",
        "count": 3,
        "nested": {"tags": ["ci", "flakey"]},
    }


def test_score_by_category_serialized_with_category_value() -> None:
    report = _report(
        (_finding(severity=Severity.HIGH, category=Category.SECURITY, rule_id="HT-SEC-001"),),
        category_scores=(
            CategoryScore(category=Category.SECURITY, score=88.0, weight=2.0, findings=1),
        ),
    )

    payload = json.loads(JsonReporter().render(report))

    assert payload["summary"]["score"]["by_category"] == [
        {"category": "security", "score": 88.0, "weight": 2.0, "findings": 1}
    ]


def test_checks_array_serialized_in_outcome_order() -> None:
    report = _report(
        outcomes=(
            CheckOutcome("alpha.check", Status.PASS, findings=2, message="2 notes"),
            CheckOutcome("beta.check", Status.SKIP, message="no matching files"),
            CheckOutcome("gamma.check", Status.ERROR, message="ValueError: boom"),
            CheckOutcome("delta.check", Status.FAIL, findings=1),
        )
    )

    payload = json.loads(JsonReporter().render(report))

    assert payload["checks"] == [
        {"id": "alpha.check", "status": "pass", "findings": 2, "message": "2 notes"},
        {"id": "beta.check", "status": "skip", "findings": 0, "message": "no matching files"},
        {"id": "gamma.check", "status": "error", "findings": 0, "message": "ValueError: boom"},
        {"id": "delta.check", "status": "fail", "findings": 1, "message": None},
    ]


def test_checks_array_is_empty_without_outcomes() -> None:
    out = JsonReporter().render(_report())

    payload = json.loads(out)
    assert payload["checks"] == []
    assert '"checks": []' in out


def test_checks_and_findings_render_deterministically() -> None:
    def build() -> Report:
        return _report(
            (_finding(),),
            outcomes=(
                CheckOutcome("gamma.check", Status.ERROR, message="ValueError: boom"),
                CheckOutcome("beta.check", Status.SKIP, message="no matching files"),
            ),
        )

    first = JsonReporter().render(build())
    second = JsonReporter().render(build())
    assert first == second
    payload = json.loads(first)
    assert [entry["id"] for entry in payload["checks"]] == ["gamma.check", "beta.check"]
    expected = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    assert first == expected


def test_encoding_is_stable_and_keeps_non_ascii() -> None:
    out = JsonReporter().render(_report((_finding(message="Optimisé — café"),)))

    payload = json.loads(out)
    expected = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    assert out == expected
    assert '"Optimisé — café"' in out


def test_color_flag_is_ignored() -> None:
    report = _report((_finding(),))

    plain = JsonReporter().render(report)
    assert JsonReporter().render(report, color=True) == plain
    assert "\x1b[" not in plain


def test_format_id_and_registry() -> None:
    reporter = JsonReporter()
    assert reporter.format_id == "json"

    registered = get_reporter("json")
    assert isinstance(registered, JsonReporter)
    assert registered.format_id == "json"
