"""``healthytoon list`` — enumerate the registered checks."""

from __future__ import annotations

from typing import Annotated

import typer

from healthytoon.core.registry import all_checks
from healthytoon.errors import EXIT_OK

__all__ = ["run"]

_NO_CHECKS = "No checks are registered yet."


def run(
    category: Annotated[
        str | None,
        typer.Option("--category", help="restrict to a single category"),
    ] = None,
) -> int:
    """Print one line per registered check, optionally filtered by category."""

    checks = all_checks()
    if not checks:
        typer.echo(_NO_CHECKS)
        return EXIT_OK

    for check_id in sorted(checks):
        check = checks[check_id]
        if category is not None and check.category != category:
            continue
        typer.echo(f"{check_id}  {check.category}  {check.default_severity.name.lower()}")
    return EXIT_OK
