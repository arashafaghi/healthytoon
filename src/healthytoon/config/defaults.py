"""Built-in default configuration values and the default :class:`Config`."""

from __future__ import annotations

from healthytoon.config.models import DEFAULT_EXCLUDES, Config

__all__ = ["DEFAULT_EXCLUDES", "DEFAULT_INCLUDE", "default_config"]

#: Path globs scanned when no configuration overrides them. ``**`` means the
#: whole repository; default exclusions prune generated/vendored directories.
DEFAULT_INCLUDE: tuple[str, ...] = ("**",)


def default_config() -> Config:
    """Return the built-in default :class:`Config` (no source file)."""
    return Config(include=DEFAULT_INCLUDE, exclude=DEFAULT_EXCLUDES)
