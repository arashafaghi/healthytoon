"""``healthytoon version`` — print the tool and interpreter versions."""

from __future__ import annotations

import argparse
import sys

from healthytoon import __version__
from healthytoon.errors import EXIT_OK

__all__ = ["run"]


def run(args: argparse.Namespace) -> int:
    """Print the Healthytoon version and the running Python version."""

    python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    print(f"healthytoon {__version__}")
    print(f"Python {python_version}")
    return EXIT_OK
