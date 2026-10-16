"""Unit tests for the optional Vulture-backed dead-code check."""

from __future__ import annotations

import dataclasses
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from healthytoon.checks import _optional
from healthytoon.checks.deadcode import DeadCodeCheck
from healthytoon.config.defaults import default_config
from healthytoon.config.models import Config, RuleConfig
from healthytoon.core.context import CheckContext
from healthytoon.core.types import Category, FileKind, Severity, Status

_RULE = "maintainability.dead_code"

_SAMPLE_OUTPUT = (
    "sample.py:1: unused function 'unused' (60% confidence)\n"
    "sample.py:5: unused import 'os' (90% confidence)\n"
    "sample.py:9: unreachable code after 'return' (100% confidence)\n"
    "this line is not a vulture report\n"
    "\n"
)


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _context(tmp_path: Path, rules: Mapping[str, RuleConfig] | None = None) -> CheckContext:
    config: Config = default_config()
    if rules is not None:
        config = dataclasses.replace(config, rules=dict(rules))
    return CheckContext.from_config(tmp_path, config)


def _patch_vulture(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stdout: str,
    returncode: int = 3,
) -> dict[str, Any]:
    """Force a fake ``vulture`` executable and capture the subprocess arguments."""

    captured: dict[str, Any] = {}
    monkeypatch.setattr(_optional, "vulture_executable", lambda: "/usr/bin/vulture")

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            args=args, returncode=returncode, stdout=stdout, stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    return captured


def test_dead_code_declares_cross_file_python_metadata() -> None:
    check = DeadCodeCheck()

    assert check.id == _RULE
    assert check.rule_id == "HT-MAINT-007"
    assert check.category is Category.MAINTAINABILITY
    assert check.default_severity is Severity.LOW
    assert check.cross_file is True
    assert check.file_kinds == frozenset({FileKind.PYTHON})


def test_dead_code_parses_vulture_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, "sample.py", "value = 1\n")
    captured = _patch_vulture(monkeypatch, stdout=_SAMPLE_OUTPUT)

    result = DeadCodeCheck().run(_context(tmp_path))

    assert result.status is Status.WARN
    assert [finding.symbol for finding in result.findings] == ["unused", "os", None]
    first, second, third = result.findings
    assert first.location is not None
    assert first.location.file == "sample.py"
    assert first.location.line == 1
    assert first.properties["confidence"] == 60
    assert first.message == "unused function 'unused'"
    assert second.properties["confidence"] == 90
    assert third.properties["confidence"] == 100
    assert third.message == "unreachable code after 'return'"
    # The confidence threshold is passed through to the real tool.
    assert captured["args"][:3] == ["/usr/bin/vulture", "--min-confidence", "60"]


def test_dead_code_suppresses_framework_and_user_whitelists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(
        tmp_path,
        "sample.py",
        "import pytest\n\n"
        '__all__ = ["public_api"]\n\n\n'
        "def public_api():\n    return 1\n\n\n"
        "@pytest.fixture\n"
        "def my_fixture():\n    return 1\n\n\n"
        "def genuinely_dead():\n    return 2\n",
    )
    stdout = (
        "sample.py:1: unused function 'test_helper' (60% confidence)\n"
        "sample.py:2: unused function 'visit_Name' (60% confidence)\n"
        "sample.py:3: unused function 'public_api' (60% confidence)\n"
        "sample.py:4: unused function 'my_fixture' (60% confidence)\n"
        "sample.py:5: unused function 'keep_me' (60% confidence)\n"
        "sample.py:6: unused function 'genuinely_dead' (60% confidence)\n"
    )
    _patch_vulture(monkeypatch, stdout=stdout)
    rules = {_RULE: RuleConfig(options={"whitelist": ["keep_me"]})}

    result = DeadCodeCheck().run(_context(tmp_path, rules))

    assert [finding.symbol for finding in result.findings] == ["genuinely_dead"]
    assert result.findings[0].location is not None
    assert result.findings[0].location.file == "sample.py"


def test_dead_code_skips_when_vulture_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path, "sample.py", "value = 1\n")
    monkeypatch.setattr(_optional, "vulture_executable", lambda: None)

    result = DeadCodeCheck().run(_context(tmp_path))

    assert result.status is Status.SKIP
    assert result.findings == ()
    assert result.notes == "vulture is not installed; dead-code analysis skipped"


def test_dead_code_skips_when_vulture_cannot_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path, "sample.py", "value = 1\n")
    monkeypatch.setattr(_optional, "vulture_executable", lambda: "/usr/bin/vulture")

    def boom(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise OSError("exec format error")

    monkeypatch.setattr(subprocess, "run", boom)

    result = DeadCodeCheck().run(_context(tmp_path))

    assert result.status is Status.SKIP
    assert result.findings == ()
    assert result.notes == "vulture could not be executed; dead-code analysis skipped"


def test_dead_code_skips_when_vulture_fails_without_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path, "sample.py", "value = 1\n")
    _patch_vulture(monkeypatch, stdout="", returncode=1)

    result = DeadCodeCheck().run(_context(tmp_path))

    assert result.status is Status.SKIP
    assert result.findings == ()
    assert result.notes is not None
    assert "produced no output" in result.notes


def test_dead_code_caps_findings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, "sample.py", "value = 1\n")
    stdout = "".join(
        f"sample.py:{index}: unused variable 'v{index}' (60% confidence)\n"
        for index in range(1, 251)
    )
    _patch_vulture(monkeypatch, stdout=stdout)

    result = DeadCodeCheck().run(_context(tmp_path))

    assert len(result.findings) == 200
    assert result.findings[0].location is not None
    assert result.findings[0].location.line == 1
    assert result.findings[-1].location is not None
    assert result.findings[-1].location.line == 200
