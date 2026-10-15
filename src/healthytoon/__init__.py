"""Healthytoon: audit Python repositories for maintainability and CI/CD health."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _distribution_version

try:
    __version__ = _distribution_version("healthytoon")
except PackageNotFoundError:  # running from a source tree that is not installed
    __version__ = "0.1.0"

__all__ = ["__version__"]
