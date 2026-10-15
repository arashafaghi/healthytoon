"""Configuration data model.

These dataclasses are the frozen contract consumed by the CLI, discovery, and
every check. Keep them standard-library only and immutable.

The ``loader`` submodule (owned by a separate task) is responsible for
discovering and merging configuration sources into a :class:`Config`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from healthytoon.core.types import Severity

__all__ = [
    "DEFAULT_EXCLUDES",
    "OutputConfig",
    "RuleConfig",
    "Config",
]

#: Path globs excluded by default. Matching is performed by ``core.discovery``.
#: The scan scope is the whole repository (``include = ("**",)``); these
#: exclusions keep generated, vendored, and VCS directories out of the audit.
DEFAULT_EXCLUDES: tuple[str, ...] = (
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    ".tox",
    ".nox",
    ".eggs",
    "__pycache__",
    "build",
    "dist",
    "site-packages",
    "*.egg-info",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "htmlcov",
)


@dataclass(frozen=True, slots=True)
class OutputConfig:
    format: str = "terminal"
    color: str = "auto"
    output: str = "-"


@dataclass(frozen=True, slots=True)
class RuleConfig:
    enabled: bool = True
    severity: Severity | None = None
    options: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True, slots=True)
class Config:
    include: tuple[str, ...] = ("**",)
    exclude: tuple[str, ...] = DEFAULT_EXCLUDES
    respect_gitignore: bool = True
    fail_on: Severity = Severity.HIGH
    baseline: str | None = None
    jobs: int = 0
    output: OutputConfig = field(default_factory=OutputConfig)
    rules: Mapping[str, RuleConfig] = field(default_factory=lambda: MappingProxyType({}))
    source: str | None = None
