"""SARIF 2.1.0 reporter."""

from __future__ import annotations

import json

from healthytoon.core.types import FINGERPRINT_ALGORITHM, Finding, Report

__all__ = ["SarifReporter"]

_SCHEMA_URI = "https://json.schemastore.org/sarif-2.1.0.json"


class SarifReporter:
    """Render a :class:`Report` as a deterministic SARIF 2.1.0 log."""

    format_id = "sarif"

    def render(self, report: Report, *, color: bool = False) -> str:
        payload = {
            "$schema": _SCHEMA_URI,
            "version": "2.1.0",
            "runs": [
                {
                    "tool": {"driver": _driver(report)},
                    "results": [_result(finding) for finding in report.findings],
                }
            ],
        }
        return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _driver(report: Report) -> dict[str, object]:
    return {
        "name": report.tool.name,
        "version": report.tool.version,
        "rules": _rules(report.findings),
    }


def _rules(findings: tuple[Finding, ...]) -> list[dict[str, object]]:
    """Build deduplicated rule descriptors from the first sighting of each rule id."""

    rules: dict[str, dict[str, object]] = {}
    for finding in findings:
        if finding.rule_id in rules:
            continue
        rule: dict[str, object] = {
            "id": finding.rule_id,
            "name": finding.check,
            "shortDescription": {"text": finding.title},
            "defaultConfiguration": {"level": finding.severity.sarif_level},
        }
        if finding.remediation is not None and finding.remediation.url is not None:
            rule["helpUri"] = finding.remediation.url
        rules[finding.rule_id] = rule
    return [rules[rule_id] for rule_id in sorted(rules)]


def _result(finding: Finding) -> dict[str, object]:
    result: dict[str, object] = {
        "ruleId": finding.rule_id,
        "level": finding.severity.sarif_level,
        "message": {"text": finding.message},
        "partialFingerprints": {FINGERPRINT_ALGORITHM: finding.fingerprint},
    }
    location = finding.location
    if location is not None:
        result["locations"] = [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": location.file.replace("\\", "/")},
                    "region": {
                        "startLine": location.line or 1,
                        "startColumn": location.column or 1,
                    },
                }
            }
        ]
    return result
