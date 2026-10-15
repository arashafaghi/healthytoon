"""Unit tests for the documentation coverage check."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from pathlib import Path

from healthytoon.checks.docstrings import DocstringCoverageCheck
from healthytoon.config.defaults import default_config
from healthytoon.config.models import Config, RuleConfig
from healthytoon.core.context import CheckContext
from healthytoon.core.types import Category, Severity, Status

_RULE = "documentation.docstring_coverage"

_FOUR_OF_FIVE = '''"""Module."""


def alpha():
    """A."""
    return 1


def beta():
    """B."""
    return 1


def gamma():
    """C."""
    return 1


def delta():
    return 1
'''


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _context(tmp_path: Path, rules: Mapping[str, RuleConfig] | None = None) -> CheckContext:
    config: Config = default_config()
    if rules is not None:
        config = dataclasses.replace(config, rules=dict(rules))
    return CheckContext.from_config(tmp_path, config)


def test_docstring_coverage_violation_emits_expected_finding(tmp_path: Path) -> None:
    source = '''"""Module docstring."""


def documented():
    """Documented."""
    return 1


def undocumented():
    return 2


class Documented:
    """Documented."""


class Undocumented:
    pass
'''
    _write(tmp_path, "docs.py", source)

    result = DocstringCoverageCheck().run(_context(tmp_path))

    assert result.status is Status.WARN
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.rule_id == "HT-DOC-001"
    assert finding.check == _RULE
    assert finding.category is Category.DOCUMENTATION
    assert finding.severity is Severity.LOW
    assert finding.symbol == "<module>"
    assert finding.location is not None
    assert finding.location.file == "docs.py"
    assert finding.location.line == 1
    assert dict(finding.properties) == {"documented": 3, "total": 5, "min_ratio": 0.8}
    assert finding.message == "only 3/5 items have docstrings (min 80%)"


def test_docstring_coverage_clean_file_emits_nothing(tmp_path: Path) -> None:
    source = '''"""Module."""


def documented_function():
    """Documented."""
    return 1


class DocumentedClass:
    """Documented."""
'''
    _write(tmp_path, "clean.py", source)

    result = DocstringCoverageCheck().run(_context(tmp_path))

    assert result.findings == ()
    assert result.status is Status.PASS


def test_docstring_coverage_at_min_ratio_is_clean(tmp_path: Path) -> None:
    # No module docstring: the absent module entry still counts (4/5 == 80%).
    _write(tmp_path, "four_of_five.py", _FOUR_OF_FIVE)

    result = DocstringCoverageCheck().run(_context(tmp_path))

    assert result.findings == ()
    assert result.status is Status.PASS


def test_docstring_coverage_min_ratio_is_configurable(tmp_path: Path) -> None:
    _write(tmp_path, "strict.py", _FOUR_OF_FIVE)
    rules = {_RULE: RuleConfig(options={"min_ratio": 0.9})}

    result = DocstringCoverageCheck().run(_context(tmp_path, rules))

    assert len(result.findings) == 1
    finding = result.findings[0]
    assert dict(finding.properties) == {"documented": 4, "total": 5, "min_ratio": 0.9}
    assert finding.message == "only 4/5 items have docstrings (min 90%)"


def test_module_docstring_entry_always_counts(tmp_path: Path) -> None:
    source = '''def documented_function():
    """Documented."""
    return 1
'''
    _write(tmp_path, "no_module_doc.py", source)

    result = DocstringCoverageCheck().run(_context(tmp_path))

    assert len(result.findings) == 1
    assert dict(result.findings[0].properties) == {"documented": 1, "total": 2, "min_ratio": 0.8}


def test_docstring_coverage_ignores_private_definitions(tmp_path: Path) -> None:
    source = '''"""Module."""


def documented_function():
    """Documented."""
    return 1


def _undocumented_helper():
    return 2


def _another_undocumented_helper():
    return 3


class _UndocumentedPrivate:
    pass
'''
    _write(tmp_path, "private.py", source)

    result = DocstringCoverageCheck().run(_context(tmp_path))

    assert result.findings == ()
    assert result.status is Status.PASS
