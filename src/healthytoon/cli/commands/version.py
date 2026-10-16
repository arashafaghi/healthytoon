"""``healthytoon version`` — print the tool and interpreter versions."""

from __future__ import annotations

import sys

import typer

from healthytoon import __version__
from healthytoon.errors import EXIT_OK

__all__ = ["run"]


def run() -> int:
    """Print the Healthytoon version and the running Python version."""

    python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    typer.echo(f"healthytoon {__version__}")
    typer.echo(f"Python {python_version}")
    return EXIT_OK
