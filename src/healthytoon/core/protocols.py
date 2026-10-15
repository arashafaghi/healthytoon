"""Extension protocols for checks and reporters.

These are the frozen interfaces that engines (Phase 2) implement and the runner
and CLI consume. Third-party checks are discovered via the ``healthytoon.checks``
entry-point group, so the contract must stay dependency-free and stable.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from healthytoon.core.context import CheckContext
from healthytoon.core.types import Category, FileKind, Finding, Report, Severity, Status

__all__ = ["CHECK_API_VERSION", "CheckResult", "Check", "Reporter"]

#: Incremented on any breaking change to the :class:`Check` contract.
CHECK_API_VERSION = 1


@dataclass(frozen=True, slots=True)
class CheckResult:
    """What a single check reports back to the runner."""

    check: str
    findings: tuple[Finding, ...] = ()
    status: Status = Status.PASS
    score: float | None = None
    metrics: Mapping[str, float] = field(default_factory=lambda: MappingProxyType({}))
    notes: str | None = None


@runtime_checkable
class Check(Protocol):
    """A single audit rule over a :class:`CheckContext`."""

    id: str
    category: Category
    default_severity: Severity
    api_version: int
    cross_file: bool
    file_kinds: frozenset[FileKind]

    def supports(self, ctx: CheckContext) -> bool:
        """Return False when this check is not applicable to ``ctx``."""

    def run(self, ctx: CheckContext) -> CheckResult:
        """Execute the check and return its findings."""


@runtime_checkable
class Reporter(Protocol):
    """Renders a :class:`Report` into a string for a given output format."""

    format_id: str

    def render(self, report: Report, *, color: bool = False) -> str:
        """Return the serialized report; ``color`` requests ANSI styling."""
