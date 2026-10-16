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

import dataclasses
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

#: Key name in a YAML duplicate-key error. The error text also embeds the value,
#: so only this capture group may ever be surfaced.
_DUPLICATE_KEY_RE = re.compile(r'duplicate key\s+"([^"]*)"')

#: The ``(line N)`` suffix the tolerant loader appends to a loader error.
_LINE_RE = re.compile(r"\(line (\d+)\)")


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


def _redact_value(value: object) -> object:
    """Return ``value`` with secret-looking strings replaced, recursively.

    Only strings, mappings, and sequences are traversed; every other value (for
    example an ``int`` or ``bool``) is returned unchanged.
    """

    if isinstance(value, str):
        return redact(value)
    if isinstance(value, Mapping):
        return {key: _redact_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_value(item) for item in value]
    return value


def _sanitize_finding(finding: Finding) -> Finding:
    """Return ``finding`` with every free-text field passed through :func:`redact`.

    This is the single choke point that structurally guarantees no CI/CD check
    can leak a secret through ``title``, ``message``, ``symbol``, ``evidence``,
    or any string value reachable from ``properties``.
    """

    return dataclasses.replace(
        finding,
        title=redact(finding.title),
        message=redact(finding.message),
        symbol=None if finding.symbol is None else redact(finding.symbol),
        evidence=None if finding.evidence is None else redact(finding.evidence),
        properties={key: _redact_value(value) for key, value in finding.properties.items()},
    )


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
        """Build the shared context, analyze it, sanitize, and sort the findings."""

        raw = self.analyze(build_context(ctx.root, ctx.config))
        findings = sorted((_sanitize_finding(finding) for finding in raw), key=_sort_key)
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
            summary = _parse_error_summary(pipeline.parse_error)
            findings.append(
                _finding(
                    self,
                    severity=Severity.HIGH,
                    title="Pipeline configuration failed to parse",
                    message=f"{pipeline.path}: {summary}",
                    file=pipeline.path,
                    pointer="/",
                )
            )
        return findings


def _parse_error_summary(error: str) -> str:
    """Return a safe, value-free summary of a tolerant-loader error.

    Duplicate-key errors embed both the key and its value in the raw text (for
    example ``found duplicate key "password" with value "hunter2secret"``), so
    only the key name and line number may ever be surfaced. Any other error is
    reduced to a generic message plus its line, if known.
    """

    line_match = _LINE_RE.search(error)
    suffix = f" (line {line_match.group(1)})" if line_match is not None else ""
    duplicate = _DUPLICATE_KEY_RE.search(error)
    if duplicate is not None:
        return f"duplicate key '{duplicate.group(1)}'{suffix}"
    return f"pipeline configuration could not be parsed{suffix}"


class TestsPresentCheck(BaseCICheck):
    """Flag CI that exists but never runs the test suite."""

    id: str = "ci.tests_present"
    rule_id: str = "HT-CI-003"
    default_severity: Severity = Severity.HIGH

    def analyze(self, ci: CIContext) -> list[Finding]:
        pipelines = ci.artifacts.pipelines
        if not pipelines:
            return []
        jobs = [job for pipeline in pipelines for job in pipeline.jobs]
        if not jobs:
            # A pipeline exists but exposes no jobs (for example a presence-only
            # Jenkinsfile), so there is nothing to inspect and no finding.
            return []
        if any(_runs_tests(job) for job in jobs):
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


def _action_finding(
    check: ActionsPinnedCheck,
    ref: ActionRef,
    file: str,
    pointer: str,
    *,
    severity: Severity,
    title: str,
    message: str,
    include_ref: bool = False,
) -> Finding:
    """Build an action finding with redacted text and a unique ``symbol``.

    ``pointer`` doubles as the finding's ``symbol`` so several references in one
    file do not collapse to a single fingerprint in the runner.
    """

    raw = redact(ref.raw)
    extra: dict[str, object] = {"raw": raw}
    if include_ref:
        extra["ref"] = None if ref.ref is None else redact(ref.ref)
    return _finding(
        check,
        severity=severity,
        title=title,
        message=message,
        file=file,
        pointer=pointer,
        symbol=pointer,
        extra=extra,
    )


def _evaluate_action(
    check: ActionsPinnedCheck, ref: ActionRef, file: str, pointer: str
) -> Finding | None:
    """Classify one action reference; ``None`` means it is acceptable.

    ``ref.raw`` (and ``ref.ref``) are redacted before they reach any text field.
    """

    if ref.is_local:
        return None
    if ref.is_expression:
        return _expression_finding(check, ref, file, pointer)
    if ref.is_docker:
        return _docker_finding(check, ref, file, pointer)
    if ref.pinned_to_sha:
        return None
    return _unpinned_finding(check, ref, file, pointer)


def _expression_finding(
    check: ActionsPinnedCheck, ref: ActionRef, file: str, pointer: str
) -> Finding:
    raw = redact(ref.raw)
    return _action_finding(
        check,
        ref,
        file,
        pointer,
        severity=Severity.INFO,
        title="Action reference cannot be verified statically",
        message=f"Action reference '{raw}' is a workflow expression and cannot be pinned.",
    )


def _docker_finding(
    check: ActionsPinnedCheck, ref: ActionRef, file: str, pointer: str
) -> Finding | None:
    raw = redact(ref.raw)
    if "@sha256:" in raw:
        return None
    return _action_finding(
        check,
        ref,
        file,
        pointer,
        severity=Severity.HIGH,
        title="Docker action is not pinned by digest",
        message=f"Docker action '{raw}' is not pinned with '@sha256:'.",
    )


def _unpinned_finding(
    check: ActionsPinnedCheck, ref: ActionRef, file: str, pointer: str
) -> Finding:
    raw = redact(ref.raw)
    if ref.ref in _BRANCH_REFS:
        return _action_finding(
            check,
            ref,
            file,
            pointer,
            severity=Severity.CRITICAL,
            title="Action is pinned to a mutable branch",
            message=f"Action '{raw}' tracks the mutable branch '{ref.ref}'.",
            include_ref=True,
        )
    return _action_finding(
        check,
        ref,
        file,
        pointer,
        severity=Severity.HIGH,
        title="Action is not pinned to a commit SHA",
        message=f"Action '{raw}' is not pinned to a full commit SHA.",
        include_ref=True,
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
