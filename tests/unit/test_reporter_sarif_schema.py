"""Validate SARIF reporter output against the official SARIF 2.1.0 JSON Schema.

The schema is vendored once at ``tests/fixtures/schemas/sarif-2.1.0.json`` from
the OASIS errata release (``sarif-schema-2.1.0.json``, JSON Schema draft-04) and
is never fetched at test time (ROADMAP T1.3 acceptance).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from healthytoon.core.types import (
    Category,
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
from healthytoon.reporters.sarif import SarifReporter

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "schemas" / "sarif-2.1.0.json"


def _load_schema() -> Any:
    with SCHEMA_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def sarif_schema() -> Any:
    return _load_schema()


def _report(
    findings: tuple[Finding, ...] = (),
    *,
    outcomes: tuple[CheckOutcome, ...] = (),
) -> Report:
    return Report(
        tool=ToolInfo(name="healthytoon", version="0.1.0"),
        target=TargetInfo(path=".", files_scanned=3),
        summary=Summary(
            total=len(findings),
            score=Score(overall=80.0, grade="B"),
        ),
        findings=findings,
        outcomes=outcomes,
    )


def _finding(
    rule_id: str,
    *,
    severity: Severity,
    file: str | None = "pkg/mod.py",
    line: int | None = 12,
    end_line: int | None = None,
    remediation: Remediation | None = None,
) -> Finding:
    location = Location(file=file, line=line, end_line=end_line) if file is not None else None
    return Finding(
        rule_id=rule_id,
        check="schema.check",
        category=Category.MAINTAINABILITY,
        severity=severity,
        title=f"title for {rule_id}",
        message=f"message for {rule_id}",
        location=location,
        remediation=remediation,
        fingerprint="deadbeefdeadbeef",
    )


def _validate(payload_text: str, schema: Any) -> list[str]:
    document = json.loads(payload_text)
    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)
    validator = validator_cls(schema)
    return sorted(error.message for error in validator.iter_errors(document))


def test_vendored_schema_is_the_official_sarif_210_schema() -> None:
    schema = _load_schema()
    assert schema["title"] == ("Static Analysis Results Format (SARIF) Version 2.1.0 JSON Schema")
    assert schema["$schema"] == "http://json-schema.org/draft-04/schema#"
    assert schema["properties"]["version"]["enum"] == ["2.1.0"]


def test_report_with_located_findings_validates(sarif_schema: Any) -> None:
    findings = (
        _finding(
            "HT-001",
            severity=Severity.HIGH,
            remediation=Remediation(summary="fix it", url="https://example.com/fix"),
        ),
        _finding("HT-002", severity=Severity.MEDIUM, file="other.py", line=None),
        _finding("HT-003", severity=Severity.INFO, file=None, line=None),
    )

    errors = _validate(SarifReporter().render(_report(findings)), sarif_schema)

    assert errors == []


def test_report_without_findings_validates(sarif_schema: Any) -> None:
    errors = _validate(SarifReporter().render(_report()), sarif_schema)
    assert errors == []


def test_report_with_error_and_skip_outcomes_validates(sarif_schema: Any) -> None:
    findings = (
        _finding(
            "HT-001",
            severity=Severity.HIGH,
            line=10,
            end_line=14,
            remediation=Remediation(summary="fix it", url="https://example.com/fix"),
        ),
        _finding("HT-002", severity=Severity.LOW, file="other.py", line=None),
    )
    outcomes = (
        CheckOutcome(check="HT-001", status=Status.ERROR, message="check crashed"),
        CheckOutcome(check="HT-002", status=Status.SKIP),
        CheckOutcome(check="HT-003", status=Status.PASS),
    )

    errors = _validate(SarifReporter().render(_report(findings, outcomes=outcomes)), sarif_schema)

    assert errors == []
