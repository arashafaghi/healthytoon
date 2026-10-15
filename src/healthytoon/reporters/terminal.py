"""Terminal reporter: human-readable output with optional ANSI colour."""

from __future__ import annotations

from healthytoon.core.types import CheckOutcome, Finding, Report, Severity, Status

__all__ = ["TerminalReporter"]

_RESET = "\x1b[0m"
_RED = "\x1b[31m"

#: ANSI foreground codes per severity, applied only to the severity token.
_SEVERITY_CODES: dict[Severity, str] = {
    Severity.CRITICAL: "\x1b[31m",
    Severity.HIGH: "\x1b[31m",
    Severity.MEDIUM: "\x1b[33m",
    Severity.LOW: "\x1b[39m",
    Severity.INFO: "\x1b[34m",
}

#: Display order for the severity breakdown: most severe first.
_SEVERITY_ORDER: tuple[Severity, ...] = (
    Severity.CRITICAL,
    Severity.HIGH,
    Severity.MEDIUM,
    Severity.LOW,
    Severity.INFO,
)


def _severity_summary(findings: tuple[Finding, ...]) -> str:
    counts: dict[Severity, int] = {severity: 0 for severity in _SEVERITY_ORDER}
    for finding in findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    breakdown = ", ".join(
        f"{counts[severity]} {severity.name.lower()}"
        for severity in _SEVERITY_ORDER
        if counts[severity] > 0
    )
    total = len(findings)
    noun = "finding" if total == 1 else "findings"
    return f"{total} {noun}: {breakdown}"


def _severity_token(severity: Severity, *, color: bool) -> str:
    token = severity.name.upper()
    if not color:
        return token
    return f"{_SEVERITY_CODES[severity]}{token}{_RESET}"


def _finding_line(finding: Finding, *, color: bool) -> str:
    parts: list[str] = []
    location = finding.location
    if location is not None:
        if location.line is not None:
            parts.append(f"{location.file}:{location.line}")
        else:
            parts.append(location.file)
    parts.append(_severity_token(finding.severity, color=color))
    parts.append(finding.rule_id)
    parts.append(finding.message)
    return " ".join(parts)


#: Statuses counted as "ran" in the compact checks line.
_RAN_STATUSES: frozenset[Status] = frozenset({Status.PASS, Status.WARN, Status.FAIL})


def _red(token: str, *, color: bool) -> str:
    if not color:
        return token
    return f"{_RED}{token}{_RESET}"


def _checks_lines(outcomes: tuple[CheckOutcome, ...], *, color: bool) -> list[str]:
    """Compact run accounting plus one visible line per errored check.

    Emitted only when at least one check was skipped or errored: a crashed
    check must never render as an all-green report.
    """

    skipped = sum(1 for outcome in outcomes if outcome.status is Status.SKIP)
    errored = sum(1 for outcome in outcomes if outcome.status is Status.ERROR)
    if skipped == 0 and errored == 0:
        return []
    ran = sum(1 for outcome in outcomes if outcome.status in _RAN_STATUSES)
    lines = [f"checks: {ran} ran, {skipped} skipped, {errored} {_red('errored', color=color)}"]
    for outcome in outcomes:
        if outcome.status is not Status.ERROR:
            continue
        flat = " ".join(outcome.message.split()) if outcome.message else ""
        detail = f": {flat}" if flat else ""
        lines.append(f"{_red('error:', color=color)} {outcome.check}{detail}")
    return lines


class TerminalReporter:
    """Render a :class:`Report` as human-readable, deterministic text."""

    format_id: str = "terminal"

    def render(self, report: Report, *, color: bool = False) -> str:
        """Return the report as plain text ending in exactly one newline.

        Findings keep the canonical order of ``report.findings`` (already
        grouped by file). With ``color=True`` only the severity token of each
        finding line and the ``errored``/``error:`` tokens of the checks
        section are ANSI-styled; with ``color=False`` the output contains
        zero escape sequences.
        """

        score = report.summary.score
        lines = [
            f"{report.tool.name} {report.tool.version} — grade {score.grade} ({score.overall}/100)",
        ]
        findings = report.findings
        if not findings:
            lines.append("No findings.")
        else:
            lines.append(_severity_summary(findings))
            lines.extend(_finding_line(finding, color=color) for finding in findings)
        lines.extend(_checks_lines(report.outcomes, color=color))
        return "\n".join(lines) + "\n"
