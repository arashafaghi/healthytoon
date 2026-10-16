"""Unit tests for the token-based duplication check."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from pathlib import Path

from healthytoon.checks.duplication import DuplicationCheck
from healthytoon.config.defaults import default_config
from healthytoon.config.models import Config, RuleConfig
from healthytoon.core.context import CheckContext
from healthytoon.core.types import Category, FileKind, Severity, Status

_RULE = "maintainability.duplication"


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _context(tmp_path: Path, rules: Mapping[str, RuleConfig] | None = None) -> CheckContext:
    config: Config = default_config()
    if rules is not None:
        config = dataclasses.replace(config, rules=dict(rules))
    return CheckContext.from_config(tmp_path, config)


def _block(statements: int, *, prefix: str = "value_") -> str:
    """Return ``statements`` assignments; each is five tokens (NAME = NAME + NAME)."""

    return "".join(f"{prefix}{index} = a + b\n" for index in range(statements))


def test_duplication_declares_cross_file_python_metadata() -> None:
    check = DuplicationCheck()

    assert check.cross_file is True
    assert check.file_kinds == frozenset({FileKind.PYTHON})
    assert check.rule_id == "HT-MAINT-005"
    assert check.category is Category.MAINTAINABILITY
    assert check.default_severity is Severity.LOW


def test_duplication_repeated_block_across_files_emits_one_finding(tmp_path: Path) -> None:
    _write(tmp_path, "one.py", _block(12))
    _write(tmp_path, "two.py", _block(12))

    result = DuplicationCheck().run(_context(tmp_path))

    assert result.status is Status.WARN
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.rule_id == "HT-MAINT-005"
    assert finding.check == _RULE
    assert finding.category is Category.MAINTAINABILITY
    assert finding.severity is Severity.LOW
    assert finding.location is not None
    assert finding.location.file == "one.py"
    assert dict(finding.properties) == {
        "tokens": 60,
        "occurrences": 2,
        "files": ["one.py", "two.py"],
    }
    assert finding.remediation is not None


def test_duplication_below_min_tokens_emits_nothing(tmp_path: Path) -> None:
    _write(tmp_path, "one.py", _block(8))
    _write(tmp_path, "two.py", _block(8))

    result = DuplicationCheck().run(_context(tmp_path))

    assert result.findings == ()
    assert result.status is Status.PASS


def test_duplication_single_occurrence_emits_nothing(tmp_path: Path) -> None:
    _write(tmp_path, "one.py", _block(12))

    result = DuplicationCheck().run(_context(tmp_path))

    assert result.findings == ()
    assert result.status is Status.PASS


def test_duplication_min_tokens_is_configurable(tmp_path: Path) -> None:
    _write(tmp_path, "one.py", _block(8))
    _write(tmp_path, "two.py", _block(8))
    rules = {_RULE: RuleConfig(options={"min_tokens": 20})}

    result = DuplicationCheck().run(_context(tmp_path, rules))

    assert len(result.findings) == 1
    assert dict(result.findings[0].properties) == {
        "tokens": 40,
        "occurrences": 2,
        "files": ["one.py", "two.py"],
    }


def test_duplication_max_findings_is_configurable(tmp_path: Path) -> None:
    _write(tmp_path, "alpha_one.py", _block(12, prefix="alpha_"))
    _write(tmp_path, "alpha_two.py", _block(12, prefix="alpha_"))
    _write(tmp_path, "beta_one.py", _block(12, prefix="beta_"))
    _write(tmp_path, "beta_two.py", _block(12, prefix="beta_"))
    rules = {_RULE: RuleConfig(options={"max_findings": 1})}

    result = DuplicationCheck().run(_context(tmp_path, rules))

    assert len(result.findings) == 1
    assert result.notes == "duplication findings truncated to 1 of 2 groups"


def test_duplication_notes_are_absent_when_not_truncated(tmp_path: Path) -> None:
    _write(tmp_path, "one.py", _block(12))
    _write(tmp_path, "two.py", _block(12))

    result = DuplicationCheck().run(_context(tmp_path))

    assert result.notes is None
