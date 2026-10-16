"""Unit tests for :mod:`healthytoon.core.registry`."""

from __future__ import annotations

import importlib.metadata
from collections.abc import Callable
from typing import Any

import pytest

from healthytoon.checks.structure import FunctionLengthCheck
from healthytoon.core import registry
from healthytoon.core.context import CheckContext
from healthytoon.core.protocols import CHECK_API_VERSION, CheckResult
from healthytoon.core.types import Category, Severity


class FakeCheck:
    """Minimal concrete check satisfying the :class:`Check` protocol."""

    id: str
    category: Category = Category.MAINTAINABILITY
    default_severity: Severity = Severity.LOW
    api_version: int = CHECK_API_VERSION
    cross_file: bool = False

    def __init__(self, check_id: str = "fake.check") -> None:
        self.id = check_id

    def supports(self, ctx: CheckContext) -> bool:
        return True

    def run(self, ctx: CheckContext) -> CheckResult:
        return CheckResult(check=self.id)


class FakeEntryPoint:
    """Stand-in for :class:`importlib.metadata.EntryPoint`."""

    def __init__(self, loader: Callable[[], object], name: str = "fake") -> None:
        self._loader = loader
        self.name = name
        self.group = registry.CHECK_ENTRY_POINT_GROUP

    def load(self) -> object:
        return self._loader()


class LegacyEntryPoints:
    """Stand-in for the pre-3.12 dict-like collection with ``.get(group=...)``."""

    def __init__(self, points: tuple[FakeEntryPoint, ...]) -> None:
        self._points = points

    def get(self, group: str | None = None, default: Any = None) -> Any:
        if group == registry.CHECK_ENTRY_POINT_GROUP:
            return self._points
        return default


def _patch_entry_points(monkeypatch: pytest.MonkeyPatch, points: object) -> None:
    def fake_entry_points(**kwargs: object) -> object:
        return points

    monkeypatch.setattr(importlib.metadata, "entry_points", fake_entry_points)


#: Stable registration order of the checks bundled with Healthytoon
#: (T2a.1a/T2a.1b maintainability, then T2b.3/T2b.4 CI/CD).
_BUILTIN_IDS: tuple[str, ...] = (
    "maintainability.function_length",
    "maintainability.nesting_depth",
    "maintainability.module_length",
    "typing.annotation_coverage",
    "documentation.docstring_coverage",
    "maintainability.import_cycles",
    "maintainability.duplication",
    "maintainability.complexity",
    "maintainability.dead_code",
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


def test_builtin_checks_returns_builtins_in_stable_order() -> None:
    assert tuple(check.id for check in registry.builtin_checks()) == _BUILTIN_IDS


def test_all_checks_is_builtins_without_plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_points(monkeypatch, ())

    checks = registry.all_checks()

    assert tuple(checks) == _BUILTIN_IDS
    assert all(checks[check_id].id == check_id for check_id in checks)


def test_get_check_returns_none_for_unknown_id() -> None:
    assert registry.get_check("does.not.exist") is None


def test_plugin_entry_point_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = FakeCheck("plugin.valid")
    _patch_entry_points(monkeypatch, (FakeEntryPoint(lambda: expected),))

    assert registry.plugin_checks() == (expected,)
    checks = registry.all_checks()
    # The non-colliding plugin is appended after every built-in, in order.
    assert tuple(checks) == (*_BUILTIN_IDS, "plugin.valid")
    assert checks["plugin.valid"] is expected
    assert registry.get_check("plugin.valid") is expected


def test_plugin_factory_is_called(monkeypatch: pytest.MonkeyPatch) -> None:
    def factory() -> FakeCheck:
        return FakeCheck("plugin.factory")

    _patch_entry_points(monkeypatch, (FakeEntryPoint(factory),))

    assert [check.id for check in registry.plugin_checks()] == ["plugin.factory"]


def test_plugin_class_is_instantiated(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_points(monkeypatch, (FakeEntryPoint(lambda: FakeCheck),))

    checks = registry.plugin_checks()

    assert len(checks) == 1
    assert isinstance(checks[0], FakeCheck)
    assert checks[0].id == "fake.check"


def test_entry_point_that_raises_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> object:
        raise RuntimeError("cannot import plugin")

    _patch_entry_points(monkeypatch, (FakeEntryPoint(boom),))

    assert registry.plugin_checks() == ()


def test_entry_point_without_usable_id_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    class NoId:
        pass

    _patch_entry_points(
        monkeypatch,
        (
            FakeEntryPoint(lambda: NoId(), name="no-id"),
            FakeEntryPoint(lambda: object(), name="empty-id-object"),
            FakeEntryPoint(lambda: FakeCheck(""), name="empty-id"),
        ),
    )

    assert registry.plugin_checks() == ()


def test_duplicate_plugin_id_loses_to_builtin(monkeypatch: pytest.MonkeyPatch) -> None:
    colliding_id = "maintainability.function_length"
    plugin = FakeCheck(colliding_id)
    _patch_entry_points(monkeypatch, (FakeEntryPoint(lambda: plugin),))

    checks = registry.all_checks()

    # builtin_checks() builds fresh instances per call, so compare by type:
    # the registered winner must be the real built-in, not the colliding plugin.
    assert isinstance(checks[colliding_id], FunctionLengthCheck)
    assert checks[colliding_id] is not plugin
    assert tuple(checks) == _BUILTIN_IDS
    assert isinstance(registry.get_check(colliding_id), FunctionLengthCheck)


def test_builtin_only_lookup_skips_plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_points(monkeypatch, (FakeEntryPoint(lambda: FakeCheck("plugin.only")),))

    assert tuple(registry.all_checks(with_plugins=False)) == _BUILTIN_IDS
    assert registry.get_check("plugin.only", with_plugins=False) is None


def test_legacy_get_api_is_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = FakeCheck("plugin.legacy")

    def legacy_entry_points(**kwargs: object) -> object:
        if kwargs:
            raise TypeError("entry_points() takes no arguments")
        return LegacyEntryPoints((FakeEntryPoint(lambda: expected),))

    monkeypatch.setattr(importlib.metadata, "entry_points", legacy_entry_points)

    assert registry.plugin_checks() == (expected,)


def test_broken_entry_point_metadata_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken_entry_points(**kwargs: object) -> object:
        raise RuntimeError("corrupt metadata")

    monkeypatch.setattr(importlib.metadata, "entry_points", broken_entry_points)

    assert registry.plugin_checks() == ()
