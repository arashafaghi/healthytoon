"""Markdown reporter."""

from __future__ import annotations

from healthytoon.core.types import CheckOutcome, Finding, Report, Status

__all__ = ["MarkdownReporter"]


class MarkdownReporter:
    """Render a :class:`Report` as a GitHub-flavoured Markdown document."""

    format_id = "markdown"

    def render(self, report: Report, *, color: bool = False) -> str:
        score = report.summary.score
        parts: list[str] = [
            f"# Healthytoon Report — Grade {score.grade} ({_number(score.overall)}/100)"
        ]
        if score.by_category:
            parts += [
                "",
                "## Summary",
                "",
                "| Category | Score | Findings |",
                "| --- | --- | --- |",
            ]
            parts += [
                f"| {_cell(str(entry.category))} | {_number(entry.score)} | {entry.findings} |"
                for entry in score.by_category
            ]
        parts += _checks_section(report.outcomes)
        if report.findings:
            parts += ["", "## Findings", "", _finding_sections(report.findings)]
        else:
            parts += ["", "No findings."]
        return "\n".join(parts) + "\n"


def _cell(text: str) -> str:
    """Escape pipes and flatten line breaks so text cannot break table rows."""

    return _flatten(text.replace("|", "\\|"))


def _flatten(text: str) -> str:
    """Replace any newline or carriage return with a single space."""

    return text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")


def _checks_section(outcomes: tuple[CheckOutcome, ...]) -> list[str]:
    """List skipped and errored checks.

    Hidden when every check ran (pass/warn/fail): an errored check must be
    visible, while routine skips stay out of healthy reports.
    """

    flagged = [outcome for outcome in outcomes if outcome.status in (Status.SKIP, Status.ERROR)]
    if not flagged:
        return []
    lines = ["", "## Checks", ""]
    for outcome in flagged:
        entry = f"- {outcome.status.value}: `{outcome.check}`"
        if outcome.message:
            entry += f" — {_cell(outcome.message)}"
        lines.append(entry)
    return lines


def _number(value: float) -> str:
    return f"{value:g}"


def _finding_sections(findings: tuple[Finding, ...]) -> str:
    groups: dict[str, list[Finding]] = {}
    for finding in findings:
        key = finding.location.file if finding.location is not None else ""
        groups.setdefault(key, []).append(finding)

    blocks: list[str] = []
    for file_key, group in groups.items():
        heading = file_key if file_key else "(no location)"
        blocks.append(
            "\n".join(
                [
                    f"### {heading}",
                    "",
                    "| Severity | Rule | Location | Message | Remediation |",
                    "| --- | --- | --- | --- | --- |",
                    *(_row(finding) for finding in group),
                ]
            )
        )
    return "\n\n".join(blocks)


def _row(finding: Finding) -> str:
    location = finding.location
    if location is None:
        where = ""
    elif location.line is not None:
        where = f"{location.file}:{location.line}"
    else:
        where = location.file
    remediation = finding.remediation.summary if finding.remediation is not None else ""
    return (
        f"| {finding.severity.name.lower()} "
        f"| {_cell(finding.rule_id)} "
        f"| {_cell(where)} "
        f"| {_cell(finding.message)} "
        f"| {_cell(remediation)} |"
    )
