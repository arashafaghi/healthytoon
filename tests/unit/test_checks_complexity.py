"""Unit tests for the optional Radon-backed complexity check."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from pathlib import Path

import pytest

from healthytoon.checks import _optional
from healthytoon.checks.complexity import ComplexityCheck
from healthytoon.config.defaults import default_config
from healthytoon.config.models import Config, RuleConfig
from healthytoon.core.context import CheckContext
from healthytoon.core.types import Category, FileKind, Severity, Status

_RULE = "maintainability.complexity"

#: Eleven sequential branches => cyclomatic complexity 12 (> default max of 10).
_HIGH_COMPLEXITY = (
    "def tangled(a):\n"
    + "".join(f"    if a == {index}:\n        return {index}\n" for index in range(1, 12))
    + "    return 0\n"
)

_SIMPLE = "def simple(a: int) -> int:\n    return a + 1\n"


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _context(tmp_path: Path, rules: Mapping[str, RuleConfig] | None = None) -> CheckContext:
    config: Config = default_config()
    if rules is not None:
        config = dataclasses.replace(config, rules=dict(rules))
    return CheckContext.from_config(tmp_path, config)


def test_complexity_declares_single_file_python_metadata() -> None:
    check = ComplexityCheck()

    assert check.id == _RULE
    assert check.rule_id == "HT-MAINT-006"
    assert check.category is Category.MAINTAINABILITY
    assert check.default_severity is Severity.MEDIUM
    assert check.cross_file is False
    assert check.file_kinds == frozenset({FileKind.PYTHON})


def test_complexity_real_radon_flags_a_high_complexity_function(tmp_path: Path) -> None:
    _write(tmp_path, "tangled.py", _HIGH_COMPLEXITY)

    result = ComplexityCheck().run(_context(tmp_path))

    assert result.status is Status.WARN
    findings = [finding for finding in result.findings if finding.rule_id == "HT-MAINT-006"]
    assert len(findings) == 1
    finding = findings[0]
    assert finding.check == _RULE
    assert finding.location is not None
    assert finding.location.file == "tangled.py"
    assert finding.location.line == 1
    assert finding.symbol == "tangled"
    assert finding.properties["complexity"] > 10
    assert finding.properties["max"] == 10
    assert finding.remediation is not None


def test_complexity_simple_function_produces_no_findings(tmp_path: Path) -> None:
    _write(tmp_path, "simple.py", _SIMPLE)

    result = ComplexityCheck().run(_context(tmp_path))

    assert result.findings == ()
    assert result.status is Status.PASS


def test_complexity_option_lowers_the_threshold(tmp_path: Path) -> None:
    # A single branch gives complexity 2; the default max of 10 ignores it.
    _write(tmp_path, "branchy.py", "def branchy(a):\n    if a:\n        return 1\n    return 0\n")
    rules = {_RULE: RuleConfig(options={"max_cyclomatic": 1})}

    result = ComplexityCheck().run(_context(tmp_path, rules))

    assert result.status is Status.WARN
    assert result.findings[0].symbol == "branchy"
    assert result.findings[0].properties == {"complexity": 2, "max": 1}


def test_complexity_skips_when_radon_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path, "tangled.py", _HIGH_COMPLEXITY)
    monkeypatch.setattr(_optional, "radon_module", lambda: None)

    result = ComplexityCheck().run(_context(tmp_path))

    assert result.status is Status.SKIP
    assert result.findings == ()
    assert result.notes == "radon is not installed; complexity analysis skipped"
