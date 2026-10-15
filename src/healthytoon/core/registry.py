"""Check registry: built-in engines first, then third-party plugins.

Phase 1 ships no built-in engines. Later phases register them in
:func:`builtin_checks`; plugins are discovered through the
``healthytoon.checks`` entry-point group. Discovery is deliberately forgiving:
a broken plugin must never crash the tool, it is simply skipped.
"""

from __future__ import annotations

import importlib
import importlib.metadata
from collections.abc import Mapping
from typing import Any, cast

from healthytoon.core.protocols import Check

__all__ = ["all_checks", "builtin_checks", "get_check", "plugin_checks"]

#: Entry-point group third-party checks register under.
CHECK_ENTRY_POINT_GROUP = "healthytoon.checks"

#: Module that exposes the bundled checks via a ``builtin_checks()`` factory.
_BUILTIN_MODULE = "healthytoon.checks"


def builtin_checks() -> tuple[Check, ...]:
    """Return the checks bundled with Healthytoon itself.

    The audit engines live in :mod:`healthytoon.checks`; they are imported
    lazily so that ``import healthytoon.core.registry`` (or a check importing
    this module) does not eagerly drag every engine in. The engines are
    expected to expose a stable-order ``builtin_checks()`` factory.
    """

    try:
        module = importlib.import_module(_BUILTIN_MODULE)
    except ImportError:
        return ()
    factory = getattr(module, "builtin_checks", None)
    if factory is None:
        return ()
    return tuple(factory())


def plugin_checks() -> tuple[Check, ...]:
    """Discover third-party checks via the ``healthytoon.checks`` entry points.

    Every entry point is loaded defensively: anything that fails to load, is
    not a check instance or zero-arg factory/class producing one, or lacks a
    non-empty string ``id`` is skipped rather than raising.
    """

    try:
        candidates = _entry_points_for(CHECK_ENTRY_POINT_GROUP)
    except Exception:  # noqa: BLE001 - broken plugin metadata must not crash discovery
        return ()
    checks: list[Check] = []
    for entry_point in candidates:
        check = _load_check(entry_point)
        if check is not None:
            checks.append(check)
    return tuple(checks)


def all_checks(*, with_plugins: bool = True) -> dict[str, Check]:
    """Return every available check keyed by ``id``.

    Built-ins come first; a plugin whose ``id`` collides with an already
    registered check is skipped (built-ins win).
    """

    checks: dict[str, Check] = {}
    for check in builtin_checks():
        if check.id not in checks:
            checks[check.id] = check
    if with_plugins:
        for check in plugin_checks():
            if check.id not in checks:
                checks[check.id] = check
    return checks


def get_check(check_id: str, *, with_plugins: bool = True) -> Check | None:
    """Return the check with ``check_id``, or None when it is not registered."""

    return all_checks(with_plugins=with_plugins).get(check_id)


def _entry_points_for(group: str) -> tuple[Any, ...]:
    """Return the entry points registered under ``group``.

    Uses the modern ``entry_points(group=...)`` API and, defensively, falls
    back to older no-argument forms exposing ``.get(group=...)``.
    """

    selected: Any
    try:
        selected = importlib.metadata.entry_points(group=group)
    except TypeError:
        # Older importlib.metadata versions accept no keyword arguments.
        selected = importlib.metadata.entry_points()
    return _grouped(selected, group)


def _grouped(selected: Any, group: str) -> tuple[Any, ...]:
    """Extract ``group``'s entry points from whichever collection shape we got."""

    if callable(getattr(selected, "load", None)):
        return (selected,)
    if isinstance(selected, Mapping):
        return tuple(selected.get(group, ()))
    getter = getattr(selected, "get", None)
    if callable(getter):
        try:
            return tuple(getter(group=group))
        except TypeError:
            pass
        try:
            return tuple(getter(group, ()))
        except TypeError:
            pass
    select = getattr(selected, "select", None)
    if callable(select):
        try:
            return tuple(select(group=group))
        except TypeError:
            pass
    try:
        items = tuple(selected)
    except TypeError:
        return ()
    return tuple(entry for entry in items if getattr(entry, "group", group) == group)


def _load_check(entry_point: Any) -> Check | None:
    """Load one entry point into a usable check, or return None to skip it."""

    try:
        loaded = entry_point.load()
        if isinstance(loaded, type) or not hasattr(loaded, "id"):
            if not callable(loaded):
                return None
            loaded = loaded()
        entry_id = getattr(loaded, "id", None)
    except Exception:  # noqa: BLE001 - a broken plugin must never break discovery
        return None
    if not isinstance(entry_id, str) or not entry_id:
        return None
    return cast("Check", loaded)
