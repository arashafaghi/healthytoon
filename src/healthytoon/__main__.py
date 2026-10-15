"""Allow ``python -m healthytoon`` to run the CLI."""

from __future__ import annotations

from healthytoon.cli.app import main

if __name__ == "__main__":
    raise SystemExit(main())
