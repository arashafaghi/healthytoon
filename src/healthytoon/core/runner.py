"""Sequential check execution: protocol in, deterministic report out.

The runner is the single place where checks are executed, execution status is
recorded, fingerprints are completed, and findings are ordered and
deduplicated so the resulting :class:`~healthytoon.core.types.Report` is a pure
function of the checks' output regardless of the order they were given.

A check that raises is recorded as an ``ERROR`` outcome (never a silent pass);
the policy turns that into a nonzero exit code because the analysis is
incomplete. A check whose inputs are absent is recorded as ``SKIP`` and does
not fail the command.
"""

from __future__ import annotations

import dataclasses
from collections import Counter
from collections.abc import Sequence
from types import MappingProxyType

from healthytoon.core.context import CheckContext
from healthytoon.core.protocols import CHECK_API_VERSION, Check, CheckResult
from healthytoon.core.scoring import build_score
from healthytoon.core.types import (
    Category,
    CheckOutcome,
    FileKind,
    Finding,
    Report,
    Severity,
    Status,
    Summary,
    TargetInfo,
    ToolInfo,
    canonical_sort_key,
    compute_fingerprint,
)

__all__ = ["run_checks"]


def run_checks(
    checks: Sequence[Check],
    ctx: CheckContext,
    *,
    tool: ToolInfo,
    jobs: int = 0,
) -> Report:
    """Run ``checks`` against ``ctx`` and assemble a deterministic report.

    ``jobs`` is accepted for interface compatibility but execution is
    sequential; parallelism arrives in a later phase. ``jobs=0`` means "auto"
    and is not an error.
    """

    findings: list[Finding] = []
    outcomes: list[CheckOutcome] = []

    for check in checks:
        outcome, result = _execute(check, ctx)
        outcomes.append(outcome)
        if result is not None:
            findings.extend(result.findings)

    resolved = [_with_fingerprint(finding) for finding in findings]
    resolved.sort(key=lambda finding: (canonical_sort_key(finding), finding.fingerprint))
    unique = _apply_severity_overrides(_deduplicate(resolved), ctx)

    severity_counts = Counter(finding.severity for finding in unique)
    category_counts = Counter(finding.category for finding in unique)
    summary = Summary(
        total=len(unique),
        score=build_score(unique),
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
    )
    target = TargetInfo(
        path=str(ctx.root),
        files_scanned=len(ctx.files),
        checks_run=tuple(
            sorted(outcome.check for outcome in outcomes if outcome.status is not Status.SKIP)
        ),
    )
    return Report(
        tool=tool,
        target=target,
        summary=summary,
        findings=tuple(unique),
        outcomes=tuple(sorted(outcomes, key=lambda outcome: outcome.check)),
    )


def _execute(check: Check, ctx: CheckContext) -> tuple[CheckOutcome, CheckResult | None]:
    """Run one check, returning its outcome and result (or ``None`` on skip/error)."""

    if check.api_version != CHECK_API_VERSION:
        return (
            CheckOutcome(
                check.id,
                Status.SKIP,
                message=f"incompatible check API version {check.api_version}",
            ),
            None,
        )
    if not check.supports(ctx):
        return CheckOutcome(check.id, Status.SKIP, message="not applicable"), None

    kinds: frozenset[FileKind] = getattr(check, "file_kinds", frozenset())
    if kinds and not ctx.files_for(kinds):
        return CheckOutcome(check.id, Status.SKIP, message="no matching files"), None

    try:
        result = check.run(ctx)
    except Exception as exc:  # noqa: BLE001 - a broken check must not abort the run
        return CheckOutcome(
            check.id,
            Status.ERROR,
            message=f"{type(exc).__name__}: {exc}",
        ), None
    return CheckOutcome(
        check.id,
        result.status,
        findings=len(result.findings),
        message=result.notes,
    ), result


def _with_fingerprint(finding: Finding) -> Finding:
    """Return ``finding`` with a computed fingerprint when it lacks one."""

    if finding.fingerprint:
        return finding
    location = finding.location
    return dataclasses.replace(
        finding,
        fingerprint=compute_fingerprint(
            rule_id=finding.rule_id,
            file=location.file if location is not None else "",
            symbol=finding.symbol,
        ),
    )


def _deduplicate(findings: Sequence[Finding]) -> list[Finding]:
    """Drop findings whose fingerprint was already seen, keeping the first."""

    seen: set[str] = set()
    unique: list[Finding] = []
    for finding in findings:
        if finding.fingerprint in seen:
            continue
        seen.add(finding.fingerprint)
        unique.append(finding)
    return unique


def _apply_severity_overrides(findings: Sequence[Finding], ctx: CheckContext) -> list[Finding]:
    """Overlay configured per-check severity onto findings.

    ``[tool.healthytoon.rules."<check id>"].severity`` overrides the severity a
    check emitted, so users can retune the gate without patching the check.
    """

    rules = ctx.config.rules
    if not rules:
        return list(findings)
    adjusted: list[Finding] = []
    for finding in findings:
        rule = rules.get(finding.check)
        if rule is not None and rule.severity is not None and rule.severity != finding.severity:
            adjusted.append(dataclasses.replace(finding, severity=rule.severity))
        else:
            adjusted.append(finding)
    return adjusted
