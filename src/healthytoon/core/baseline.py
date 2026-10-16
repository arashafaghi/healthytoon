"""Finding baselines: persist and apply fingerprint-based suppressions.

Fingerprints follow ROADMAP Phase 2 decision 3 (algorithm
``healthytoon/v2``): ``rule_id`` plus the normalized repo-relative path plus
``symbol`` when available, never line numbers or message text, so a baseline
survives refactors and threshold changes. Baselines are JSON files with a
stable shape and deterministic ordering.
"""

from __future__ import annotations

import dataclasses
import json
from collections import Counter
from pathlib import Path
from types import MappingProxyType

from healthytoon.core.scoring import build_score
from healthytoon.core.types import (
    FINGERPRINT_ALGORITHM,
    Category,
    Report,
    Severity,
    Summary,
)
from healthytoon.errors import ConfigError

__all__ = ["load_baseline", "write_baseline", "apply_baseline"]


def load_baseline(path: Path) -> frozenset[str]:
    """Read a baseline file and return its fingerprint set.

    Accepts either the canonical ``{"fingerprints": [...]}`` object or a bare
    JSON list of strings. Any unreadable or malformed content raises
    :class:`~healthytoon.errors.ConfigError`.
    """

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read baseline: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: malformed baseline JSON: {exc}") from exc

    if isinstance(data, dict):
        raw_fingerprints: object = data.get("fingerprints")
    elif isinstance(data, list):
        raw_fingerprints = data
    else:
        raise ConfigError(f"{path}: baseline must be a JSON object or list")

    if not isinstance(raw_fingerprints, list):
        raise ConfigError(f"{path}: baseline fingerprints must be a list of strings")

    fingerprints: set[str] = set()
    for item in raw_fingerprints:
        if not isinstance(item, str):
            raise ConfigError(f"{path}: baseline fingerprints must be a list of strings")
        fingerprints.add(item)
    return frozenset(fingerprints)


def write_baseline(path: Path, report: Report) -> None:
    """Write ``report``'s non-empty fingerprints as a deterministic baseline."""

    fingerprints = sorted(
        {finding.fingerprint for finding in report.findings if finding.fingerprint}
    )
    payload = {"algorithm": FINGERPRINT_ALGORITHM, "fingerprints": fingerprints}
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"{path}: cannot write baseline: {exc}") from exc


def apply_baseline(report: Report, fingerprints: frozenset[str]) -> Report:
    """Return a copy of ``report`` without suppressed findings.

    The summary is recomputed from the surviving findings; ``outcomes``,
    ``generated_at``, and every other report field are preserved. The result is
    a pure function of its inputs, so repeated application is deterministic.
    """

    remaining = tuple(
        finding for finding in report.findings if finding.fingerprint not in fingerprints
    )
    severity_counts = Counter(finding.severity for finding in remaining)
    category_counts = Counter(finding.category for finding in remaining)
    summary = Summary(
        total=len(remaining),
        score=build_score(remaining),
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
        duration_ms=report.summary.duration_ms,
    )
    return dataclasses.replace(report, findings=remaining, summary=summary)
