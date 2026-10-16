"""Deterministic file discovery with include/exclude and ``.gitignore`` support.

The walker is intentionally standard-library only: it never shells out to git
and never follows symlinks (so cyclic trees cannot hang an audit). Matching is
split between two similar-but-distinct notions:

* ``include``/``exclude`` use root-relative globs with directory semantics:
  a pattern without a slash (such as ``"src"`` or ``"*.egg-info"``) matches any
  path component, while a pattern containing a slash is anchored to the root.
* ``.gitignore`` follows the common git rules: comments, negation, leading-slash
  anchoring, trailing-slash directory patterns, and ``*``/``**`` globs.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from healthytoon.core.types import FileKind
from healthytoon.errors import TargetError

__all__ = ["discover_files", "iter_python_files", "classify_file"]


@dataclass(frozen=True, slots=True)
class _Glob:
    """A single glob pattern compiled once for repeated matching."""

    anchored: bool
    dir_only: bool
    regex: re.Pattern[str]

    def matches(self, rel: str, *, is_dir: bool) -> bool:
        if self.dir_only and not is_dir:
            return False
        if self.anchored:
            return self.regex.fullmatch(rel) is not None
        return any(self.regex.fullmatch(part) is not None for part in rel.split("/"))


@dataclass(frozen=True, slots=True)
class _IgnoreRule:
    """One parsed ``.gitignore`` line, scoped to the file's directory."""

    glob: _Glob
    negation: bool
    base: str

    def applies(self, rel: str) -> bool:
        if not self.base:
            return True
        return rel == self.base or rel.startswith(f"{self.base}/")

    def matches(self, rel: str, *, is_dir: bool) -> bool:
        sub = rel if not self.base else rel[len(self.base) + 1 :]
        return self.glob.matches(sub, is_dir=is_dir)


def _translate(pattern: str) -> re.Pattern[str]:
    """Translate a glob (``*``, ``**``, ``?``, ``[...]``) into a regex."""

    parts: list[str] = ["^"]
    index = 0
    length = len(pattern)
    while index < length:
        char = pattern[index]
        if char == "*":
            if index + 1 < length and pattern[index + 1] == "*":
                index += 2
                if index < length and pattern[index] == "/":
                    parts.append("(?:.*/)?")
                    index += 1
                else:
                    parts.append(".*")
            else:
                parts.append("[^/]*")
                index += 1
        elif char == "?":
            parts.append("[^/]")
            index += 1
        elif char == "[":
            index = _append_char_class(parts, pattern, index)
        else:
            parts.append(re.escape(char))
            index += 1
    parts.append("$")
    return re.compile("".join(parts))


def _append_char_class(parts: list[str], pattern: str, start: int) -> int:
    """Append a ``[...]`` class to ``parts``; return the next index to read."""

    length = len(pattern)
    index = start + 1
    negate = False
    if index < length and pattern[index] in "!^":
        negate = True
        index += 1
    if index < length and pattern[index] == "]":
        index += 1
    begin = index
    while index < length and pattern[index] != "]":
        index += 1
    if index >= length:
        parts.append(r"\[")
        return start + 1
    body = pattern[begin:index]
    parts.append("[" + ("^" if negate else "") + body + "]")
    return index + 1


def _compile_glob(raw: str) -> _Glob:
    normalized = raw.strip()
    if normalized.startswith("./"):
        normalized = normalized[2:]
    dir_only = normalized.endswith("/")
    core = normalized.rstrip("/")
    if core.startswith("/"):
        anchored = True
        core = core.lstrip("/")
    else:
        anchored = "/" in core
    return _Glob(anchored=anchored, dir_only=dir_only, regex=_translate(core))


def _load_ignore(gitignore: Path, base: str) -> tuple[_IgnoreRule, ...]:
    try:
        text = gitignore.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ()
    rules: list[_IgnoreRule] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line or line.startswith("#"):
            continue
        negation = line.startswith("!")
        pattern = line[1:] if negation else line
        if pattern.startswith(("\\#", "\\!")):
            pattern = pattern[1:]
        if not pattern.strip() or pattern.strip() == "/":
            continue
        rules.append(_IgnoreRule(glob=_compile_glob(pattern), negation=negation, base=base))
    return tuple(rules)


def _extend_ignore_rules(
    directory: Path, rel_dir: str, inherited: tuple[_IgnoreRule, ...]
) -> tuple[_IgnoreRule, ...]:
    gitignore = directory / ".gitignore"
    if not gitignore.is_file():
        return inherited
    return inherited + _load_ignore(gitignore, rel_dir)


def _is_ignored(rules: tuple[_IgnoreRule, ...], rel: str, *, is_dir: bool) -> bool:
    ignored = False
    for rule in rules:
        if rule.applies(rel) and rule.matches(rel, is_dir=is_dir):
            ignored = not rule.negation
    return ignored


def _match_any(globs: tuple[_Glob, ...], rel: str, *, is_dir: bool) -> bool:
    return any(glob.matches(rel, is_dir=is_dir) for glob in globs)


def _scan_directory(directory: Path) -> list[os.DirEntry[str]]:
    """Return the sorted entries of ``directory``, or ``[]`` when unreadable."""

    try:
        with os.scandir(directory) as iterator:
            return sorted(iterator, key=lambda entry: entry.name)
    except OSError:
        return []


def _entry_is_dir(entry: os.DirEntry[str]) -> bool | None:
    """Return whether ``entry`` is a directory, or ``None`` when unknown."""

    try:
        return entry.is_dir(follow_symlinks=False)
    except OSError:
        return None


class _Walker:
    """Stateful walker that accumulates the regular files under one root."""

    def __init__(
        self,
        *,
        include_globs: tuple[_Glob, ...],
        exclude_globs: tuple[_Glob, ...],
        respect_gitignore: bool,
    ) -> None:
        self._include_globs = include_globs
        self._exclude_globs = exclude_globs
        self._respect_gitignore = respect_gitignore
        self.found: list[Path] = []
        self.seen: set[Path] = set()

    def walk(self, directory: Path, rel_dir: str, rules: tuple[_IgnoreRule, ...]) -> None:
        if self._respect_gitignore:
            rules = _extend_ignore_rules(directory, rel_dir, rules)
        for entry in _scan_directory(directory):
            self._visit(entry, rel_dir, rules)

    def _visit(self, entry: os.DirEntry[str], rel_dir: str, rules: tuple[_IgnoreRule, ...]) -> None:
        if entry.is_symlink():
            return
        rel = entry.name if not rel_dir else f"{rel_dir}/{entry.name}"
        is_dir = _entry_is_dir(entry)
        if is_dir is None:
            return
        if _match_any(self._exclude_globs, rel, is_dir=is_dir):
            return
        if rules and _is_ignored(rules, rel, is_dir=is_dir):
            return
        if is_dir:
            self.walk(Path(entry.path), rel, rules)
            return
        self._add_file(entry, rel)

    def _add_file(self, entry: os.DirEntry[str], rel: str) -> None:
        if not entry.is_file(follow_symlinks=False):
            return
        if self._include_globs and not _match_any(self._include_globs, rel, is_dir=False):
            return
        resolved = Path(entry.path).resolve()
        if resolved in self.seen:
            return
        self.seen.add(resolved)
        self.found.append(resolved)


def discover_files(
    root: Path,
    *,
    include: Sequence[str] = (),
    exclude: Sequence[str] = (),
    respect_gitignore: bool = True,
) -> tuple[Path, ...]:
    """Return the regular files under ``root`` in deterministic order.

    Paths are absolute and resolved, sorted by their root-relative POSIX path.
    ``include`` (when non-empty) restricts results; ``exclude`` prunes matched
    files and whole directory subtrees. Missing or non-directory roots raise
    :class:`TargetError`.
    """

    root_path = Path(root)
    if not root_path.is_dir():
        raise TargetError(f"target is not a directory: {root_path}")
    resolved_root = root_path.resolve()

    walker = _Walker(
        include_globs=tuple(_compile_glob(p.rstrip("/")) for p in include if p.strip()),
        exclude_globs=tuple(_compile_glob(p) for p in exclude if p.strip()),
        respect_gitignore=respect_gitignore,
    )
    walker.walk(resolved_root, "", ())
    walker.found.sort(key=lambda path: path.relative_to(resolved_root).as_posix())
    return tuple(walker.found)


def iter_python_files(files: Sequence[Path]) -> tuple[Path, ...]:
    """Filter ``files`` down to those ending in ``.py``."""

    return tuple(path for path in files if path.suffix == ".py")


_WORKFLOW_NAMES = frozenset({".gitlab-ci.yml", ".gitlab-ci.yaml"})
_LOCKFILE_NAMES = frozenset({"uv.lock", "poetry.lock", "Pipfile.lock", "pdm.lock"})
_CONFIG_SUFFIXES = frozenset({".toml", ".cfg", ".ini", ".json", ".yml", ".yaml", ".properties"})
_YAML_SUFFIXES = frozenset({".yml", ".yaml"})


def _is_workflow(candidate: Path, name: str, suffix: str) -> bool:
    return name in _WORKFLOW_NAMES or (".github" in candidate.parts and suffix in _YAML_SUFFIXES)


def _is_requirements(name: str, suffix: str) -> bool:
    return name.startswith("requirements") and suffix == ".txt"


def _is_config(name: str, suffix: str) -> bool:
    return suffix in _CONFIG_SUFFIXES or name.startswith(".")


def classify_file(path: Path | str) -> FileKind:
    """Classify ``path`` into a coarse :class:`~healthytoon.core.types.FileKind`.

    Classification is name/suffix based and intentionally cheap; it lets a
    check declare which file kinds it consumes and lets the runner skip checks
    whose inputs are absent.
    """

    candidate = Path(path)
    name = candidate.name
    suffix = candidate.suffix.lower()

    if suffix == ".pyi":
        return FileKind.STUB
    if suffix == ".py":
        return FileKind.PYTHON
    if _is_workflow(candidate, name, suffix):
        return FileKind.WORKFLOW
    if name == "pyproject.toml":
        return FileKind.PYPROJECT
    if name in _LOCKFILE_NAMES:
        return FileKind.LOCKFILE
    if _is_requirements(name, suffix):
        return FileKind.REQUIREMENTS
    if suffix == ".md":
        return FileKind.MARKDOWN
    if _is_config(name, suffix):
        return FileKind.CONFIG
    return FileKind.OTHER
