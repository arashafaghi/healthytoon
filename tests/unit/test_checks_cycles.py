"""Unit tests for the import-cycle maintainability check."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from pathlib import Path

from healthytoon.checks.cycles import ImportCycleCheck
from healthytoon.config.defaults import default_config
from healthytoon.config.models import Config, RuleConfig
from healthytoon.core.context import CheckContext
from healthytoon.core.types import Category, FileKind, Severity, Status

_RULE = "maintainability.import_cycles"


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


def test_import_cycles_declares_cross_file_python_metadata() -> None:
    check = ImportCycleCheck()

    assert check.cross_file is True
    assert check.file_kinds == frozenset({FileKind.PYTHON})
    assert check.rule_id == "HT-MAINT-004"
    assert check.category is Category.MAINTAINABILITY
    assert check.default_severity is Severity.MEDIUM


def test_import_cycles_two_module_cycle_emits_one_finding(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/a.py", "from pkg.b import helper\n\n\ndef run():\n    return helper()\n")
    _write(tmp_path, "pkg/b.py", "from pkg.a import run\n\n\ndef helper():\n    return run\n")

    result = ImportCycleCheck().run(_context(tmp_path))

    assert result.status is Status.WARN
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.rule_id == "HT-MAINT-004"
    assert finding.check == _RULE
    assert finding.category is Category.MAINTAINABILITY
    assert finding.severity is Severity.MEDIUM
    assert finding.location is not None
    assert finding.location.file == "pkg/a.py"
    assert dict(finding.properties) == {"modules": ["pkg/a.py", "pkg/b.py"]}
    assert finding.symbol == "pkg/a.py -> pkg/b.py"
    assert finding.remediation is not None


def test_import_cycles_acyclic_graph_emits_nothing(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/a.py", "from pkg.b import helper\n")
    _write(tmp_path, "pkg/b.py", "def helper():\n    return 1\n")

    result = ImportCycleCheck().run(_context(tmp_path))

    assert result.findings == ()
    assert result.status is Status.PASS


def test_import_cycles_resolves_relative_imports(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/a.py", "from . import b\n")
    _write(tmp_path, "pkg/b.py", "from . import a\n")

    result = ImportCycleCheck().run(_context(tmp_path))

    assert result.status is Status.WARN
    assert len(result.findings) == 1
    assert dict(result.findings[0].properties) == {"modules": ["pkg/a.py", "pkg/b.py"]}


def test_import_cycles_self_import_is_reported(tmp_path: Path) -> None:
    _write(tmp_path, "selfmod.py", "import selfmod\n")

    result = ImportCycleCheck().run(_context(tmp_path))

    assert len(result.findings) == 1
    assert dict(result.findings[0].properties) == {"modules": ["selfmod.py"]}
