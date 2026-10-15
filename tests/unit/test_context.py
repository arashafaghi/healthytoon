"""Unit tests for :mod:`healthytoon.core.context`."""

from __future__ import annotations

import ast
from pathlib import Path

from healthytoon.config.models import Config
from healthytoon.core.context import CheckContext


def _write(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _context(tmp_path: Path, files: tuple[Path, ...] = ()) -> CheckContext:
    return CheckContext(root=tmp_path, files=files, config=Config())


def test_read_text_normal(tmp_path: Path) -> None:
    target = _write(tmp_path / "a.py", "x = 1\n")

    assert _context(tmp_path).read_text(target) == "x = 1\n"


def test_read_text_honours_declared_encoding(tmp_path: Path) -> None:
    target = tmp_path / "latin.py"
    target.write_bytes(b"# -*- coding: latin-1 -*-\ncaf\xe9\n")

    assert _context(tmp_path).read_text(target) == "# -*- coding: latin-1 -*-\ncaf\u00e9\n"


def test_read_text_non_utf8_falls_back(tmp_path: Path) -> None:
    target = tmp_path / "bad.py"
    target.write_bytes(b"caf\xe9\n")

    result = _context(tmp_path).read_text(target)

    assert isinstance(result, str)
    assert "\ufffd" in result


def test_parse_ast_returns_module_and_caches_identity(tmp_path: Path) -> None:
    target = _write(tmp_path / "a.py", "value = 1\n")
    context = _context(tmp_path)

    first = context.parse_ast(target)
    second = context.parse_ast(target)

    assert isinstance(first, ast.Module)
    assert first is second


def test_parse_ast_returns_none_on_syntax_error(tmp_path: Path) -> None:
    target = _write(tmp_path / "broken.py", "def (:\n")

    assert _context(tmp_path).parse_ast(target) is None


def test_from_config_populates_files(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "pkg" / "a.py")
    _write(tmp_path / "tests" / "b.py")
    config = Config(include=("src",), exclude=(), respect_gitignore=False)

    context = CheckContext.from_config(tmp_path, config)

    assert context.config is config
    assert context.files == ((tmp_path / "src" / "pkg" / "a.py").resolve(),)


def test_iter_python_files_filters(tmp_path: Path) -> None:
    py = _write(tmp_path / "a.py")
    txt = _write(tmp_path / "b.txt")
    context = _context(tmp_path, files=(py, txt))

    assert context.iter_python_files() == (py,)
