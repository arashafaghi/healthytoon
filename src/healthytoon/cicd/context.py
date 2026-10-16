"""Build the shared :class:`~healthytoon.cicd.ir.CIContext` for CI/CD checks.

CI/CD checks do not consume a single file kind; they reason over repository-level
artifacts (workflows, lockfiles, dependency-update config). Parsing those
artifacts once per run keeps the checks cheap and deterministic, so
:func:`build_context` memoizes the result per resolved repository root and
:func:`clear_cache` lets tests drop the memo.

``today`` is captured here so the EOL checks can compare against a single,
stable date for the whole run.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from healthytoon.cicd.data import eol_data_as_of, load_eol
from healthytoon.cicd.github import collect_artifacts
from healthytoon.cicd.ir import CIContext
from healthytoon.config.models import Config

__all__ = ["build_context", "clear_cache"]

#: One parsed context per resolved repository root for the lifetime of the process.
_CACHE: dict[str, CIContext] = {}


def build_context(root: Path, config: Config) -> CIContext:
    """Return a memoized :class:`CIContext` for ``root``.

    ``config`` is accepted so every CI check can bridge from a
    :class:`~healthytoon.core.context.CheckContext` uniformly; the artifacts
    themselves are repository-derived and do not depend on configuration.
    """

    root_path = Path(root)
    key = str(root_path.resolve())
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    context = CIContext(
        root=root_path,
        artifacts=collect_artifacts(root_path),
        python_eol=load_eol(),
        data_as_of=eol_data_as_of(),
        today=date.today().isoformat(),
    )
    _CACHE[key] = context
    return context


def clear_cache() -> None:
    """Drop every memoized context (used by tests)."""

    _CACHE.clear()
