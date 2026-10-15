"""Unit tests for :mod:`healthytoon.core.types`."""

from __future__ import annotations

import pytest

from healthytoon.core.types import (
    FINGERPRINT_ALGORITHM,
    SEVERITY_SARIF_LEVEL,
    Category,
    Finding,
    Location,
    Severity,
    canonical_sort_key,
    compute_fingerprint,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("HIGH", Severity.HIGH),
        ("high", Severity.HIGH),
        (" High ", Severity.HIGH),
        ("critical", Severity.CRITICAL),
        ("info", Severity.INFO),
        (0, Severity.INFO),
        (2, Severity.MEDIUM),
        (4, Severity.CRITICAL),
        (Severity.LOW, Severity.LOW),
    ],
)
def test_severity_parse_accepts_names_ints_and_members(value: object, expected: Severity) -> None:
    assert Severity.parse(value) is expected


@pytest.mark.parametrize("value", [5, -1, 99])
def test_severity_parse_rejects_out_of_range_ints(value: int) -> None:
    with pytest.raises(ValueError):
        Severity.parse(value)


@pytest.mark.parametrize("value", [True, False])
def test_severity_parse_rejects_bools(value: bool) -> None:
    with pytest.raises(ValueError):
        Severity.parse(value)


@pytest.mark.parametrize("value", ["urgent", "", "MEDIUMish", 2.5, None, ["HIGH"]])
def test_severity_parse_rejects_unknown_values(value: object) -> None:
    with pytest.raises(ValueError):
        Severity.parse(value)


@pytest.mark.parametrize(
    ("severity", "level"),
    [
        (Severity.CRITICAL, "error"),
        (Severity.HIGH, "error"),
        (Severity.MEDIUM, "warning"),
        (Severity.LOW, "note"),
        (Severity.INFO, "note"),
    ],
)
def test_sarif_level_for_every_severity(severity: Severity, level: str) -> None:
    assert severity.sarif_level == level
    assert SEVERITY_SARIF_LEVEL[severity] == level


def test_fingerprint_is_stable_and_hex_formatted() -> None:
    first = compute_fingerprint(rule_id="HT-1", file="pkg/a.py", symbol="func")
    second = compute_fingerprint(rule_id="HT-1", file="pkg/a.py", symbol="func")

    assert first == second
    assert len(first) == 16
    assert all(character in "0123456789abcdef" for character in first)


def test_fingerprint_distinct_for_different_rule_id() -> None:
    assert compute_fingerprint(rule_id="HT-1", file="pkg/a.py") != compute_fingerprint(
        rule_id="HT-2", file="pkg/a.py"
    )


def _finding_fingerprint(finding: Finding) -> str:
    location = finding.location
    assert location is not None
    return compute_fingerprint(rule_id=finding.rule_id, file=location.file, symbol=finding.symbol)


def test_fingerprint_ignores_line_and_message_by_contract() -> None:
    # The fingerprint basis is (rule_id, file, symbol) only: differing message,
    # line, severity, or title must not change the computed fingerprint.
    first = Finding(
        rule_id="HT-1",
        check="test.check",
        category=Category.MAINTAINABILITY,
        severity=Severity.MEDIUM,
        title="title",
        message="first message",
        location=Location(file="pkg/a.py", line=1),
        symbol="func",
    )
    second = Finding(
        rule_id="HT-1",
        check="test.check",
        category=Category.MAINTAINABILITY,
        severity=Severity.HIGH,
        title="renamed title",
        message="second message",
        location=Location(file="pkg/a.py", line=99),
        symbol="func",
    )

    assert _finding_fingerprint(first) == _finding_fingerprint(second)


def test_fingerprint_normalizes_backslash_paths() -> None:
    windows_style = compute_fingerprint(rule_id="HT-1", file="pkg\\sub\\mod.py")
    posix_style = compute_fingerprint(rule_id="HT-1", file="pkg/sub/mod.py")

    assert windows_style == posix_style


def test_fingerprint_symbol_distinguishes_findings() -> None:
    with_symbol = compute_fingerprint(rule_id="HT-1", file="pkg/a.py", symbol="first")
    other_symbol = compute_fingerprint(rule_id="HT-1", file="pkg/a.py", symbol="second")
    without_symbol = compute_fingerprint(rule_id="HT-1", file="pkg/a.py")
    explicit_none = compute_fingerprint(rule_id="HT-1", file="pkg/a.py", symbol=None)

    assert with_symbol != other_symbol
    assert with_symbol != without_symbol
    assert without_symbol == explicit_none


def test_fingerprint_algorithm_identifier() -> None:
    assert FINGERPRINT_ALGORITHM == "healthytoon/v2"


def _sort_key_finding(
    rule_id: str,
    title: str,
    *,
    file: str | None = "a.py",
    line: int | None = None,
    column: int | None = None,
) -> Finding:
    location = Location(file=file, line=line, column=column) if file is not None else None
    return Finding(
        rule_id=rule_id,
        check="test.check",
        category=Category.MAINTAINABILITY,
        severity=Severity.LOW,
        title=title,
        message="message",
        location=location,
    )


def test_canonical_sort_key_file_line_column_rule_title() -> None:
    file_variant = _sort_key_finding("R1", "m", file="c.py", line=1, column=1)
    line_variant = _sort_key_finding("R1", "m", file="b.py", line=2, column=2)
    column_variant = _sort_key_finding("R1", "m", file="b.py", line=1, column=2)
    rule_variant = _sort_key_finding("R0", "m", file="b.py", line=1, column=1)
    title_variant = _sort_key_finding("R1", "n", file="b.py", line=1, column=1)
    baseline = _sort_key_finding("R1", "m", file="b.py", line=1, column=1)

    shuffled = [file_variant, line_variant, title_variant, column_variant, baseline, rule_variant]
    ordered = sorted(shuffled, key=canonical_sort_key)

    # file, then line, then column, then rule id, then title.
    assert ordered == [
        rule_variant,
        baseline,
        title_variant,
        column_variant,
        line_variant,
        file_variant,
    ]
    assert [canonical_sort_key(finding) for finding in ordered] == sorted(
        canonical_sort_key(finding) for finding in shuffled
    )


def test_canonical_sort_key_location_less_uses_empty_file() -> None:
    bare = _sort_key_finding("R1", "t", file=None)

    assert canonical_sort_key(bare) == ("", -1, -1, "R1", "t")


def test_canonical_sort_key_location_less_sorts_before_located() -> None:
    bare = _sort_key_finding("R9", "z", file=None)
    located = _sort_key_finding("R0", "a", file="a.py", line=1)

    assert sorted([located, bare], key=canonical_sort_key) == [bare, located]


def test_canonical_sort_key_none_line_and_column_sort_before_numbers() -> None:
    no_position = _sort_key_finding("R1", "t", file="a.py")
    first_line = _sort_key_finding("R1", "t", file="a.py", line=1)

    assert canonical_sort_key(no_position) < canonical_sort_key(first_line)


def test_canonical_sort_key_normalizes_backslashes() -> None:
    windows_style = _sort_key_finding("R1", "t", file="b\\c.py")
    posix_style = _sort_key_finding("R1", "t", file="b/c.py")

    assert canonical_sort_key(windows_style)[0] == "b/c.py"
    assert canonical_sort_key(windows_style) == canonical_sort_key(posix_style)


def test_canonical_sort_key_is_a_total_order() -> None:
    findings = [
        _sort_key_finding("R2", "t2", file="b.py", line=10, column=2),
        _sort_key_finding("R1", "t1", file=None),
        _sort_key_finding("R1", "t2", file="a.py", line=3),
        _sort_key_finding("R1", "t1", file="a.py", line=3),
        _sort_key_finding("R0", "t9", file="a.py", line=3, column=7),
        _sort_key_finding("R1", "t1", file="a.py", line=1, column=9),
    ]

    first_pass = sorted(findings, key=canonical_sort_key)
    second_pass = sorted(reversed(findings), key=canonical_sort_key)

    assert first_pass == second_pass
    assert [canonical_sort_key(finding) for finding in first_pass] == sorted(
        canonical_sort_key(finding) for finding in findings
    )
