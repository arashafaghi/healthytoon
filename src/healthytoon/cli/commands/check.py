"""``healthytoon check`` — run the check pipeline and render the report.

The command resolves configuration and CLI overrides, selects the registered
checks, runs them through the runner, renders the report with the chosen
reporter, and returns the policy gate's exit code.
"""

from __future__ import annotations

import argparse
import dataclasses
import fnmatch
import os
import sys
from pathlib import Path

from healthytoon import __version__
from healthytoon.config.loader import load_config
from healthytoon.config.models import Config
from healthytoon.core import registry
from healthytoon.core.context import CheckContext
from healthytoon.core.policy import exit_code
from healthytoon.core.protocols import Check
from healthytoon.core.runner import run_checks
from healthytoon.core.types import Severity, ToolInfo
from healthytoon.errors import TargetError, UsageError
from healthytoon.reporters import get_reporter

__all__ = ["run"]


def run(args: argparse.Namespace) -> int:
    """Resolve config and checks, run them, render the report, and apply the gate."""

    target = Path(args.paths[0])
    if not target.exists() or not target.is_dir():
        raise TargetError(f"target is not a directory: {target}")

    explicit = Path(args.config) if args.config else None
    config = load_config(start=target, explicit=explicit)

    if args.exclude:
        config = dataclasses.replace(config, exclude=tuple(config.exclude) + tuple(args.exclude))
    if args.jobs is not None:
        config = dataclasses.replace(config, jobs=args.jobs)

    if args.fail_on == "none":
        fail_on: Severity | None = None
    elif args.fail_on is not None:
        fail_on = Severity.parse(args.fail_on)
    else:
        fail_on = config.fail_on

    ctx = CheckContext.from_config(target, config)
    report = run_checks(
        _select_checks(args, config),
        ctx,
        tool=ToolInfo(version=__version__),
        jobs=config.jobs,
    )

    fmt = args.format or config.output.format
    try:
        reporter = get_reporter(fmt)
    except ValueError as exc:
        raise UsageError(str(exc)) from exc

    destination = args.output if args.output is not None else config.output.output
    color = _resolve_color(args.color or config.output.color, destination=destination)

    text = reporter.render(report, color=color)
    if not (args.quiet and fmt == "terminal"):
        _emit(text, destination)

    return exit_code(report, fail_on=fail_on)


def _select_checks(args: argparse.Namespace, config: Config) -> tuple[Check, ...]:
    """Return the registered checks filtered by config and ``--only``/``--skip``."""

    selected = {
        check_id: check
        for check_id, check in registry.all_checks().items()
        if check_id not in config.rules or config.rules[check_id].enabled
    }
    if args.only:
        selected = {
            check_id: check
            for check_id, check in selected.items()
            if any(fnmatch.fnmatchcase(check_id, pattern) for pattern in args.only)
        }
    if args.skip:
        selected = {
            check_id: check
            for check_id, check in selected.items()
            if not any(fnmatch.fnmatchcase(check_id, pattern) for pattern in args.skip)
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
