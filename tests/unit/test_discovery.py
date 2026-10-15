"""Unit tests for :mod:`healthytoon.core.discovery`."""

from __future__ import annotations

from pathlib import Path

import pytest

from healthytoon.core.discovery import discover_files, iter_python_files
from healthytoon.errors import TargetError


def _write(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _relative(root: Path, files: tuple[Path, ...]) -> list[str]:
    return sorted(path.relative_to(root.resolve()).as_posix() for path in files)


def test_include_directory_name_matches_subtree(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "pkg" / "a.py")
    _write(tmp_path / "src" / "b.txt")
    _write(tmp_path / "tests" / "c.py")

    result = discover_files(tmp_path, include=("src",))

    assert _relative(tmp_path, result) == ["src/b.txt", "src/pkg/a.py"]


def test_include_glob_matches_by_basename(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "a.py")
    _write(tmp_path / "tests" / "b.py")
    _write(tmp_path / "README.md")

    result = discover_files(tmp_path, include=("*.py",))

    assert _relative(tmp_path, result) == ["src/a.py", "tests/b.py"]


def test_include_anchored_glob(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "a.py")
    _write(tmp_path / "tests" / "b.py")

    result = discover_files(tmp_path, include=("src/**",))

    assert _relative(tmp_path, result) == ["src/a.py"]


def test_exclude_directory_name_prunes_subtree(tmp_path: Path) -> None:
    _write(tmp_path / "keep.py")
    _write(tmp_path / ".venv" / "lib" / "site.py")
    _write(tmp_path / "build" / "artifact.py")

    result = discover_files(tmp_path, exclude=(".venv", "build"))

    assert _relative(tmp_path, result) == ["keep.py"]


def test_exclude_egg_info_glob_prunes(tmp_path: Path) -> None:
    _write(tmp_path / "keep.py")
    _write(tmp_path / "pkg.egg-info" / "PKG-INFO", "metadata")

    result = discover_files(tmp_path, exclude=("*.egg-info",))

    assert _relative(tmp_path, result) == ["keep.py"]


def test_exclude_migrations_double_star(tmp_path: Path) -> None:
    _write(tmp_path / "app" / "models.py")
    _write(tmp_path / "app" / "migrations" / "0001_init.py")

    result = discover_files(tmp_path, exclude=("**/migrations/**",))

    assert _relative(tmp_path, result) == ["app/models.py"]


def test_gitignore_ignores_file_directory_and_negation(tmp_path: Path) -> None:
    _write(
        tmp_path / ".gitignore",
        "ignored.txt\nignored_dir/\n*.log\n!important.log\n",
    )
    _write(tmp_path / "ignored.txt")
    _write(tmp_path / "ignored_dir" / "nested.py")
    _write(tmp_path / "debug.log")
    _write(tmp_path / "important.log")
    _write(tmp_path / "keep.py")

    result = discover_files(tmp_path)

    assert _relative(tmp_path, result) == [".gitignore", "important.log", "keep.py"]


def test_gitignore_can_be_disabled(tmp_path: Path) -> None:
    _write(tmp_path / ".gitignore", "ignored.txt\n")
    _write(tmp_path / "ignored.txt")
    _write(tmp_path / "keep.py")

    result = discover_files(tmp_path, respect_gitignore=False)

    assert _relative(tmp_path, result) == [".gitignore", "ignored.txt", "keep.py"]


def test_gitignore_leading_slash_anchors(tmp_path: Path) -> None:
    _write(tmp_path / ".gitignore", "/root_only.txt\n")
    _write(tmp_path / "root_only.txt")
    _write(tmp_path / "nested" / "root_only.txt")

    result = discover_files(tmp_path)

    assert _relative(tmp_path, result) == [".gitignore", "nested/root_only.txt"]


def test_results_are_deterministically_ordered(tmp_path: Path) -> None:
    _write(tmp_path / "z.py")
    _write(tmp_path / "a.py")
    _write(tmp_path / "m" / "b.py")
    _write(tmp_path / "m" / "a.py")

    result = discover_files(tmp_path)

    assert _relative(tmp_path, result) == ["a.py", "m/a.py", "m/b.py", "z.py"]
    assert result == discover_files(tmp_path)


def test_missing_root_raises_target_error(tmp_path: Path) -> None:
    with pytest.raises(TargetError):
        discover_files(tmp_path / "does-not-exist")


def test_file_root_raises_target_error(tmp_path: Path) -> None:
    target = _write(tmp_path / "not_a_dir")
    with pytest.raises(TargetError):
        discover_files(target)


def test_directory_symlink_is_not_followed(tmp_path: Path) -> None:
    _write(tmp_path / "real" / "inside.py")
    link = tmp_path / "link"
    try:
        link.symlink_to(tmp_path / "real", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not supported on this platform")

    result = discover_files(tmp_path)

    assert _relative(tmp_path, result) == ["real/inside.py"]


def test_iter_python_files_filters_suffix(tmp_path: Path) -> None:
    py = _write(tmp_path / "a.py")
    txt = _write(tmp_path / "b.txt")

    assert iter_python_files((py, txt)) == (py,)
