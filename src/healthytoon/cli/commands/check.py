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
from healthytoon.core import baseline as baseline_support
from healthytoon.core import registry
from healthytoon.core.context import CheckContext
from healthytoon.core.policy import exit_code
from healthytoon.core.protocols import Check, Reporter
from healthytoon.core.runner import run_checks
from healthytoon.core.source import attach_context
from healthytoon.core.types import Report, Severity, ToolInfo
from healthytoon.errors import EXIT_OK, ConfigError, TargetError, UsageError
from healthytoon.reporters import available, get_reporter

__all__ = ["run"]

_FAIL_ON_CHOICES = ("critical", "high", "medium", "low", "info", "none")
_COLOR_CHOICES = ("auto", "always", "never")

# Module-level ``Annotated`` aliases keep the command signature readable (and
# the function under the length cap) without altering the parsed CLI contract.
_TargetArgument = Annotated[Path, typer.Argument(help="target directory (default: .)")]
_ConfigOption = Annotated[
    Path | None,
    typer.Option("--config", "-c", help="explicit configuration file"),
]
_FormatOption = Annotated[
    str | None,
    typer.Option(
        "--format",
        "-f",
        click_type=click.Choice(available()),
        help="report format",
    ),
]
_OutputOption = Annotated[
    str | None,
    typer.Option("-o", "--output", help="write the report to PATH ('-' for stdout)"),
]
_FailOnOption = Annotated[
    str | None,
    typer.Option(
        "--fail-on",
        click_type=click.Choice(_FAIL_ON_CHOICES),
        help="minimum severity that fails the gate",
    ),
]
_OnlyOption = Annotated[
    list[str] | None,
    typer.Option("--only", help="run only the named check (repeatable)"),
]
_SkipOption = Annotated[
    list[str] | None,
    typer.Option("--skip", help="skip the named check (repeatable)"),
]
_ExcludeOption = Annotated[
    list[str] | None,
    typer.Option("--exclude", help="exclude a path glob (repeatable)"),
]
_BaselineOption = Annotated[
    Path | None,
    typer.Option("--baseline", help="suppress findings recorded in this baseline file"),
]
_WriteBaselineOption = Annotated[
    Path | None,
    typer.Option(
        "--write-baseline",
        help="write the current findings to PATH as a baseline and exit",
    ),
]
_ColorOption = Annotated[
    str | None,
    typer.Option(
        "--color",
        click_type=click.Choice(_COLOR_CHOICES),
        help="color output mode",
    ),
]
_VerboseOption = Annotated[
    int,
    typer.Option("-v", "--verbose", count=True, help="increase verbosity"),
]
_QuietOption = Annotated[
    int,
    typer.Option("-q", "--quiet", count=True, help="suppress human output"),
]
_NoProgressOption = Annotated[
    bool,
    typer.Option("--no-progress", help="disable progress reporting"),
]
_JobsOption = Annotated[
    int | None,
    typer.Option("--jobs", help="maximum parallel workers"),
]
_ContextOption = Annotated[
    int | None,
    typer.Option("--context", help="show N lines of source context around each finding"),
]


def run(
    path: _TargetArgument = Path("."),
    config: _ConfigOption = None,
    format: _FormatOption = None,
    output: _OutputOption = None,
    fail_on: _FailOnOption = None,
    only: _OnlyOption = None,
    skip: _SkipOption = None,
    exclude: _ExcludeOption = None,
    baseline: _BaselineOption = None,
    write_baseline: _WriteBaselineOption = None,
    color: _ColorOption = None,
    verbose: _VerboseOption = 0,
    quiet: _QuietOption = 0,
    no_progress: _NoProgressOption = False,
    jobs: _JobsOption = None,
    context: _ContextOption = None,
) -> int:
    """Audit the target directory."""

    set_verbose_level(verbose)
    _require_directory(path)
    loaded = _load_effective_config(path, config, exclude=exclude, jobs=jobs)
    threshold = _resolve_threshold(fail_on, loaded)
    report = _run_report(path, loaded, only=only, skip=skip)

    if write_baseline is not None:
        return _write_baseline(write_baseline, report)

    # Suppress baselined findings before rendering and before the gate is
    # evaluated, so a fully-baselined repository exits 0.
    report = _apply_baseline(report, explicit=baseline, configured=loaded.baseline)
    context_lines = context if context is not None else loaded.output.context
    report = attach_context(report, path, context_lines)
    return _render_and_gate(
        report,
        report_format=format,
        output=output,
        color=color,
        quiet=quiet,
        threshold=threshold,
        config=loaded,
    )


def _require_directory(path: Path) -> None:
    """Raise :class:`TargetError` unless ``path`` is an existing directory."""

    if not path.exists() or not path.is_dir():
        raise TargetError(f"target is not a directory: {path}")


def _load_effective_config(
    path: Path,
    explicit: Path | None,
    *,
    exclude: list[str] | None,
    jobs: int | None,
) -> Config:
    """Load configuration and fold in the ``--exclude``/``--jobs`` overrides."""

    loaded = load_config(start=path, explicit=explicit)
    if exclude:
        loaded = dataclasses.replace(loaded, exclude=tuple(loaded.exclude) + tuple(exclude))
    if jobs is not None:
        loaded = dataclasses.replace(loaded, jobs=jobs)
    return loaded


def _resolve_threshold(fail_on: str | None, config: Config) -> Severity | None:
    """Resolve the gate; ``none`` disables it and ``None`` defers to config."""

    if fail_on == "none":
        return None
    if fail_on is not None:
        return Severity.parse(fail_on)
    return config.fail_on


def _run_report(
    path: Path, config: Config, *, only: list[str] | None, skip: list[str] | None
) -> Report:
    """Run the selected checks over ``path`` and return the aggregated report."""

    ctx = CheckContext.from_config(path, config)
    return run_checks(
        _select_checks(only=only, skip=skip, config=config),
        ctx,
        tool=ToolInfo(version=__version__),
        jobs=config.jobs,
    )


def _write_baseline(destination: Path, report: Report) -> int:
    """Persist the report's fingerprints as a baseline and never gate."""

    baseline_support.write_baseline(destination, report)
    recorded = sum(1 for finding in report.findings if finding.fingerprint)
    typer.echo(f"Wrote {recorded} fingerprint(s) to {destination}")
    return EXIT_OK


def _render_and_gate(
    report: Report,
    *,
    report_format: str | None,
    output: str | None,
    color: str | None,
    quiet: int,
    threshold: Severity | None,
    config: Config,
) -> int:
    """Render ``report`` to the chosen destination and return the gate code."""

    fmt = report_format or config.output.format
    reporter = _get_reporter(fmt)
    destination = output if output is not None else config.output.output
    resolved_color = _resolve_color(color or config.output.color, destination=destination)

    text = reporter.render(report, color=resolved_color)
    if not (quiet and fmt == "terminal"):
        _emit(text, destination)

    return exit_code(report, fail_on=threshold)


def _get_reporter(name: str) -> Reporter:
    """Resolve ``name`` to a reporter, mapping unknown formats onto usage errors."""

    try:
        return get_reporter(name)
    except ValueError as exc:
        raise UsageError(str(exc)) from exc


def _select_checks(
    *, only: list[str] | None, skip: list[str] | None, config: Config
) -> tuple[Check, ...]:
    """Return the registered checks filtered by config and ``--only``/``--skip``."""

    selected = {
        check_id: check
        for check_id, check in registry.all_checks().items()
        if _is_enabled(check_id, config)
    }
    if only:
        selected = {
            check_id: check for check_id, check in selected.items() if _matches_any(check_id, only)
        }
    if skip:
        selected = {
            check_id: check
            for check_id, check in selected.items()
            if not _matches_any(check_id, skip)
        }
    return tuple(selected[check_id] for check_id in sorted(selected))


def _is_enabled(check_id: str, config: Config) -> bool:
    """Return whether a check is enabled, defaulting to on when unconfigured."""

    rule = config.rules.get(check_id)
    return rule is None or rule.enabled


def _matches_any(name: str, patterns: list[str]) -> bool:
    """Return whether ``name`` matches any of the fnmatch ``patterns``."""

    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


def _apply_baseline(report: Report, *, explicit: Path | None, configured: str | None) -> Report:
    """Apply the effective baseline, if any, to ``report``.

    ``--baseline`` takes precedence over ``config.baseline``. A missing
    explicitly-requested file is an error; a missing configured file is a
    no-op so a stale configuration cannot break every run.
    """

    if explicit is not None:
        source = explicit
    elif configured:
        source = Path(configured)
    else:
        return report

    if source.exists():
        return baseline_support.apply_baseline(report, baseline_support.load_baseline(source))
    if explicit is not None:
        raise ConfigError(f"{source}: baseline file not found")
    return report


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
