"""Runtime availability probes for optional analysis backends.

Two built-in checks depend on backends that are deliberately *not* runtime
dependencies (ROADMAP Phase 2a decision 9):

* ``maintainability.complexity`` needs the third-party ``radon`` package, which
  ships in the ``analysis`` extra but may be absent from a user's environment.
* ``maintainability.dead_code`` needs an external ``vulture`` executable.

Both checks must degrade to a SKIP result when their backend is missing rather
than silently passing or crashing. Each probe is a small, stdlib-only function
so a test can monkeypatch exactly one seam. Callers must look the function up
through this module (``_optional.radon_module()``) so monkeypatching works.
"""

from __future__ import annotations

import importlib
import shutil
from types import ModuleType

__all__ = ["radon_module", "vulture_executable"]


def radon_module() -> ModuleType | None:
    """Return the ``radon.complexity`` module, or ``None`` when Radon is absent.

    Imported lazily (through :func:`importlib.import_module`) so importing this
    package never requires Radon and a missing dependency cannot break the other
    checks. ``ImportError`` covers both "not installed" and a broken install.
    """

    try:
        return importlib.import_module("radon.complexity")
    except ImportError:
        return None


def vulture_executable() -> str | None:
    """Return the path to the ``vulture`` executable, or ``None`` when absent."""

    return shutil.which("vulture")
