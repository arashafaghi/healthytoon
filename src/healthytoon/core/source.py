"""Attach source-code context to findings.

When the user enables context (``--context N`` or ``[tool.healthytoon.output]
context``), each source-code finding gains a short numbered excerpt of the file
around the reported line so the report shows *where* in the code the issue is.

Only source-code categories are enriched. CI/CD, security, dependency, and
packaging findings are deliberately skipped: their locations may point at YAML
or config that can contain secrets, and re-reading those files into the report
would defeat the redaction guarantees.
"""

from __future__ import annotations

import dataclasses
import io
import tokenize
from pathlib import Path

from healthytoon.core.types import Category, Finding, Report

__all__ = ["attach_context", "CONTEXT_CATEGORIES"]

#: Categories whose findings point at Python source and may be shown verbatim.
CONTEXT_CATEGORIES: frozenset[Category] = frozenset(
    {Category.MAINTAINABILITY, Category.TYPING, Category.DOCUMENTATION}
)


def attach_context(report: Report, root: Path, context_lines: int) -> Report:
    """Return ``report`` with source excerpts attached to eligible findings.

    ``context_lines <= 0`` disables the feature and returns ``report`` unchanged.
    Findings without a usable location, in an ineligible category, or whose file
    cannot be read are returned unchanged. Ordering is preserved and the result
    is deterministic for a given tree.
    """

    if context_lines <= 0:
        return report

    cache: dict[str, tuple[str, ...] | None] = {}
    enriched = tuple(_enrich(finding, root, context_lines, cache) for finding in report.findings)
    return dataclasses.replace(report, findings=enriched)


def _enrich(
    finding: Finding,
    root: Path,
    context_lines: int,
    cache: dict[str, tuple[str, ...] | None],
) -> Finding:
    location = finding.location
    if location is None or location.line is None or finding.category not in CONTEXT_CATEGORIES:
        return finding
    lines = _read_lines(root / location.file, cache)
    if lines is None:
        return finding
    snippet = _snippet(lines, location.line, context_lines)
    if snippet is None:
        return finding
    return dataclasses.replace(finding, evidence=snippet)


def _read_lines(path: Path, cache: dict[str, tuple[str, ...] | None]) -> tuple[str, ...] | None:
    """Read ``path`` as decoded source lines, memoized per call."""

    key = str(path)
    if key in cache:
        return cache[key]
    lines: tuple[str, ...] | None
    try:
        data = path.read_bytes()
        encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
        lines = tuple(data.decode(encoding, errors="replace").splitlines())
    except (OSError, SyntaxError, ValueError, LookupError):
        lines = None
    cache[key] = lines
    return lines


def _snippet(lines: tuple[str, ...], line: int, context_lines: int) -> str | None:
    """Return a numbered excerpt marking ``line`` with ``>``, or ``None``."""

    if line < 1 or line > len(lines):
        return None
    start = max(1, line - context_lines)
    end = min(len(lines), line + context_lines)
    width = len(str(end))
    rendered = []
    for number in range(start, end + 1):
        marker = ">" if number == line else " "
        rendered.append(f"{marker} {number:>{width}} | {lines[number - 1]}")
    return "\n".join(rendered)
