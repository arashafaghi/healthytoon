"""P0 CI/CD checks: presence, parsing, tests, pinned actions, permissions, secrets.

These checks consume repository-level artifacts rather than a single file kind,
so they declare an empty ``file_kinds`` (the runner treats that as "all files")
and ``cross_file=True``. Every check builds one shared
:class:`~healthytoon.cicd.ir.CIContext` per run via
:func:`~healthytoon.cicd.context.build_context`, then reasons over the platform
neutral IR.

Findings carry repo-relative POSIX paths and a JSON-pointer-like ``pointer`` in
``properties`` instead of a line number, because the tolerant loader discards
line information. Hardcoded-secret findings never contain the matched value.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from healthytoon.cicd.context import build_context
from healthytoon.cicd.ir import ActionRef, CIContext, Job, PipelineFile
from healthytoon.core.context import CheckContext
from healthytoon.core.protocols import CHECK_API_VERSION, CheckResult
from healthytoon.core.types import (
    Category,
    FileKind,
    Finding,
    Location,
    Severity,
    Status,
)

__all__ = [
    "BaseCICheck",
    "WorkflowPresentCheck",
    "WorkflowParseCheck",
    "TestsPresentCheck",
    "ActionsPinnedCheck",
    "PermissionsExplicitCheck",
    "SecretsCheck",
]

#: Action refs that name a moving branch rather than a release: worst case.
_BRANCH_REFS = frozenset({"main", "master", "HEAD"})

#: Commands that run a Python test suite.
_TEST_COMMAND_RE = re.compile(r"\b(?:pytest|unittest|tox|nox|hatch\s+run)\b")

#: Redaction marker used whenever a secret-like value might be echoed.
_REDACTED = "[REDACTED]"

#: High-confidence secret shapes. The match itself is never surfaced.
_SPECIFIC_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("github-token", re.compile(r"ghp_[A-Za-z0-9]{36,}")),
    ("github-pat", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("aws-access-key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)

#: A ``password:``/``token:``-style key whose value is not an ``${{ ... }}``
#: reference. The negative lookbehind keeps ``id-token`` and similar names from
#: matching.
_GENERIC_SECRET_RE = re.compile(
    r"(?i)(?<![\w-])(?:password|token|secret|api[_-]?key)\b\s*[:=]\s*(?!\$\{\{)"
)

#: Whole-text redaction regex; unlike the detector it captures the value tail.
_SPECIFIC_SECRET_TEXT_RE = re.compile(
    r"(?:ghp_[A-Za-z0-9]{36,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)


def redact(text: str) -> str:
    """Return ``text`` with anything secret-looking replaced by :data:`_REDACTED`."""

    text = _SPECIFIC_SECRET_TEXT_RE.sub(_REDACTED, text)
    lines = [_REDACTED if _GENERIC_SECRET_RE.search(line) else line for line in text.splitlines()]
    return "\n".join(lines)


def _secret_kind(line: str) -> str | None:
    """Return the pattern name matching ``line``, or ``None`` when clean."""

    for name, pattern in _SPECIFIC_SECRET_PATTERNS:
        if pattern.search(line):
            return name
    if _GENERIC_SECRET_RE.search(line):
        return "literal-credential"
    return None


def _finding(
    check: BaseCICheck,
    *,
    severity: Severity,
    title: str,
    message: str,
    file: str,
    pointer: str,
    symbol: str | None = None,
    evidence: str | None = None,
    extra: Mapping[str, object] | None = None,
) -> Finding:
    """Build a finding with a boilerplate location and ``pointer`` property."""

    properties: dict[str, object] = {"pointer": pointer}
    if extra:
        properties.update(extra)
    return Finding(
        rule_id=check.rule_id,
        check=check.id,
        category=check.category,
        severity=severity,
        title=title,
        message=message,
        location=Location(file=file, line=None),
        symbol=symbol,
        evidence=evidence,
        properties=properties,
    )


def _sort_key(finding: Finding) -> tuple[str, str, str]:
    """Deterministic per-check order: file, pointer, rule id."""

    file = finding.location.file if finding.location is not None else ""
    return (file, str(finding.properties.get("pointer", "")), finding.rule_id)


class BaseCICheck:
    """Shared plumbing for the repository-level CI/CD checks."""

    id: str = ""
    rule_id: str = ""
    category: Category = Category.CI_CD
    default_severity: Severity = Severity.MEDIUM
    api_version: int = CHECK_API_VERSION
    cross_file: bool = True
    file_kinds: frozenset[FileKind] = frozenset()

    def supports(self, ctx: CheckContext) -> bool:
        """Every CI/CD check is applicable to every repository."""

        return True

    def analyze(self, ci: CIContext) -> list[Finding]:
        """Return this check's findings for ``ci``."""

        raise NotImplementedError

    def run(self, ctx: CheckContext) -> CheckResult:
        """Build the shared context, analyze it, and sort the findings."""

        findings = sorted(self.analyze(build_context(ctx.root, ctx.config)), key=_sort_key)
        status = Status.WARN if findings else Status.PASS
        return CheckResult(check=self.id, findings=tuple(findings), status=status)


class WorkflowPresentCheck(BaseCICheck):
    """Flag a repository with no CI/CD pipeline at all."""

    id: str = "ci.workflow_present"
    rule_id: str = "HT-CI-001"
    default_severity: Severity = Severity.HIGH

    def analyze(self, ci: CIContext) -> list[Finding]:
        if ci.artifacts.pipelines:
            return []
        return [
            _finding(
                self,
                severity=Severity.HIGH,
                title="No CI/CD pipeline detected",
                message="No CI/CD pipeline configuration was found in this repository.",
                file=".",
                pointer="/",
                extra={"pipelines": 0},
            )
        ]


class WorkflowParseCheck(BaseCICheck):
    """Flag pipeline files that the tolerant loader could not parse."""

    id: str = "ci.workflow_parse"
    rule_id: str = "HT-CI-002"
    default_severity: Severity = Severity.HIGH

    def analyze(self, ci: CIContext) -> list[Finding]:
        findings: list[Finding] = []
        for pipeline in ci.artifacts.pipelines:
            if pipeline.parse_error is None:
                continue
            error = redact(pipeline.parse_error)
            findings.append(
                _finding(
                    self,
                    severity=Severity.HIGH,
                    title="Pipeline configuration failed to parse",
                    message=f"{pipeline.path} could not be parsed: {error}",
                    file=pipeline.path,
                    pointer="/",
                )
            )
        return findings


class TestsPresentCheck(BaseCICheck):
    """Flag CI that exists but never runs the test suite."""

    id: str = "ci.tests_present"
    rule_id: str = "HT-CI-003"
    default_severity: Severity = Severity.HIGH

    def analyze(self, ci: CIContext) -> list[Finding]:
        pipelines = ci.artifacts.pipelines
        if not pipelines:
            return []
        if any(_runs_tests(job) for pipeline in pipelines for job in pipeline.jobs):
            return []
        return [
            _finding(
                self,
                severity=Severity.HIGH,
                title="No CI job runs the test suite",
                message=(
                    "No CI job step runs tests (expected pytest, unittest, tox, nox, or hatch run)."
                ),
                file=".",
                pointer="/",
            )
        ]


def _runs_tests(job: Job) -> bool:
    return any(step.run is not None and _TEST_COMMAND_RE.search(step.run) for step in job.steps)


class ActionsPinnedCheck(BaseCICheck):
    """Flag action references that are not pinned to an immutable revision."""

    id: str = "ci.actions_pinned"
    rule_id: str = "HT-CI-004"
    default_severity: Severity = Severity.HIGH

    def analyze(self, ci: CIContext) -> list[Finding]:
        findings: list[Finding] = []
        for pipeline in _github_pipelines(ci):
            for ref, pointer in _action_refs(pipeline):
                finding = _evaluate_action(self, ref, pipeline.path, pointer)
                if finding is not None:
                    findings.append(finding)
        return findings


def _github_pipelines(ci: CIContext) -> list[PipelineFile]:
    """Return only GitHub Actions pipelines for GitHub-specific checks."""

    return [pipeline for pipeline in ci.artifacts.pipelines if pipeline.platform == "github"]


def _action_refs(pipeline: PipelineFile) -> list[tuple[ActionRef, str]]:
    """Return every action reference in ``pipeline`` with its JSON pointer."""

    refs: list[tuple[ActionRef, str]] = []
    for job in pipeline.jobs:
        for action in job.uses:
            refs.append((action, f"{job.pointer}/uses"))
        for step in job.steps:
            if step.action is not None:
                refs.append((step.action, f"{step.pointer}/uses"))
    return refs


def _evaluate_action(
    check: ActionsPinnedCheck, ref: ActionRef, file: str, pointer: str
) -> Finding | None:
    """Classify one action reference; ``None`` means it is acceptable."""

    if ref.is_local:
        return None
    if ref.is_expression:
        return _finding(
            check,
            severity=Severity.INFO,
            title="Action reference cannot be verified statically",
            message=f"Action reference '{ref.raw}' is a workflow expression and cannot be pinned.",
            file=file,
            pointer=pointer,
            extra={"raw": ref.raw},
        )
    if ref.is_docker:
        if "@sha256:" in ref.raw:
            return None
        return _finding(
            check,
            severity=Severity.HIGH,
            title="Docker action is not pinned by digest",
            message=f"Docker action '{ref.raw}' is not pinned with '@sha256:'.",
            file=file,
            pointer=pointer,
            extra={"raw": ref.raw},
        )
    if ref.pinned_to_sha:
        return None
    if ref.ref in _BRANCH_REFS:
        return _finding(
            check,
            severity=Severity.CRITICAL,
            title="Action is pinned to a mutable branch",
            message=f"Action '{ref.raw}' tracks the mutable branch '{ref.ref}'.",
            file=file,
            pointer=pointer,
            extra={"raw": ref.raw, "ref": ref.ref},
        )
    return _finding(
        check,
        severity=Severity.HIGH,
        title="Action is not pinned to a commit SHA",
        message=f"Action '{ref.raw}' is not pinned to a full commit SHA.",
        file=file,
        pointer=pointer,
        extra={"raw": ref.raw, "ref": ref.ref},
    )


class PermissionsExplicitCheck(BaseCICheck):
    """Flag workflows/jobs without explicit token permissions or with ``write-all``."""

    id: str = "ci.permissions_explicit"
    rule_id: str = "HT-CI-005"
    default_severity: Severity = Severity.MEDIUM

    def analyze(self, ci: CIContext) -> list[Finding]:
        findings: list[Finding] = []
        for pipeline in _github_pipelines(ci):
            findings.extend(self._pipeline_findings(pipeline))
        return findings

    def _pipeline_findings(self, pipeline: PipelineFile) -> list[Finding]:
        findings: list[Finding] = []
        workflow_permissions = pipeline.permissions
        workflow_explicit = workflow_permissions is not None
        if _is_write_all(workflow_permissions):
            findings.append(_write_all(self, pipeline.path, "/permissions", "workflow"))
        jobs = pipeline.jobs
        if not workflow_explicit and not jobs:
            findings.append(_missing_permissions(self, pipeline.path, "/permissions", "workflow"))
        for job in jobs:
            if _is_write_all(job.permissions):
                findings.append(_write_all(self, pipeline.path, job.pointer, job.pointer))
            if not workflow_explicit and job.permissions is None:
                findings.append(_missing_permissions(self, pipeline.path, job.pointer, job.pointer))
        return findings


def _is_write_all(permissions: Mapping[str, object] | str | None) -> bool:
    return isinstance(permissions, str) and permissions.strip().lower() == "write-all"


def _write_all(check: PermissionsExplicitCheck, file: str, pointer: str, symbol: str) -> Finding:
    return _finding(
        check,
        severity=Severity.HIGH,
        title="Over-broad token permissions",
        message="Permissions grant 'write-all', the broadest possible token scope.",
        file=file,
        pointer=pointer,
        symbol=symbol,
    )


def _missing_permissions(
    check: PermissionsExplicitCheck, file: str, pointer: str, symbol: str
) -> Finding:
    return _finding(
        check,
        severity=Severity.MEDIUM,
        title="Token permissions are not set explicitly",
        message=(
            "No explicit 'permissions' are declared, so the token falls back to the "
            "repository default."
        ),
        file=file,
        pointer=pointer,
        symbol=symbol,
    )


class SecretsCheck(BaseCICheck):
    """Detect hardcoded secrets in CI YAML; never report the value."""

    id: str = "ci.secrets"
    rule_id: str = "HT-SEC-001"
    category: Category = Category.SECURITY
    default_severity: Severity = Severity.CRITICAL

    def analyze(self, ci: CIContext) -> list[Finding]:
        findings: list[Finding] = []
        for pipeline in ci.artifacts.pipelines:
            findings.extend(self._pipeline_findings(pipeline))
        return findings

    def _pipeline_findings(self, pipeline: PipelineFile) -> list[Finding]:
        findings: list[Finding] = []
        seen_lines: set[int] = set()
        for index, line in enumerate(pipeline.raw_text.splitlines(), start=1):
            if index in seen_lines:
                continue
            kind = _secret_kind(line)
            if kind is None:
                continue
            seen_lines.add(index)
            findings.append(
                _finding(
                    self,
                    severity=Severity.CRITICAL,
                    title="Hardcoded secret detected",
                    message="hardcoded secret detected (value redacted)",
                    file=pipeline.path,
                    pointer="/",
                    symbol=f"line-{index}",
                    extra={"pattern": kind},
                )
            )
        return findings
