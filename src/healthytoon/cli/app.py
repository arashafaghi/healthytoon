"""Healthytoon command-line entry point and argument parsing.

The parser is built from the standard library's :mod:`argparse` only. Argument
errors therefore keep argparse's own ``SystemExit(2)``; only failures raised by
the command handlers are translated into the documented exit codes.
"""

from __future__ import annotations

import argparse
import sys
import traceback
from collections.abc import Sequence

from healthytoon import __version__
from healthytoon.cli.commands import check as check_command
from healthytoon.cli.commands import list as list_command
from healthytoon.cli.commands import version as version_command
from healthytoon.errors import EXIT_INTERNAL, HealthytoonError
from healthytoon.reporters import available

__all__ = ["build_parser", "main"]

_INTERRUPTED_EXIT = 130


def _configure_streams() -> None:
    """Make stdout tolerant of characters the active console cannot encode."""

    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(errors="replace")


def build_parser() -> argparse.ArgumentParser:
    """Build the ``healthytoon`` argument parser."""

    parser = argparse.ArgumentParser(
        prog="healthytoon",
        description="Audit Python repositories for maintainability and CI/CD health.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"healthytoon {__version__}",
        help="print the Healthytoon version and exit",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("check", help="audit the target directory")
    check.add_argument("paths", nargs="*", default=["."], help="target directory (default: .)")
    check.add_argument("--config", metavar="PATH", help="explicit configuration file")
    check.add_argument(
        "--format",
        choices=available(),
        help="report format",
    )
    check.add_argument(
        "-o",
        "--output",
        metavar="PATH",
        help="write the report to PATH ('-' for stdout)",
    )
    check.add_argument(
        "--fail-on",
        choices=("critical", "high", "medium", "low", "info", "none"),
        help="minimum severity that fails the gate",
    )
    check.add_argument("--only", action="append", help="run only the named check (repeatable)")
    check.add_argument("--skip", action="append", help="skip the named check (repeatable)")
    check.add_argument("--exclude", action="append", help="exclude a path glob (repeatable)")
    check.add_argument("--color", choices=("auto", "always", "never"), help="color output mode")
    check.add_argument("-v", "--verbose", action="count", default=0, help="increase verbosity")
    check.add_argument("-q", "--quiet", action="count", default=0, help="suppress human output")
    check.add_argument("--no-progress", action="store_true", help="disable progress reporting")
    check.add_argument("--jobs", type=int, default=None, help="maximum parallel workers")
    check.set_defaults(handler=check_command.run)

    list_parser = subparsers.add_parser("list", help="list the registered checks")
    list_parser.add_argument("--category", help="restrict to a single category")
    list_parser.set_defaults(handler=list_command.run)

    version = subparsers.add_parser("version", help="print version information")
    version.set_defaults(handler=version_command.run)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse ``argv``, dispatch to a command, and return an exit code.

    argparse's own ``SystemExit`` (raised for ``--help``, ``--version``, and bad
    usage) is deliberately not caught so it propagates unchanged.
    """

    parser = build_parser()
    args = parser.parse_args(argv)

    _configure_streams()

    try:
        return int(args.handler(args))
    except HealthytoonError as exc:
        print(f"healthytoon: error: {exc}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("healthytoon: interrupted", file=sys.stderr)
        return _INTERRUPTED_EXIT
    except Exception as exc:  # noqa: BLE001 - last-resort guard for the CLI boundary
        if getattr(args, "verbose", 0) >= 1:
            traceback.print_exc()
        print(f"healthytoon: internal error: {exc}", file=sys.stderr)
        return EXIT_INTERNAL
