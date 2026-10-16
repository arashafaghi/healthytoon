"""Deterministic, pure scoring of a finding set.

The formula is fixed and recorded in ``ROADMAP.md`` (Phase 2 decision 10):
each finding subtracts a fixed penalty (scaled by confidence) from its
category's 100-point baseline; ``by_category`` always contains every category
in canonical order (zero-finding categories score 100); the overall score is
the fixed-weight mean across all categories, capped at 40 when any critical
finding is present. The score is informational and never fails a command on its
own (see ``core.policy``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType

from healthytoon.core.types import Category, CategoryScore, Finding, Score, Severity

__all__ = ["SEVERITY_PENALTY", "DEFAULT_WEIGHTS", "grade_for", "build_score"]

SEVERITY_PENALTY: Mapping[Severity, float] = MappingProxyType(
    {
        Severity.CRITICAL: 40.0,
        Severity.HIGH: 20.0,
        Severity.MEDIUM: 8.0,
        Severity.LOW: 3.0,
        Severity.INFO: 0.0,
    }
)

DEFAULT_WEIGHTS: Mapping[Category, float] = MappingProxyType(
    {
        Category.MAINTAINABILITY: 1.0,
        Category.SECURITY: 1.0,
        Category.CI_CD: 0.8,
        Category.TESTING: 0.8,
        Category.DEPENDENCIES: 0.7,
        Category.TYPING: 0.6,
        Category.DOCUMENTATION: 0.5,
        Category.PACKAGING: 0.5,
    }
)

_CRITICAL_CAP = 40.0
_GRADE_THRESHOLDS: tuple[tuple[float, str], ...] = (
    (90.0, "A"),
    (80.0, "B"),
    (70.0, "C"),
    (60.0, "D"),
)


def grade_for(score: float) -> str:
    """Map a 0-100 score to a letter grade."""

    for threshold, grade in _GRADE_THRESHOLDS:
        if score >= threshold:
            return grade
    return "F"


def build_score(
    findings: Sequence[Finding],
    *,
    weights: Mapping[Category, float] | None = None,
) -> Score:
    """Aggregate ``findings`` into a :class:`Score`.

    ``by_category`` always lists every :class:`Category` in canonical order so
    report shape is stable across runs; categories without findings score 100.
    The overall value is the fixed-weight mean over all categories, so it is
    comparable between repositories and runs.
    """

    active_weights = weights if weights is not None else DEFAULT_WEIGHTS
    by_category = _group_by_category(findings)
    category_scores = [
        _category_score(category, by_category.get(category, []), active_weights)
        for category in Category
    ]
    overall = _overall_score(category_scores, findings)
    return Score(overall=overall, grade=grade_for(overall), by_category=tuple(category_scores))


def _group_by_category(findings: Sequence[Finding]) -> dict[Category, list[Finding]]:
    by_category: dict[Category, list[Finding]] = {}
    for finding in findings:
        by_category.setdefault(finding.category, []).append(finding)
    return by_category


def _category_score(
    category: Category,
    items: Sequence[Finding],
    weights: Mapping[Category, float],
) -> CategoryScore:
    if items:
        penalty = sum(
            SEVERITY_PENALTY.get(finding.severity, 0.0) * _confidence(finding) for finding in items
        )
        score = round(_clamp(100.0 - penalty), 2)
    else:
        score = 100.0
    return CategoryScore(
        category=category,
        score=score,
        weight=float(weights.get(category, 0.0)),
        findings=len(items),
    )


def _overall_score(category_scores: Sequence[CategoryScore], findings: Sequence[Finding]) -> float:
    weighted = [(entry.weight, entry.score) for entry in category_scores if entry.weight > 0]
    if weighted:
        total_weight = sum(weight for weight, _ in weighted)
        overall = sum(weight * score for weight, score in weighted) / total_weight
    else:
        overall = 100.0

    overall = _clamp(overall)
    if any(finding.severity is Severity.CRITICAL for finding in findings):
        overall = min(overall, _CRITICAL_CAP)
    return round(overall, 2)


def _confidence(finding: Finding) -> float:
    return _clamp(finding.confidence, 0.0, 1.0)


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))
