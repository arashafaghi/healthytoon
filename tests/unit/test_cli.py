"""Unit tests for the command-line interface."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pytest import CaptureFixture, MonkeyPatch

from healthytoon import __version__
from healthytoon.cli.app import main
from healthytoon.cli.commands import list as list_command
from healthytoon.core import registry
from healthytoon.core.context import CheckContext
from healthytoon.core.protocols import CHECK_API_VERSION, CheckResult
from healthytoon.core.types import Category, FileKind, Finding, Severity
from healthytoon.errors import EXIT_USAGE


def _make_target(root: Path) -> Path:
    package = root / "src"
    package.mkdir(parents=True, exist_ok=True)
    (package / "a.py").write_text("x = 1\n", encoding="utf-8")
    return root


def _make_clean_target(root: Path) -> Path:
    """Build a minimal fixture that satisfies every built-in check."""

    package = root / "src"
    package.mkdir(parents=True, exist_ok=True)
    (package / "a.py").write_text(
        '"""Clean module."""\n\n\ndef increment(value: int) -> int:\n'
        '    """Return ``value`` plus one."""\n    return value + 1\n',
        encoding="utf-8",
    )
    return root


#: Stable registration order of the checks bundled with Healthytoon (T2a.1a).
_BUILTIN_CHECK_IDS: tuple[str, ...] = (
    "maintainability.function_length",
    "maintainability.nesting_depth",
    "maintainability.module_length",
    "typing.annotation_coverage",
    "documentation.docstring_coverage",
)


class _HighFindingCheck:
    """Concrete check emitting a single HIGH finding, for gate tests."""

    id = "fake.high"
    category = Category.MAINTAINABILITY
    default_severity = Severity.HIGH
    api_version = CHECK_API_VERSION
    cross_file = False

    def supports(self, ctx: CheckContext) -> bool:
        return True

    def run(self, ctx: CheckContext) -> CheckResult:
        finding = Finding(
            rule_id="fake.high.rule",
            check=self.id,
            category=Category.MAINTAINABILITY,
            severity=Severity.HIGH,
            title="synthetic high finding",
            message="a synthetic high finding for gate testing",
        )
        return CheckResult(check=self.id, findings=(finding,))


class _CrashingCheck:
    """Concrete check whose ``run`` raises, modelling a broken engine."""

    id = "fake.crash"
    category = Category.MAINTAINABILITY
    default_severity = Severity.HIGH
    api_version = CHECK_API_VERSION
    cross_file = False
    file_kinds: frozenset[FileKind] = frozenset()

    def supports(self, ctx: CheckContext) -> bool:
        return True

    def run(self, ctx: CheckContext) -> CheckResult:
        raise RuntimeError("synthetic crash")


def test_version_subcommand(capsys: CaptureFixture[str]) -> None:
    assert main(["version"]) == 0
    assert __version__ in capsys.readouterr().out


def test_global_version_flag() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0


def test_list_returns_zero() -> None:
    assert main(["list"]) == 0


def test_list_empty_registry(monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]) -> None:
    monkeypatch.setattr(list_command, "all_checks", lambda: {})
    assert main(["list"]) == 0
    assert "No checks are registered yet." in capsys.readouterr().out


def test_list_prints_builtin_check_ids(capsys: CaptureFixture[str]) -> None:
    assert main(["list"]) == 0
    output = capsys.readouterr().out
    assert "No checks are registered yet." not in output
    for check_id in _BUILTIN_CHECK_IDS:
        assert check_id in output


def test_check_scans_files(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    target = _make_target(tmp_path)

    assert main(["check", str(target), "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["target"]["files_scanned"] == 1


def test_check_missing_target(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    assert main(["check", str(tmp_path / "nope")]) == 4
    assert "error" in capsys.readouterr().err


def test_check_bad_config(tmp_path: Path) -> None:
    target = _make_target(tmp_path)
    bad = tmp_path / "bad.toml"
    bad.write_text("this is = = not valid toml\n", encoding="utf-8")

    assert main(["check", str(target), "--config", str(bad)]) == 3


def test_check_multiple_targets_exits_usage(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    dir_a = _make_target(tmp_path / "a")
    dir_b = _make_target(tmp_path / "b")

    assert main(["check", str(dir_a), str(dir_b)]) == EXIT_USAGE
    assert "single target directory" in capsys.readouterr().err


def test_unknown_subcommand_exits_two() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["bogus"])
    assert excinfo.value.code == 2


def test_unknown_flag_exits_two() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["check", "--bogus"])
    assert excinfo.value.code == 2


def test_check_json_format(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    target = _make_target(tmp_path)

    assert main(["check", str(target), "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "target" in payload
    assert "summary" in payload


def test_check_sarif_format(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    target = _make_target(tmp_path)

    assert main(["check", str(target), "--format", "sarif"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["version"] == "2.1.0"


def test_check_markdown_format(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    # A clean minimal fixture satisfies every built-in check: zero findings,
    # a perfect score, and no findings table in the rendered report.
    target = _make_clean_target(tmp_path)

    assert main(["check", str(target), "--format", "markdown"]) == 0
    output = capsys.readouterr().out
    assert "Grade A" in output
    assert "100/100" in output
    assert "No findings." in output
    assert "| Severity | Rule |" not in output


def test_check_bogus_format_exits_two(tmp_path: Path) -> None:
    target = _make_target(tmp_path)

    with pytest.raises(SystemExit) as excinfo:
        main(["check", str(target), "--format", "bogus"])
    assert excinfo.value.code == 2


def test_check_output_file(tmp_path: Path) -> None:
    target = _make_target(tmp_path)
    destination = tmp_path / "report.txt"

    assert main(["check", str(target), "-o", str(destination)]) == 0
    assert destination.read_text(encoding="utf-8")


def test_fail_on_bogus_exits_two(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["check", str(tmp_path), "--fail-on", "bogus"])
    assert excinfo.value.code == 2


def test_fail_on_none_returns_ok(tmp_path: Path) -> None:
    target = _make_target(tmp_path)

    assert main(["check", str(target), "--fail-on", "none"]) == 0


def test_fail_on_high_findings_exit_one(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    target = _make_target(tmp_path)
    monkeypatch.setattr(registry, "all_checks", lambda: {"fake.high": _HighFindingCheck()})

    assert main(["check", str(target), "--fail-on", "high", "--quiet"]) == 1


def test_fail_on_critical_findings_exit_zero(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    target = _make_target(tmp_path)
    monkeypatch.setattr(registry, "all_checks", lambda: {"fake.high": _HighFindingCheck()})

    assert main(["check", str(target), "--fail-on", "critical", "--quiet"]) == 0


def test_check_exclude_appends_to_default_excludes(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    target = tmp_path / "repo"
    (target / "src").mkdir(parents=True)
    (target / "src" / "keep.py").write_text("x = 1\n", encoding="utf-8")
    (target / ".venv").mkdir()
    (target / ".venv" / "skip.py").write_text("y = 2\n", encoding="utf-8")
    (target / "extra").mkdir()
    (target / "extra" / "other.py").write_text("z = 3\n", encoding="utf-8")

    # Default excludes prune .venv; src/keep.py and extra/other.py remain.
    assert main(["check", str(target), "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["target"]["files_scanned"] == 2

    # --exclude appends to the defaults: extra/ is pruned, but .venv must stay
    # excluded (so the count drops to 1: only src/keep.py survives).
    assert main(["check", str(target), "--format", "json", "--exclude", "extra"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["target"]["files_scanned"] == 1


def test_crashed_check_exits_internal(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    target = _make_target(tmp_path)
    monkeypatch.setattr(registry, "all_checks", lambda: {"fake.crash": _CrashingCheck()})

    assert main(["check", str(target)]) == 5


def test_fail_on_none_with_high_finding_returns_zero(
    monkeypatch: MonkeyPatch, tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    target = _make_target(tmp_path)
    monkeypatch.setattr(registry, "all_checks", lambda: {"fake.high": _HighFindingCheck()})

    assert main(["check", str(target), "--fail-on", "none", "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["total"] == 1
    assert payload["summary"]["by_severity"] == {"high": 1}
