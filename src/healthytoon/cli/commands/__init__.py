"""Subcommand implementations for the Healthytoon CLI.

The commands communicate the requested verbosity back to the CLI entry point
through a :class:`contextvars.ContextVar`: the entry point needs it when an
unexpected exception escapes a command and it decides whether to print a
traceback.
"""

from __future__ import annotations

import contextvars

__all__ = ["get_verbose_level", "set_verbose_level"]

_VERBOSE_LEVEL: contextvars.ContextVar[int] = contextvars.ContextVar(
    "healthytoon_verbose_level", default=0
)


def set_verbose_level(level: int) -> None:
    """Record the verbosity requested for the current invocation."""

    _VERBOSE_LEVEL.set(level)


def get_verbose_level() -> int:
    """Return the verbosity requested for the current invocation."""

    return _VERBOSE_LEVEL.get()
