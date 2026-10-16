"""Unit tests for :mod:`healthytoon.cicd.loader`."""

from __future__ import annotations

from pathlib import Path

from healthytoon.cicd.github import parse_github_workflow
from healthytoon.cicd.loader import RawTag, load_yaml


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_on_key_is_a_string_not_a_boolean(tmp_path: Path) -> None:
    path = _write(
        tmp_path / ".github" / "workflows" / "ci.yml",
        "on: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n",
    )

    doc = load_yaml(path)

    assert doc.error is None
    assert isinstance(doc.data, dict)
    assert "on" in doc.data
    assert doc.data["on"] == "push"
    assert True not in doc.data
    # The GitHub adapter therefore sees the trigger by its textual name.
    pipeline = parse_github_workflow(doc, tmp_path)
    assert pipeline.triggers == ("push",)


def test_duplicate_keys_return_error_without_raising(tmp_path: Path) -> None:
    path = _write(tmp_path / "dup.yml", "a: 1\na: 2\n")

    doc = load_yaml(path)

    assert doc.data is None
    assert doc.error is not None
    assert "duplicate" in doc.error.lower()
    assert "line" in doc.error.lower()


def test_malformed_yaml_returns_error(tmp_path: Path) -> None:
    path = _write(tmp_path / "bad.yml", "a: [1, 2\nb: 3\n")

    doc = load_yaml(path)

    assert doc.data is None
    assert doc.error is not None


def test_oversized_file_returns_error(tmp_path: Path) -> None:
    path = _write(tmp_path / "big.yml", "a: " + "x" * 100 + "\n")

    doc = load_yaml(path, max_bytes=10)

    assert doc.data is None
    assert doc.error is not None
    assert "too large" in doc.error


def test_unknown_tag_becomes_rawtag(tmp_path: Path) -> None:
    path = _write(tmp_path / "gl.yml", "ref: !reference [.foo, bar]\n")

    doc = load_yaml(path)

    assert doc.error is None
    assert isinstance(doc.data, dict)
    value = doc.data["ref"]
    assert isinstance(value, RawTag)
    assert value.tag == "!reference"
    assert value.value == [".foo", "bar"]


def test_missing_file_returns_error(tmp_path: Path) -> None:
    doc = load_yaml(tmp_path / "nope.yml")

    assert doc.data is None
    assert doc.error is not None
    assert "cannot read" in doc.error


def test_invalid_utf8_is_replaced_not_raised(tmp_path: Path) -> None:
    path = tmp_path / "latin.yml"
    path.write_bytes(b"key: \xff\xfe\n")

    doc = load_yaml(path)

    assert doc.error is None
    assert isinstance(doc.data, dict)
    assert doc.data["key"] == "\ufffd\ufffd"
