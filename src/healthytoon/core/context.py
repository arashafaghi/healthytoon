"""Shared per-run context handed to every check.

A :class:`CheckContext` bundles the resolved target root, the discovered file
set, the effective configuration, and arbitrary per-check options. Parsed ASTs
are memoized so multiple checks can reuse a single parse of a file.
"""

from __future__ import annotations

import ast
import io
import tokenize
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from healthytoon.config.models import Config
from healthytoon.core import discovery
from healthytoon.core.types import FileKind

__all__ = ["CheckContext"]


@dataclass(frozen=True, slots=True)
class CheckContext:
    """Immutable inputs plus a mutable, equality-excluded AST cache."""

    root: Path
    files: tuple[Path, ...]
    config: Config
    options: Mapping[str, object] = field(default_factory=dict)
    _ast_cache: dict[Path, ast.Module | None] = field(
        init=False, default_factory=dict, compare=False, repr=False
    )

    def read_text(self, path: Path) -> str:
        """Decode ``path`` using its declared encoding with a safe fallback."""

        data = Path(path).read_bytes()
        try:
            encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
        except (SyntaxError, ValueError):
            return data.decode("utf-8", errors="replace")
        try:
            return data.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            return data.decode("utf-8", errors="replace")

    def parse_ast(self, path: Path) -> ast.Module | None:
        """Parse ``path`` once; return ``None`` when it cannot be parsed."""

        if path in self._ast_cache:
            return self._ast_cache[path]
        try:
            module: ast.Module | None = ast.parse(self.read_text(path), filename=str(path))
        except (SyntaxError, ValueError):
            module = None
        self._ast_cache[path] = module
        return module

    def iter_python_files(self) -> tuple[Path, ...]:
        """Return the context's files that end in ``.py``."""

        return discovery.iter_python_files(self.files)

    def files_for(self, kinds: frozenset[FileKind]) -> tuple[Path, ...]:
        """Return files matching ``kinds``; an empty set means every file."""

        if not kinds:
            return self.files
        return tuple(path for path in self.files if discovery.classify_file(path) in kinds)

    def rule_options(self, check_id: str, defaults: Mapping[str, object]) -> Mapping[str, object]:
        """Return ``defaults`` overlaid with any ``[rules."<check_id>"].options``.

        This is the single channel by which checks read their configurable
        thresholds, so no check needs to know how configuration is layered.
        """

        merged: dict[str, object] = dict(defaults)
        rule = self.config.rules.get(check_id)
        if rule is not None:
            merged.update(rule.options)
        return merged

    @classmethod
    def from_config(
        cls,
        root: Path,
        config: Config,
        *,
        options: Mapping[str, object] | None = None,
    ) -> CheckContext:
        """Discover files under ``root`` using ``config``'s discovery settings."""

        files = discovery.discover_files(
            root,
            include=config.include,
            exclude=config.exclude,
            respect_gitignore=config.respect_gitignore,
        )
        return cls(root=root, files=files, config=config, options=options or {})
