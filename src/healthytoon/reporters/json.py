"""JSON reporter: deterministic, machine-readable rendering of a report."""

from __future__ import annotations

import json
from collections.abc import Mapping

from healthytoon.core.types import CheckOutcome, Finding, Report

__all__ = ["JsonReporter"]


def _plain(value: object) -> object:
    """Return ``value`` with mappings and sequences replaced by plain containers."""

    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    return value


def _location_payload(finding: Finding) -> dict[str, object] | None:
    location = finding.location
    if location is None:
        return None
    return {
        "file": location.file,
        "line": location.line,
        "column": location.column,
        "end_line": location.end_line,
    }


def _remediation_payload(finding: Finding) -> dict[str, object] | None:
    remediation = finding.remediation
    if remediation is None:
        return None
    return {
        "summary": remediation.summary,
        "detail": remediation.detail,
        "url": remediation.url,
    }


def _finding_payload(finding: Finding) -> dict[str, object]:
    return {
        "rule_id": finding.rule_id,
        "check": finding.check,
        "category": finding.category.value,
        "severity": finding.severity.name.lower(),
        "title": finding.title,
        "message": finding.message,
        "location": _location_payload(finding),
        "symbol": finding.symbol,
        "evidence": finding.evidence,
        "remediation": _remediation_payload(finding),
        "confidence": finding.confidence,
        "properties": {key: _plain(item) for key, item in finding.properties.items()},
        "fingerprint": finding.fingerprint,
    }


def _check_payload(outcome: CheckOutcome) -> dict[str, object]:
    return {
        "id": outcome.check,
        "status": outcome.status.value,
        "findings": outcome.findings,
        "message": outcome.message,
    }


def _report_payload(report: Report) -> dict[str, object]:
    tool = report.tool
    target = report.target
    summary = report.summary
    score = summary.score
    return {
        "tool": {
            "name": tool.name,
            "version": tool.version,
            "schema_version": tool.schema_version,
        },
        "target": {
            "path": target.path,
            "files_scanned": target.files_scanned,
            "checks_run": list(target.checks_run),
        },
        "summary": {
            "total": summary.total,
            "score": {
                "overall": score.overall,
                "grade": score.grade,
                "by_category": [
                    {
                        "category": entry.category.value,
                        "score": entry.score,
                        "weight": entry.weight,
                        "findings": entry.findings,
                    }
                    for entry in score.by_category
                ],
            },
            "by_severity": {
                severity.name.lower(): count for severity, count in summary.by_severity.items()
            },
            "by_category": {
                category.value: count for category, count in summary.by_category.items()
            },
            "duration_ms": summary.duration_ms,
        },
        "findings": [_finding_payload(finding) for finding in report.findings],
        "checks": [_check_payload(outcome) for outcome in report.outcomes],
        "generated_at": (
            report.generated_at.isoformat() if report.generated_at is not None else None
        ),
    }


class JsonReporter:
    """Serialize a :class:`Report` as stable, diff-friendly JSON text."""

    format_id: str = "json"

    def render(self, report: Report, *, color: bool = False) -> str:
        """Return the report as JSON ending in exactly one newline.

        ``color`` is accepted for protocol compatibility and ignored: JSON
        output never carries ANSI escapes. The encoding is fully deterministic
        (sorted keys, no timestamps beyond ``report.generated_at``).
        """

        payload = _report_payload(report)
        return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
