"""Unit tests for the structural maintainability checks."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from pathlib import Path

from healthytoon.checks.structure import (
    FunctionLengthCheck,
    ModuleLengthCheck,
    NestingDepthCheck,
)
from healthytoon.config.defaults import default_config
from healthytoon.config.models import Config, RuleConfig
from healthytoon.core.context import CheckContext
from healthytoon.core.types import Category, Severity, Status

_MAX_LENGTH_RULE = "maintainability.function_length"
_MAX_DEPTH_RULE = "maintainability.nesting_depth"
_MAX_LINES_RULE = "maintainability.module_length"


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _context(tmp_path: Path, rules: Mapping[str, RuleConfig] | None = None) -> CheckContext:
    config: Config = default_config()
    if rules is not None:
        config = dataclasses.replace(config, rules=dict(rules))
    return CheckContext.from_config(tmp_path, config)


def _long_function(lines: int = 60, name: str = "long_function") -> str:
    body = "\n".join(f"    value_{i} = {i}" for i in range(lines - 2))
    return f"def {name}():\n{body}\n    return value_0\n"


# --- FunctionLengthCheck ----------------------------------------------------


def test_function_length_violation_emits_expected_finding(tmp_path: Path) -> None:
    _write(tmp_path, "long.py", _long_function(60))

    result = FunctionLengthCheck().run(_context(tmp_path))

    assert result.status is Status.WARN
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.rule_id == "HT-MAINT-001"
    assert finding.check == _MAX_LENGTH_RULE
    assert finding.category is Category.MAINTAINABILITY
    assert finding.severity is Severity.MEDIUM
    assert finding.symbol == "long_function"
    assert finding.location is not None
    assert finding.location.file == "long.py"
    assert finding.location.line == 1
    assert finding.location.end_line == 60
    assert dict(finding.properties) == {"length": 60, "max": 50}
    assert finding.remediation is not None


def test_function_length_clean_file_emits_nothing(tmp_path: Path) -> None:
    _write(tmp_path, "short.py", _long_function(20))

    result = FunctionLengthCheck().run(_context(tmp_path))

    assert result.findings == ()
    assert result.status is Status.PASS


def test_function_length_threshold_is_configurable(tmp_path: Path) -> None:
    _write(tmp_path, "small.py", "def small():\n    a = 1\n    b = 2\n    return a + b\n")
    rules = {_MAX_LENGTH_RULE: RuleConfig(options={"max_length": 3})}

    result = FunctionLengthCheck().run(_context(tmp_path, rules))

    assert len(result.findings) == 1
    assert dict(result.findings[0].properties) == {"length": 4, "max": 3}


def test_function_length_uses_dotted_qualified_names(tmp_path: Path) -> None:
    source = (
        "class Parser:\n"
        "    def parse(self, data):\n"
        "        return data\n"
        "\n"
        "def outer():\n"
        "    def inner():\n"
        "        return 1\n"
        "    return inner\n"
    )
    _write(tmp_path, "nested.py", source)
    rules = {_MAX_LENGTH_RULE: RuleConfig(options={"max_length": 1})}

    result = FunctionLengthCheck().run(_context(tmp_path, rules))

    symbols = [finding.symbol for finding in result.findings]
    assert symbols == ["Parser.parse", "outer", "outer.inner"]
    lines = {
        finding.symbol: finding.location.line
        for finding in result.findings
        if finding.location is not None
    }
    assert lines == {"Parser.parse": 2, "outer": 5, "outer.inner": 6}


def test_unparseable_files_are_skipped(tmp_path: Path) -> None:
    _write(tmp_path, "broken.py", "def (:\n")
    _write(tmp_path, "fine.py", _long_function(10))

    result = FunctionLengthCheck().run(_context(tmp_path))

    assert result.findings == ()
    assert result.status is Status.PASS


# --- NestingDepthCheck ------------------------------------------------------

_DEEP_SOURCE = """def deep(a, b, c, d, e, f):
    if a:
        if b:
            if c:
                if d:
                    if e:
                        if f:
                            pass
"""


def test_nesting_depth_violation_emits_expected_finding(tmp_path: Path) -> None:
    _write(tmp_path, "deep.py", _DEEP_SOURCE)

    result = NestingDepthCheck().run(_context(tmp_path))

    assert result.status is Status.WARN
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.rule_id == "HT-MAINT-002"
    assert finding.check == _MAX_DEPTH_RULE
    assert finding.category is Category.MAINTAINABILITY
    assert finding.severity is Severity.MEDIUM
    assert finding.symbol == "deep"
    assert finding.location is not None
    assert finding.location.file == "deep.py"
    assert finding.location.line == 1
    assert dict(finding.properties) == {"depth": 6, "max": 5}


def test_nesting_depth_clean_file_emits_nothing(tmp_path: Path) -> None:
    source = (
        "def shallow(a, items):\n"
        "    if a:\n"
        "        for item in items:\n"
        "            if item:\n"
        "                pass\n"
    )
    _write(tmp_path, "shallow.py", source)

    result = NestingDepthCheck().run(_context(tmp_path))

    assert result.findings == ()
    assert result.status is Status.PASS


def test_nesting_depth_threshold_is_configurable(tmp_path: Path) -> None:
    source = "def level_two(a):\n    if a:\n        if a > 0:\n            pass\n"
    _write(tmp_path, "two.py", source)
    rules = {_MAX_DEPTH_RULE: RuleConfig(options={"max_depth": 1})}

    result = NestingDepthCheck().run(_context(tmp_path, rules))

    assert len(result.findings) == 1
    assert dict(result.findings[0].properties) == {"depth": 2, "max": 1}


def test_nesting_depth_comprehensions_optional(tmp_path: Path) -> None:
    source = "def grid(matrix):\n    return [[cell for cell in row] for row in matrix]\n"
    _write(tmp_path, "grid.py", source)

    default_result = NestingDepthCheck().run(_context(tmp_path))
    counted = NestingDepthCheck().run(
        _context(
            tmp_path,
            {_MAX_DEPTH_RULE: RuleConfig(options={"count_comprehensions": True, "max_depth": 1})},
        )
    )

    assert default_result.findings == ()
    assert len(counted.findings) == 1
    assert dict(counted.findings[0].properties) == {"depth": 2, "max": 1}


def test_nesting_depth_lambdas_optional(tmp_path: Path) -> None:
    source = "def rank(items):\n    return sorted(items, key=lambda item: item.rank)\n"
    _write(tmp_path, "rank.py", source)

    default_result = NestingDepthCheck().run(_context(tmp_path))
    counted = NestingDepthCheck().run(
        _context(
            tmp_path,
            {_MAX_DEPTH_RULE: RuleConfig(options={"count_lambdas": True, "max_depth": 0})},
        )
    )

    assert default_result.findings == ()
    assert len(counted.findings) == 1
    assert dict(counted.findings[0].properties) == {"depth": 1, "max": 0}


# --- ModuleLengthCheck ------------------------------------------------------


def test_module_length_violation_emits_one_finding(tmp_path: Path) -> None:
    _write(tmp_path, "big.py", "\n".join(f"x{i} = {i}" for i in range(1001)) + "\n")

    result = ModuleLengthCheck().run(_context(tmp_path))

    assert result.status is Status.WARN
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.rule_id == "HT-MAINT-003"
    assert finding.check == _MAX_LINES_RULE
    assert finding.category is Category.MAINTAINABILITY
    assert finding.severity is Severity.LOW
    assert finding.symbol == "<module>"
    assert finding.location is not None
    assert finding.location.file == "big.py"
    assert finding.location.line == 1
    assert dict(finding.properties) == {"lines": 1001, "max": 1000}


def test_module_length_clean_file_emits_nothing(tmp_path: Path) -> None:
    _write(tmp_path, "small.py", "\n".join(f"x{i} = {i}" for i in range(10)) + "\n")

    result = ModuleLengthCheck().run(_context(tmp_path))

    assert result.findings == ()
    assert result.status is Status.PASS


def test_module_length_threshold_is_configurable(tmp_path: Path) -> None:
    _write(tmp_path, "ten.py", "\n".join(f"x{i} = {i}" for i in range(10)) + "\n")
    rules = {_MAX_LINES_RULE: RuleConfig(options={"max_lines": 5})}

    result = ModuleLengthCheck().run(_context(tmp_path, rules))

    assert len(result.findings) == 1
    assert dict(result.findings[0].properties) == {"lines": 10, "max": 5}


def test_module_length_counts_only_source_lines(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "sparse.py",
        '"""Doc."""\n\n# comment one\nx = 1\n\n# comment two\ny = 2\n',
    )

    default_result = ModuleLengthCheck().run(_context(tmp_path))
    strict_result = ModuleLengthCheck().run(
        _context(tmp_path, {_MAX_LINES_RULE: RuleConfig(options={"max_lines": 2})})
    )

    assert default_result.findings == ()
    assert len(strict_result.findings) == 1
    assert dict(strict_result.findings[0].properties) == {"lines": 3, "max": 2}
