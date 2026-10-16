"""Unit tests for the import-cycle maintainability check."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from pathlib import Path

from healthytoon.checks.cycles import ImportCycleCheck
from healthytoon.config.defaults import default_config
from healthytoon.config.models import Config, RuleConfig
from healthytoon.core.context import CheckContext
from healthytoon.core.types import Category, FileKind, Finding, Severity, Status

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


def _assert_cycle_finding(finding: Finding, modules: list[str]) -> None:
    assert finding.rule_id == "HT-MAINT-004"
    assert finding.check == _RULE
    assert finding.category is Category.MAINTAINABILITY
    assert finding.severity is Severity.MEDIUM
    assert finding.location is not None
    assert finding.location.file == modules[0]
    assert dict(finding.properties) == {"modules": modules}
    assert finding.symbol == " -> ".join(modules)
    assert finding.remediation is not None


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
    _assert_cycle_finding(result.findings[0], ["pkg/a.py", "pkg/b.py"])


def test_import_cycles_acyclic_graph_emits_nothing(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/a.py", "from pkg.b import helper\n")
    _write(tmp_path, "pkg/b.py", "def helper():\n    return 1\n")

    result = ImportCycleCheck().run(_context(tmp_path))

    assert result.findings == ()
    assert result.status is Status.PASS


def test_import_cycles_ignores_stdlib_shadowing_a_nested_file(tmp_path: Path) -> None:
    # ``reporters/json.py`` importing stdlib ``json`` must not self-cycle just
    # because its own path suffix happens to match the module name.
    _write(tmp_path, "reporters/json.py", "import json\n\nvalue = json.dumps({})\n")

    result = ImportCycleCheck().run(_context(tmp_path))

    assert result.findings == ()
    assert result.status is Status.PASS


def test_import_cycles_keeps_a_project_package_named_like_stdlib(tmp_path: Path) -> None:
    # A root-level ``json.py`` genuinely shadows the standard library, so its
    # self-import is a real cycle and must still be reported.
    _write(tmp_path, "json.py", "import json\n")

    result = ImportCycleCheck().run(_context(tmp_path))

    assert len(result.findings) == 1
    assert dict(result.findings[0].properties) == {"modules": ["json.py"]}


def test_import_cycles_package_submodule_import_is_not_a_false_cycle(tmp_path: Path) -> None:
    # The package ``__init__`` imports its submodules while each submodule pulls
    # a shared helper with ``from pkg import _shared``; the submodule edge must
    # replace the package edge, otherwise a fictional cycle appears.
    _write(tmp_path, "pkg/__init__.py", "from pkg.a import thing\nfrom pkg.b import thing\n")
    _write(tmp_path, "pkg/a.py", "from pkg import _shared\n\nthing = _shared\n")
    _write(tmp_path, "pkg/b.py", "from pkg import _shared\n\nthing = _shared\n")
    _write(tmp_path, "pkg/_shared.py", "HELPER = 1\n")

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


def test_import_cycles_self_package_import_is_not_a_cycle(tmp_path: Path) -> None:
    # ``from pkg import helper`` inside ``pkg/__init__.py`` (where the name is
    # defined) must not become a self-edge to the package itself.
    _write(tmp_path, "pkg/__init__.py", "from pkg import helper\n\n\ndef helper():\n    return 1\n")

    result = ImportCycleCheck().run(_context(tmp_path))

    assert result.findings == ()
    assert result.status is Status.PASS


def test_import_cycles_unparseable_file_is_ignored(tmp_path: Path) -> None:
    _write(tmp_path, "broken.py", "def (:\n")

    result = ImportCycleCheck().run(_context(tmp_path))

    assert result.findings == ()
    assert result.status is Status.PASS


def test_import_cycles_self_import_is_reported(tmp_path: Path) -> None:
    _write(tmp_path, "selfmod.py", "import selfmod\n")

    result = ImportCycleCheck().run(_context(tmp_path))

    assert len(result.findings) == 1
    assert dict(result.findings[0].properties) == {"modules": ["selfmod.py"]}
