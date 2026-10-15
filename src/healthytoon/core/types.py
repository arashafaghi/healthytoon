"""Core domain types shared by every Healthytoon engine, check, and reporter.

This module is the frozen contract between subsystems. It intentionally uses
only the standard library so third-party checks can depend on it cheaply.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum, StrEnum
from hashlib import sha1
from types import MappingProxyType

__all__ = [
    "Category",
    "CATEGORY_ORDER",
    "FileKind",
    "Severity",
    "SEVERITY_SARIF_LEVEL",
    "Status",
    "Location",
    "Remediation",
    "Finding",
    "ToolInfo",
    "TargetInfo",
    "CategoryScore",
    "Score",
    "CheckOutcome",
    "Summary",
    "Report",
    "FINGERPRINT_ALGORITHM",
    "compute_fingerprint",
    "canonical_sort_key",
]


class Category(StrEnum):
    """Top-level finding categories. Security is deliberately its own bucket."""

    MAINTAINABILITY = "maintainability"
    TESTING = "testing"
    CI_CD = "ci_cd"
    DOCUMENTATION = "documentation"
    DEPENDENCIES = "dependencies"
    TYPING = "typing"
    PACKAGING = "packaging"
    SECURITY = "security"


CATEGORY_ORDER: tuple[Category, ...] = tuple(Category)


class FileKind(StrEnum):
    """Coarse file classifications a check can declare it consumes.

    Checks expose a set of these via ``Check.file_kinds`` so the runner can skip
    a check when a repository contains none of the kinds it needs (for example,
    a CI/CD check when there are no workflow files). An empty set means "all
    files".
    """

    PYTHON = "python"
    WORKFLOW = "workflow"
    PYPROJECT = "pyproject"
    LOCKFILE = "lockfile"
    REQUIREMENTS = "requirements"
    CONFIG = "config"
    MARKDOWN = "markdown"
    OTHER = "other"


class Severity(IntEnum):
    """Ordered severity. Higher value == more severe."""

    INFO = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4

    @classmethod
    def parse(cls, value: object) -> Severity:
        if isinstance(value, cls):
            return value
        if isinstance(value, bool):
            raise ValueError(f"unknown severity: {value!r}")
        if isinstance(value, int):
            return cls(value)
        if not isinstance(value, str):
            raise ValueError(f"unknown severity: {value!r}")
        try:
            return cls[value.strip().upper()]
        except KeyError:
            raise ValueError(f"unknown severity: {value!r}") from None

    @property
    def sarif_level(self) -> str:
        return SEVERITY_SARIF_LEVEL[self]


SEVERITY_SARIF_LEVEL: Mapping[Severity, str] = MappingProxyType(
    {
        Severity.CRITICAL: "error",
        Severity.HIGH: "error",
        Severity.MEDIUM: "warning",
        Severity.LOW: "note",
        Severity.INFO: "note",
    }
)


class Status(StrEnum):
    """Outcome of a single check against a target."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Location:
    """A location inside the audited tree, with repo-relative POSIX paths."""

    file: str
    line: int | None = None
    column: int | None = None
    end_line: int | None = None


@dataclass(frozen=True, slots=True)
class Remediation:
    """Actionable guidance attached to a finding."""

    summary: str
    detail: str | None = None
    url: str | None = None


@dataclass(frozen=True, slots=True)
class Finding:
    """The atomic unit every check emits and every reporter consumes."""

    rule_id: str
    check: str
    category: Category
    severity: Severity
    title: str
    message: str
    location: Location | None = None
    symbol: str | None = None
    evidence: str | None = None
    remediation: Remediation | None = None
    confidence: float = 1.0
    properties: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))
    fingerprint: str = ""


#: Fingerprint algorithm identifier, surfaced in SARIF partialFingerprints.
FINGERPRINT_ALGORITHM = "healthytoon/v2"


def compute_fingerprint(*, rule_id: str, file: str, symbol: str | None = None) -> str:
    """Return a stable fingerprint from rule id, path, and symbol.

    The basis deliberately excludes line numbers and volatile message text so a
    suppression written into a baseline survives refactors and threshold changes
    (see ROADMAP Phase 2 decision 3). Checks that can emit several findings for
    the same rule in the same file should set ``symbol`` to distinguish them.
    """

    normalized_file = file.replace("\\", "/").strip()
    basis = "\x00".join((rule_id, normalized_file, symbol or ""))
    return sha1(basis.encode("utf-8")).hexdigest()[:16]


def canonical_sort_key(finding: Finding) -> tuple[str, int, int, str, str]:
    """Deterministic total order for findings: file, line, column, rule, title.

    Reporters and the runner rely on this ordering so output is byte-stable
    across runs and machines.
    """

    location = finding.location
    file = location.file if location is not None else ""
    line = location.line if location is not None and location.line is not None else -1
    column = location.column if location is not None and location.column is not None else -1
    return (file.replace("\\", "/"), line, column, finding.rule_id, finding.title)


@dataclass(frozen=True, slots=True)
class ToolInfo:
    version: str
    name: str = "healthytoon"
    schema_version: str = "1.0"


@dataclass(frozen=True, slots=True)
class TargetInfo:
    path: str
    files_scanned: int
    checks_run: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CategoryScore:
    category: Category
    score: float
    weight: float
    findings: int


@dataclass(frozen=True, slots=True)
class Score:
    overall: float
    grade: str
    by_category: tuple[CategoryScore, ...] = ()


@dataclass(frozen=True, slots=True)
class CheckOutcome:
    """Per-check execution record, including checks that were skipped or failed.

    ``status`` is :class:`Status`: ``ERROR`` means the check crashed and the
    analysis is incomplete (a nonzero exit code), ``SKIP`` means it was not
    applicable or its consumed file kinds were absent (informational only).
    """

    check: str
    status: Status
    findings: int = 0
    message: str | None = None


@dataclass(frozen=True, slots=True)
class Summary:
    total: int
    score: Score
    by_severity: Mapping[Severity, int] = field(default_factory=lambda: MappingProxyType({}))
    by_category: Mapping[Category, int] = field(default_factory=lambda: MappingProxyType({}))
    duration_ms: int | None = None


@dataclass(frozen=True, slots=True)
class Report:
    """Complete audit result. ``generated_at`` is None for reproducibility."""

    tool: ToolInfo
    target: TargetInfo
    summary: Summary
    findings: tuple[Finding, ...] = ()
    outcomes: tuple[CheckOutcome, ...] = ()
    generated_at: datetime | None = None

    @property
    def analysis_incomplete(self) -> bool:
        """True when any check errored, meaning the audit is not trustworthy."""

        return any(outcome.status is Status.ERROR for outcome in self.outcomes)
