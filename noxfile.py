"""Nox sessions for Healthytoon.

This file defines the developer and CI automation entry points. Nox manages one
virtual environment per session; because this is a ``uv`` project, each session
first syncs the locked project and dev dependencies with ``uv`` and then runs
its commands through ``uv run --no-sync`` so no second resolution happens.

Sessions
--------
format
    Apply formatting with ``ruff format``.
lint
    Run ``ruff check`` and strict ``mypy`` against ``src``.
tests-3.12 / tests-3.13
    Run the full test suite on every supported Python version.
check
    The CI gate: formatting check, lint, strict typing, and the full test suite
    with a coverage floor. This is the default session.
release
    Build a distribution locally; publish to a remote index with ``--remote``.

Examples
--------
    uv run --no-sync nox -l
    uv run --no-sync nox -s check
    uv run --no-sync nox -s release              # local build only
    uv run --no-sync nox -s release -- --remote  # build and publish
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import cast

import nox

# Supported interpreters for the test matrix (matches pyproject requires-python).
PYTHON_VERSIONS = ["3.12", "3.13"]

# Minimum line coverage enforced by the ``check`` session.
COVERAGE_FAIL_UNDER = "90"

nox.options.sessions = ["check"]
nox.options.reuse_existing_virtualenvs = True
nox.options.default_venv_backend = "uv"


def _sync(session: nox.Session) -> None:
    """Install the locked project and dev dependencies into the session env.

    ``UV_PROJECT_ENVIRONMENT`` pins ``uv`` to the interpreter Nox selected for
    this session (including the per-version test matrix), instead of the
    repository's default ``.venv``.
    """
    session.env["UV_PROJECT_ENVIRONMENT"] = str(session.virtualenv.location)
    session.run("uv", "sync", "--locked")


def _uv(session: nox.Session, *args: str) -> None:
    """Run a command inside the already-synced project environment."""
    session.run("uv", "run", "--no-sync", *args)


def _project_version() -> str:
    """Read ``project.version`` from ``pyproject.toml``."""
    with Path("pyproject.toml").open("rb") as handle:
        data = tomllib.load(handle)
    return cast(str, data["project"]["version"])


def _has_publish_credentials() -> bool:
    """Return whether remote publish credentials appear to be configured."""
    if os.environ.get("UV_PUBLISH_TOKEN"):
        return True
    if os.environ.get("UV_PUBLISH_USERNAME") or os.environ.get("UV_PUBLISH_PASSWORD"):
        return True
    return Path.home().joinpath(".pypirc").is_file()


@nox.session(name="format")
def format_code(session: nox.Session) -> None:
    """Apply formatting with ``ruff format``."""
    _sync(session)
    _uv(session, "ruff", "format", ".")


@nox.session
def lint(session: nox.Session) -> None:
    """Run ``ruff check`` and strict ``mypy``."""
    _sync(session)
    _uv(session, "ruff", "check", ".")
    _uv(session, "mypy", "src")


@nox.session(python=PYTHON_VERSIONS)
def tests(session: nox.Session) -> None:
    """Run the full test suite on every supported Python version."""
    _sync(session)
    _uv(session, "pytest")


@nox.session
def check(session: nox.Session) -> None:
    """Run the CI gate: format check, lint, typing, and coverage tests."""
    _sync(session)
    _uv(session, "ruff", "format", "--check", ".")
    _uv(session, "ruff", "check", ".")
    _uv(session, "mypy", "src")
    _uv(
        session,
        "pytest",
        "--cov=healthytoon",
        "--cov-report=term-missing",
        f"--cov-fail-under={COVERAGE_FAIL_UNDER}",
    )


@nox.session
def release(session: nox.Session) -> None:
    """Build a distribution and optionally publish it to a remote index.

    Pass ``--remote`` after ``--`` to publish, e.g.
    ``nox -s release -- --remote``. The remote path refuses to run without
    credentials so it can never block waiting for interactive input.
    """
    _sync(session)
    remote = "--remote" in session.posargs
    version = _project_version()
    session.log(f"Building healthytoon {version}")

    # ``build`` and ``publish`` are uv subcommands, not executables, so they are
    # invoked directly rather than through ``uv run``.
    session.run("uv", "build")

    if not remote:
        session.log("Local build complete (no publish). Use '-- --remote' to publish.")
        return

    if not _has_publish_credentials():
        session.error(
            "No publish credentials found. Set UV_PUBLISH_TOKEN "
            "(or UV_PUBLISH_USERNAME/UV_PUBLISH_PASSWORD), configure ~/.pypirc, "
            "or use trusted publishing. Refusing to run 'uv publish' because it "
            "could block waiting for interactive credentials."
        )

    session.log(f"Publishing healthytoon {version} to the configured remote index")
    session.run("uv", "publish")
