"""Unit tests for the built-in check package and its registry integration."""

from __future__ import annotations

from healthytoon import checks
from healthytoon.core import registry
from healthytoon.core.protocols import CHECK_API_VERSION, Check
from healthytoon.core.types import Category, FileKind, Severity

#: Maintainability/typing/documentation checks owned by :mod:`healthytoon.checks`.
_MAINTAINABILITY_IDS: tuple[str, ...] = (
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

#: CI/CD checks owned by :mod:`healthytoon.cicd` (P0 then P1).
_CI_IDS: tuple[str, ...] = (
    "ci.workflow_present",
    "ci.workflow_parse",
    "ci.tests_present",
    "ci.actions_pinned",
    "ci.permissions_explicit",
    "ci.secrets",
    "ci.python_eol",
    "ci.python_matrix",
    "ci.python_consistency",
    "ci.coverage_gate",
    "ci.lockfile",
    "ci.dependency_updates",
    "ci.triggers_pull_request",
)

#: Full built-in registration order exposed by the registry.
_EXPECTED_IDS: tuple[str, ...] = _MAINTAINABILITY_IDS + _CI_IDS

#: Structural checks that operate on a single file's AST.
_AST_STRUCTURAL_IDS: frozenset[str] = frozenset(
    {
        "maintainability.function_length",
        "maintainability.nesting_depth",
        "maintainability.module_length",
    }
)


def test_builtin_checks_expose_ids_in_stable_order() -> None:
    assert tuple(check.id for check in checks.builtin_checks()) == _MAINTAINABILITY_IDS


def test_builtin_checks_are_stable_across_registry_calls() -> None:
    from_package = tuple(check.id for check in checks.builtin_checks())
    from_registry = tuple(check.id for check in registry.builtin_checks())

    assert from_package == _MAINTAINABILITY_IDS
    assert from_registry == _EXPECTED_IDS


def _assert_builtin_check_contract(check: Check) -> None:
    assert isinstance(check, Check)
    assert check.api_version == CHECK_API_VERSION
    assert isinstance(check.cross_file, bool)
    assert isinstance(check.default_severity, Severity)
    if check.id in _CI_IDS:
        assert check.file_kinds == frozenset()
        assert check.category in (Category.CI_CD, Category.SECURITY)
    else:
        assert check.file_kinds == frozenset({FileKind.PYTHON})
        assert check.category in (
            Category.MAINTAINABILITY,
            Category.TYPING,
            Category.DOCUMENTATION,
        )


def test_builtin_checks_satisfy_the_check_protocol() -> None:
    for check in registry.builtin_checks():
        _assert_builtin_check_contract(check)


def test_structural_ast_checks_are_single_file() -> None:
    for check in checks.builtin_checks():
        if check.id in _AST_STRUCTURAL_IDS:
            assert check.cross_file is False


def test_ci_checks_are_cross_file() -> None:
    ci_checks = {check.id: check for check in registry.builtin_checks() if check.id in _CI_IDS}

    assert tuple(ci_checks) == _CI_IDS
    assert all(check.cross_file is True for check in ci_checks.values())


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
