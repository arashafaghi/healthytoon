"""``healthytoon list`` — enumerate the registered checks."""

from __future__ import annotations

import argparse

from healthytoon.core.registry import all_checks
from healthytoon.errors import EXIT_OK

__all__ = ["run"]

_NO_CHECKS = "No checks are registered yet."


def run(args: argparse.Namespace) -> int:
    """Print one line per registered check, optionally filtered by category."""

    checks = all_checks()
    if not checks:
        print(_NO_CHECKS)
        return EXIT_OK

    for check_id in sorted(checks):
        check = checks[check_id]
        if args.category is not None and check.category != args.category:
            continue
        print(f"{check_id}  {check.category}  {check.default_severity.name.lower()}")
    return EXIT_OK
