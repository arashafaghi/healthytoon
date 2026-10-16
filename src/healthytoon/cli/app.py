"""Healthytoon command-line entry point, built with :mod:`typer`.

The command tree lives here; each command's implementation lives in
:mod:`healthytoon.cli.commands`. The public entry point :func:`main` keeps the
historical contract:

* it returns the process exit code instead of raising ``SystemExit`` for
  normal or error paths, and
* it maps every documented failure onto a distinct code
  (0 OK / 1 findings / 2 usage / 3 config / 4 target / 5 internal / 130
  interrupted).

It does so by invoking the Typer app with ``standalone_mode=False`` and
translating the exceptions Click/Typer raise back into return values.
"""

from __future__ import annotations

import sys
import traceback
from collections.abc import Sequence

import click
import typer

from healthytoon import __version__
from healthytoon.cli.commands import check as check_command
from healthytoon.cli.commands import get_verbose_level
from healthytoon.cli.commands import list as list_command
from healthytoon.cli.commands import version as version_command
from healthytoon.errors import EXIT_INTERNAL, EXIT_USAGE, HealthytoonError

__all__ = ["app", "main"]

_INTERRUPTED_EXIT = 130


def _configure_streams() -> None:
    """Make stdout tolerant of characters the active console cannot encode."""

    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(errors="replace")


def _version_callback(value: bool) -> None:
    """Print the tool version and exit (``healthytoon --version``)."""

    if value:
        typer.echo(f"healthytoon {__version__}")
        raise typer.Exit()


def _system_exit_code(exc: SystemExit) -> int:
    """Translate a stray ``SystemExit`` into a return code."""

    code = exc.code
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    print(code, file=sys.stderr)
    return EXIT_INTERNAL


app = typer.Typer(
    name="healthytoon",
    help="Audit Python repositories for maintainability and CI/CD health.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="print the Healthytoon version and exit",
    ),
) -> None:
    """Audit Python repositories for maintainability and CI/CD health."""


app.command("check")(check_command.run)
app.command("list")(list_command.run)
app.command("version")(version_command.run)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return its process exit code.

    ``argv`` defaults to ``sys.argv[1:]`` when ``None``. Nothing is raised for
    normal or error paths; the return value is always a valid exit code.
    """

    _configure_streams()
    args = list(argv) if argv is not None else None

    try:
        result = app(args=args, standalone_mode=False)
    except click.exceptions.Exit as exc:
        # ``typer.Exit`` is an alias of ``click.exceptions.Exit``; Click already
        # returns the code for help/version in non-standalone mode, so this is
        # a defensive guard.
        return int(exc.exit_code)
    except click.UsageError as exc:
        # ``click.BadParameter`` and ``typer.BadParameter`` are subclasses.
        exc.show()
        return EXIT_USAGE
    except click.exceptions.Abort:
        # ``typer.Abort`` is an alias of ``click.exceptions.Abort``.
        print("healthytoon: interrupted", file=sys.stderr)
        return _INTERRUPTED_EXIT
    except KeyboardInterrupt:
        print("healthytoon: interrupted", file=sys.stderr)
        return _INTERRUPTED_EXIT
    except HealthytoonError as exc:
        print(f"healthytoon: error: {exc}", file=sys.stderr)
        return exc.exit_code
    except SystemExit as exc:
        return _system_exit_code(exc)
    except Exception as exc:  # noqa: BLE001 - last-resort guard for the CLI boundary
        if get_verbose_level() >= 1:
            traceback.print_exc()
        print(f"healthytoon: internal error: {exc}", file=sys.stderr)
        return EXIT_INTERNAL

    return int(result) if result is not None else 0
