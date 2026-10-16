"""``healthytoon check`` — run the check pipeline and render the report.

The command resolves configuration and CLI overrides, selects the registered
checks, runs them through the runner, renders the report with the chosen
reporter, and returns the policy gate's exit code.
"""

from __future__ import annotations

import dataclasses
import fnmatch
import os
import sys
from pathlib import Path
from typing import Annotated

import click
import typer

from healthytoon import __version__
from healthytoon.cli.commands import set_verbose_level
from healthytoon.config.loader import load_config
from healthytoon.config.models import Config
from healthytoon.core import registry
from healthytoon.core.context import CheckContext
from healthytoon.core.policy import exit_code
from healthytoon.core.protocols import Check
from healthytoon.core.runner import run_checks
from healthytoon.core.types import Severity, ToolInfo
from healthytoon.errors import TargetError, UsageError
from healthytoon.reporters import available, get_reporter

__all__ = ["run"]

_FAIL_ON_CHOICES = ("critical", "high", "medium", "low", "info", "none")
_COLOR_CHOICES = ("auto", "always", "never")


def run(
    path: Annotated[Path, typer.Argument(help="target directory (default: .)")] = Path("."),
    config: Annotated[
        Path | None,
        typer.Option("--config", "-c", help="explicit configuration file"),
    ] = None,
    format: Annotated[
        str | None,
        typer.Option(
            "--format",
            "-f",
            click_type=click.Choice(available()),
            help="report format",
        ),
    ] = None,
    output: Annotated[
        str | None,
        typer.Option("-o", "--output", help="write the report to PATH ('-' for stdout)"),
    ] = None,
    fail_on: Annotated[
        str | None,
        typer.Option(
            "--fail-on",
            click_type=click.Choice(_FAIL_ON_CHOICES),
            help="minimum severity that fails the gate",
        ),
    ] = None,
    only: Annotated[
        list[str] | None,
        typer.Option("--only", help="run only the named check (repeatable)"),
    ] = None,
    skip: Annotated[
        list[str] | None,
        typer.Option("--skip", help="skip the named check (repeatable)"),
    ] = None,
    exclude: Annotated[
        list[str] | None,
        typer.Option("--exclude", help="exclude a path glob (repeatable)"),
    ] = None,
    color: Annotated[
        str | None,
        typer.Option(
            "--color",
            click_type=click.Choice(_COLOR_CHOICES),
            help="color output mode",
        ),
    ] = None,
    verbose: Annotated[
        int,
        typer.Option("-v", "--verbose", count=True, help="increase verbosity"),
    ] = 0,
    quiet: Annotated[
        int,
        typer.Option("-q", "--quiet", count=True, help="suppress human output"),
    ] = 0,
    no_progress: Annotated[
        bool,
        typer.Option("--no-progress", help="disable progress reporting"),
    ] = False,
    jobs: Annotated[
        int | None,
        typer.Option("--jobs", help="maximum parallel workers"),
    ] = None,
) -> int:
    """Audit the target directory."""

    set_verbose_level(verbose)

    if not path.exists() or not path.is_dir():
        raise TargetError(f"target is not a directory: {path}")

    loaded = load_config(start=path, explicit=config)

    if exclude:
        loaded = dataclasses.replace(loaded, exclude=tuple(loaded.exclude) + tuple(exclude))
    if jobs is not None:
        loaded = dataclasses.replace(loaded, jobs=jobs)

    if fail_on == "none":
        threshold: Severity | None = None
    elif fail_on is not None:
        threshold = Severity.parse(fail_on)
    else:
        threshold = loaded.fail_on

    ctx = CheckContext.from_config(path, loaded)
    report = run_checks(
        _select_checks(only=only, skip=skip, config=loaded),
        ctx,
        tool=ToolInfo(version=__version__),
        jobs=loaded.jobs,
    )

    fmt = format or loaded.output.format
    try:
        reporter = get_reporter(fmt)
    except ValueError as exc:
        raise UsageError(str(exc)) from exc

    destination = output if output is not None else loaded.output.output
    resolved_color = _resolve_color(color or loaded.output.color, destination=destination)

    text = reporter.render(report, color=resolved_color)
    if not (quiet and fmt == "terminal"):
        _emit(text, destination)

    return exit_code(report, fail_on=threshold)


def _select_checks(
    *, only: list[str] | None, skip: list[str] | None, config: Config
) -> tuple[Check, ...]:
    """Return the registered checks filtered by config and ``--only``/``--skip``."""

    selected = {
        check_id: check
        for check_id, check in registry.all_checks().items()
        if check_id not in config.rules or config.rules[check_id].enabled
    }
    if only:
        selected = {
            check_id: check
            for check_id, check in selected.items()
            if any(fnmatch.fnmatchcase(check_id, pattern) for pattern in only)
        }
    if skip:
        selected = {
            check_id: check
            for check_id, check in selected.items()
            if not any(fnmatch.fnmatchcase(check_id, pattern) for pattern in skip)
        }
    return tuple(selected[check_id] for check_id in sorted(selected))


def _resolve_color(mode: str, *, destination: str) -> bool:
    """Translate a color mode (``auto``/``always``/``never``) into a boolean."""

    if mode == "always":
        return True
    if mode == "never":
        return False
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None and destination == "-"


def _emit(text: str, destination: str) -> None:
    """Write ``text`` to stdout (``-``) or to a file."""

    if destination == "-":
        sys.stdout.write(text)
    else:
        Path(destination).write_text(text, encoding="utf-8")
