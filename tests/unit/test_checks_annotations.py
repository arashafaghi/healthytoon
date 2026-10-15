"""Unit tests for the typing annotation coverage check."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from pathlib import Path

from healthytoon.checks.annotations import AnnotationCoverageCheck
from healthytoon.config.defaults import default_config
from healthytoon.config.models import Config, RuleConfig
from healthytoon.core.context import CheckContext
from healthytoon.core.types import Category, Severity, Status

_RULE = "typing.annotation_coverage"


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _context(tmp_path: Path, rules: Mapping[str, RuleConfig] | None = None) -> CheckContext:
    config: Config = default_config()
    if rules is not None:
        config = dataclasses.replace(config, rules=dict(rules))
    return CheckContext.from_config(tmp_path, config)


def test_annotation_coverage_violation_emits_expected_finding(tmp_path: Path) -> None:
    source = """def one(x: int) -> int:
    return x


def two(x: int) -> int:
    return x


def three(x: int) -> int:
    return x


def four(x):
    return x


def five(x):
    return x
"""
    _write(tmp_path, "coverage.py", source)

    result = AnnotationCoverageCheck().run(_context(tmp_path))

    assert result.status is Status.WARN
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.rule_id == "HT-TYPE-001"
    assert finding.check == _RULE
    assert finding.category is Category.TYPING
    assert finding.severity is Severity.LOW
    assert finding.symbol == "<module>"
    assert finding.location is not None
    assert finding.location.file == "coverage.py"
    assert finding.location.line == 1
    assert dict(finding.properties) == {"annotated": 3, "total": 5, "min_ratio": 0.8}
    assert finding.message == "only 3/5 public functions are annotated (min 80%)"


def test_annotation_coverage_at_min_ratio_is_clean(tmp_path: Path) -> None:
    source = """def one(x: int) -> int:
    return x


def two(x: int) -> int:
    return x


def three(x: int) -> int:
    return x


def four(x: int) -> int:
    return x


def five(x):
    return x
"""
    _write(tmp_path, "boundary.py", source)

    result = AnnotationCoverageCheck().run(_context(tmp_path))

    assert result.findings == ()
    assert result.status is Status.PASS


def test_annotation_coverage_min_ratio_is_configurable(tmp_path: Path) -> None:
    source = """def one(x: int) -> int:
    return x


def two(x: int) -> int:
    return x


def three(x: int) -> int:
    return x


def four(x: int) -> int:
    return x


def five(x):
    return x
"""
    _write(tmp_path, "strict.py", source)
    rules = {_RULE: RuleConfig(options={"min_ratio": 0.9})}

    result = AnnotationCoverageCheck().run(_context(tmp_path, rules))

    assert len(result.findings) == 1
    finding = result.findings[0]
    assert dict(finding.properties) == {"annotated": 4, "total": 5, "min_ratio": 0.9}
    assert finding.message == "only 4/5 public functions are annotated (min 90%)"


def test_annotation_coverage_ignores_private_functions(tmp_path: Path) -> None:
    source = """def _unannotated(x):
    return x


def _also_unannotated(y):
    return y


def annotated(value: int) -> int:
    return value


private_value = _unannotated(1)
"""
    _write(tmp_path, "private.py", source)

    result = AnnotationCoverageCheck().run(_context(tmp_path))

    assert result.findings == ()
    assert result.status is Status.PASS


def test_annotation_coverage_excludes_self_and_requires_return(tmp_path: Path) -> None:
    source = """class Service:
    def run(self, payload: str) -> None:
        self._payload = payload

    def load(self, name):
        return name
"""
    _write(tmp_path, "service.py", source)

    result = AnnotationCoverageCheck().run(_context(tmp_path))

    assert len(result.findings) == 1
    assert dict(result.findings[0].properties) == {"annotated": 1, "total": 2, "min_ratio": 0.8}


def test_annotation_coverage_skips_modules_without_public_functions(tmp_path: Path) -> None:
    _write(tmp_path, "bare.py", "CONSTANT = 1\n\nclass _Private:\n    pass\n")

    result = AnnotationCoverageCheck().run(_context(tmp_path))

    assert result.findings == ()
    assert result.status is Status.PASS
