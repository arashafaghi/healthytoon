"""Exception hierarchy and exit-code contract.

Every user-facing failure maps to a distinct exit code so CI can tell "the
gate failed" (1) apart from "the tool was misused or the environment was
wrong" (2-5).
"""

from __future__ import annotations

__all__ = [
    "EXIT_OK",
    "EXIT_FINDINGS",
    "EXIT_USAGE",
    "EXIT_CONFIG",
    "EXIT_TARGET",
    "EXIT_INTERNAL",
    "HealthytoonError",
    "UsageError",
    "ConfigError",
    "TargetError",
    "InternalError",
]

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2
EXIT_CONFIG = 3
EXIT_TARGET = 4
EXIT_INTERNAL = 5


class HealthytoonError(Exception):
    """Base class for all Healthytoon errors that map to an exit code."""

    exit_code: int = EXIT_INTERNAL


class UsageError(HealthytoonError):
    """Bad command-line usage."""

    exit_code = EXIT_USAGE


class ConfigError(HealthytoonError):
    """Configuration is missing, unreadable, or invalid."""

    exit_code = EXIT_CONFIG


class TargetError(HealthytoonError):
    """The target path is missing, not a directory, or has nothing to analyze."""

    exit_code = EXIT_TARGET


class InternalError(HealthytoonError):
    """An unexpected internal failure."""

    exit_code = EXIT_INTERNAL
