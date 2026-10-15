"""Tests for the pure scoring functions."""

from __future__ import annotations

import pytest

from healthytoon.core.scoring import DEFAULT_WEIGHTS, SEVERITY_PENALTY, build_score, grade_for
from healthytoon.core.types import Category, Finding, Location, Severity

#: ``by_category`` must always follow the canonical ``Category`` enum order.
_CANONICAL_ORDER = tuple(Category)


def _finding(
    *,
    severity: Severity,
    category: Category = Category.MAINTAINABILITY,
    confidence: float = 1.0,
    rule_id: str = "HT-X-001",
) -> Finding:
    return Finding(
        rule_id=rule_id,
        check="test.check",
        category=category,
        severity=severity,
        title="title",
        message="message",
        location=Location(file="pkg/mod.py", line=1),
        confidence=confidence,
    )


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (100.0, "A"),
        (90.0, "A"),
        (89.99, "B"),
        (80.0, "B"),
        (70.0, "C"),
        (60.0, "D"),
        (59.99, "F"),
        (0.0, "F"),
    ],
)
def test_grade_boundaries(score: float, expected: str) -> None:
    assert grade_for(score) == expected


def test_default_penalty_table() -> None:
    assert dict(SEVERITY_PENALTY) == {
        Severity.CRITICAL: 40.0,
        Severity.HIGH: 20.0,
        Severity.MEDIUM: 8.0,
        Severity.LOW: 3.0,
        Severity.INFO: 0.0,
    }


def test_default_weights_cover_every_category_with_expected_values() -> None:
    assert set(DEFAULT_WEIGHTS) == set(Category)
    assert dict(DEFAULT_WEIGHTS) == {
        Category.MAINTAINABILITY: 1.0,
        Category.SECURITY: 1.0,
        Category.CI_CD: 0.8,
        Category.TESTING: 0.8,
        Category.DEPENDENCIES: 0.7,
        Category.TYPING: 0.6,
        Category.DOCUMENTATION: 0.5,
        Category.PACKAGING: 0.5,
    }


def test_empty_findings_score_perfect_with_full_category_shape() -> None:
    result = build_score([])

    assert result.overall == 100.0
    assert result.grade == "A"
    assert len(result.by_category) == 8
    assert [entry.category for entry in result.by_category] == list(_CANONICAL_ORDER)
    for entry in result.by_category:
        assert entry.score == 100.0
        assert entry.findings == 0
        assert entry.weight == DEFAULT_WEIGHTS[entry.category]


def test_single_medium_maintainability_finding() -> None:
    result = build_score([_finding(severity=Severity.MEDIUM)])

    # Weighted mean over all 8 categories: (92 + 490) / 5.9 == 98.644...
    assert result.overall == 98.64
    assert result.grade == "A"
    maintainability = result.by_category[0]
    assert maintainability.category is Category.MAINTAINABILITY
    assert maintainability.score == 92.0
    assert maintainability.weight == 1.0
    assert maintainability.findings == 1
    others = [
        entry for entry in result.by_category if entry.category is not Category.MAINTAINABILITY
    ]
    assert len(others) == 7
    assert all(entry.score == 100.0 and entry.findings == 0 for entry in others)


@pytest.mark.parametrize(
    ("severity", "expected_category_score"),
    [
        (Severity.CRITICAL, 60.0),
        (Severity.HIGH, 80.0),
        (Severity.MEDIUM, 92.0),
        (Severity.LOW, 97.0),
        (Severity.INFO, 100.0),
    ],
)
def test_severity_penalty_applied_to_category(
    severity: Severity, expected_category_score: float
) -> None:
    result = build_score([_finding(severity=severity)])

    entry = result.by_category[0]
    assert entry.category is Category.MAINTAINABILITY
    assert entry.score == expected_category_score
    assert entry.findings == 1


def test_by_category_always_lists_every_category_in_canonical_order() -> None:
    result = build_score(
        [
            _finding(severity=Severity.LOW, category=Category.SECURITY, rule_id="HT-SEC-001"),
            _finding(severity=Severity.HIGH, category=Category.TYPING, rule_id="HT-TYP-001"),
        ]
    )

    assert [entry.category for entry in result.by_category] == list(_CANONICAL_ORDER)
    by_category = {entry.category: entry for entry in result.by_category}
    assert by_category[Category.SECURITY].score == 97.0
    assert by_category[Category.SECURITY].findings == 1
    assert by_category[Category.TYPING].score == 80.0
    assert by_category[Category.TYPING].findings == 1
    assert by_category[Category.MAINTAINABILITY].score == 100.0
    assert by_category[Category.MAINTAINABILITY].findings == 0


def test_critical_finding_caps_overall_at_forty() -> None:
    result = build_score([_finding(severity=Severity.CRITICAL)])

    assert result.by_category[0].score == 60.0
    assert result.overall == 40.0
    assert result.grade == "F"


def test_critical_finding_in_any_category_caps_overall() -> None:
    result = build_score(
        [_finding(severity=Severity.CRITICAL, category=Category.SECURITY, rule_id="HT-SEC-001")]
    )

    by_category = {entry.category: entry for entry in result.by_category}
    assert by_category[Category.SECURITY].score == 60.0
    assert result.overall == 40.0


def test_findings_in_same_category_accumulate() -> None:
    result = build_score(
        [
            _finding(severity=Severity.MEDIUM, rule_id="HT-M-001"),
            _finding(severity=Severity.MEDIUM, rule_id="HT-M-002"),
        ]
    )

    assert result.by_category[0].score == 84.0
    assert result.by_category[0].findings == 2
    assert result.overall == 97.29


def test_confidence_scales_penalty() -> None:
    full = build_score([_finding(severity=Severity.HIGH)])
    half = build_score([_finding(severity=Severity.HIGH, confidence=0.5)])

    assert full.by_category[0].score == 80.0
    assert half.by_category[0].score == 90.0
    assert full.overall == 96.61
    assert half.overall == 98.31
    assert half.overall > full.overall


def test_overall_is_fixed_weight_mean_of_all_categories() -> None:
    result = build_score(
        [_finding(severity=Severity.LOW, category=Category.DOCUMENTATION, rule_id="HT-DOC-001")]
    )

    # (490 + 97 * 0.5) / 5.9 == 99.745...
    assert result.overall == 99.75


def test_custom_weights_change_overall() -> None:
    findings = [
        _finding(severity=Severity.HIGH, category=Category.SECURITY, rule_id="HT-SEC-001"),
        _finding(severity=Severity.LOW, category=Category.DOCUMENTATION, rule_id="HT-DOC-001"),
    ]

    default = build_score(findings)
    security_heavy = build_score(
        findings,
        weights={Category.SECURITY: 5.0, Category.DOCUMENTATION: 0.1},
    )

    # (5.0 * 80 + 0.1 * 97) / 5.1 == 80.33...
    assert security_heavy.overall == 80.33
    assert security_heavy.overall < default.overall
    # Categories absent from the custom mapping get weight 0 and drop out of the mean.
    weights_by_category = {entry.category: entry.weight for entry in security_heavy.by_category}
    assert weights_by_category[Category.SECURITY] == 5.0
    assert weights_by_category[Category.DOCUMENTATION] == 0.1
    assert weights_by_category[Category.MAINTAINABILITY] == 0.0


def test_scoring_is_deterministic() -> None:
    findings = [
        _finding(severity=Severity.HIGH, rule_id="HT-A-001"),
        _finding(severity=Severity.LOW, category=Category.TYPING, rule_id="HT-T-001"),
    ]

    assert build_score(findings) == build_score(findings)
