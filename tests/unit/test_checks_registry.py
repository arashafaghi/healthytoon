"""Unit tests for the built-in check package and its registry integration."""

from __future__ import annotations

from healthytoon import checks
from healthytoon.core import registry
from healthytoon.core.protocols import CHECK_API_VERSION, Check
from healthytoon.core.types import Category, FileKind, Severity

_EXPECTED_IDS: tuple[str, ...] = (
    "maintainability.function_length",
    "maintainability.nesting_depth",
    "maintainability.module_length",
    "typing.annotation_coverage",
    "documentation.docstring_coverage",
    "maintainability.import_cycles",
    "maintainability.duplication",
    "maintainability.complexity",
    "maintainability.dead_code",
)

#: Structural checks that operate on a single file's AST.
_AST_STRUCTURAL_IDS: frozenset[str] = frozenset(
    {
        "maintainability.function_length",
        "maintainability.nesting_depth",
        "maintainability.module_length",
    }
)


def test_builtin_checks_expose_ids_in_stable_order() -> None:
    assert tuple(check.id for check in checks.builtin_checks()) == _EXPECTED_IDS


def test_builtin_checks_are_stable_across_registry_calls() -> None:
    from_package = tuple(check.id for check in checks.builtin_checks())
    from_registry = tuple(check.id for check in registry.builtin_checks())

    assert from_package == from_registry == _EXPECTED_IDS


def test_builtin_checks_satisfy_the_check_protocol() -> None:
    for check in checks.builtin_checks():
        assert isinstance(check, Check)
        assert check.api_version == CHECK_API_VERSION
        assert isinstance(check.cross_file, bool)
        assert check.file_kinds == frozenset({FileKind.PYTHON})
        assert check.category in (
            Category.MAINTAINABILITY,
            Category.TYPING,
            Category.DOCUMENTATION,
        )
        assert isinstance(check.default_severity, Severity)


def test_structural_ast_checks_are_single_file() -> None:
    for check in checks.builtin_checks():
        if check.id in _AST_STRUCTURAL_IDS:
            assert check.cross_file is False


def test_registry_all_checks_includes_builtins_first() -> None:
    checks_by_id = registry.all_checks()

    assert tuple(checks_by_id)[: len(_EXPECTED_IDS)] == _EXPECTED_IDS
    for check_id in _EXPECTED_IDS:
        assert checks_by_id[check_id].id == check_id


def test_registry_get_check_returns_builtins() -> None:
    for check_id in _EXPECTED_IDS:
        found = registry.get_check(check_id)
        assert found is not None
        assert found.id == check_id
