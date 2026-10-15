"""Reporter registry.

Reporters are resolved lazily by name so importing this package never requires
every format module to exist or import cleanly. Each module exports a class
named per :data:`_REPORTERS` implementing :class:`healthytoon.core.protocols.Reporter`.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from healthytoon.core.protocols import Reporter

__all__ = ["available", "get_reporter"]

_REPORTERS: dict[str, tuple[str, str]] = {
    "terminal": ("healthytoon.reporters.terminal", "TerminalReporter"),
    "json": ("healthytoon.reporters.json", "JsonReporter"),
    "sarif": ("healthytoon.reporters.sarif", "SarifReporter"),
    "markdown": ("healthytoon.reporters.markdown", "MarkdownReporter"),
}


def available() -> tuple[str, ...]:
    """Return the registered format ids in deterministic order."""

    return tuple(sorted(_REPORTERS))


def get_reporter(name: str) -> Reporter:
    """Instantiate the reporter registered under ``name``."""

    try:
        module_name, attribute = _REPORTERS[name]
    except KeyError:
        raise ValueError(f"unknown report format: {name!r}") from None
    module = importlib.import_module(module_name)
    return cast("Reporter", getattr(module, attribute)())
