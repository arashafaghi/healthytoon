"""``healthytoon explain`` — describe a single registered check."""

from __future__ import annotations

from typing import Annotated

import typer

from healthytoon.core.registry import get_check
from healthytoon.errors import EXIT_OK, EXIT_USAGE

__all__ = ["run"]


def run(
    check_id: Annotated[str, typer.Argument(help="check id (see `healthytoon list`)")],
) -> int:
    """Print details for the check named ``CHECK_ID``."""

    check = get_check(check_id)
    if check is None:
        typer.echo(f"healthytoon: error: unknown check id: {check_id}", err=True)
        return EXIT_USAGE

    typer.echo(check.id)
    typer.echo(f"  category: {check.category.value}")
    typer.echo(f"  default severity: {check.default_severity.name.lower()}")
    summary = _first_line(type(check).__doc__)
    if summary:
        typer.echo(f"  {summary}")
    return EXIT_OK


def _first_line(text: str | None) -> str:
    """Return the first non-empty, stripped line of ``text`` (or ``""``)."""

    if text:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped
    return ""
