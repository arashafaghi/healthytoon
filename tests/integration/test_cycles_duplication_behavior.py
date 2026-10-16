"""Integration tests: import-cycle and duplication checks through the CLI (T2a.1b).

Each scenario builds a fixture repository under ``tmp_path`` and drives the real
command handler (``healthytoon.cli.app.main``) with ``--format json``, then
asserts on the parsed stdout document. Coverage focuses on observable outcomes:
which rule fired, at which severity and location, which files are implicated,
run determinism, and that ``--only`` selection and a
``[tool.healthytoon.rules]`` option override change the result.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pytest import CaptureFixture

from healthytoon.cli.app import main

CYCLE_RULE = "HT-MAINT-004"
DUPLICATION_RULE = "HT-MAINT-005"
CYCLE_CHECK = "maintainability.import_cycles"
DUPLICATION_CHECK = "maintainability.duplication"


def _write(repo: Path, name: str, source: str) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _run_check_json(
    repo: Path, capsys: CaptureFixture[str], *extra: str
) -> tuple[int, dict[str, Any]]:
    """Run ``healthytoon check`` in-process and return (exit code, JSON payload).

    These fixtures carry no CI configuration, so the CI/CD checks are skipped:
    the scenarios below isolate the maintainability-cycle and duplication rules.
    """

    code = main(["check", str(repo), "--format", "json", "--skip", "ci.*", *extra])
    payload = json.loads(capsys.readouterr().out)
    return code, payload


def _rule_findings(payload: dict[str, Any], rule_id: str) -> list[dict[str, Any]]:
    return [finding for finding in payload["findings"] if finding["rule_id"] == rule_id]


def _checks_run(payload: dict[str, Any]) -> list[str]:
    return payload["target"]["checks_run"]


def _write_cyclic_package(repo: Path) -> None:
    _write(repo, "pkg/__init__.py", "")
    _write(
        repo,
        "pkg/a.py",
        "from pkg.b import helper\n\n\ndef run() -> int:\n    return helper()\n",
    )
    _write(
        repo,
        "pkg/b.py",
        "from pkg.a import run\n\n\ndef helper() -> int:\n    return run()\n",
    )


def _write_acyclic_package(repo: Path) -> None:
    _write(repo, "pkg/__init__.py", "")
    _write(
        repo,
        "pkg/a.py",
        "from pkg.b import helper\n\n\ndef run() -> int:\n    return helper()\n",
    )
    _write(repo, "pkg/b.py", "def helper() -> int:\n    return 1\n")


def _duplicated_block(statements: int = 12) -> str:
    """Return ``statements`` five-token assignments (60 tokens at the default 12)."""

    return "".join(f"value_{index} = a + b\n" for index in range(statements))


def _distinct_source(prefix: str, statements: int = 14) -> str:
    """Return a module whose every name is prefixed (so no window can match another)."""

    lines = [f"def {prefix}_handler():"]
    lines.extend(f"    {prefix}_{index} = compute_{prefix}({index})" for index in range(statements))
    lines.append("    return None")
    return "\n".join(lines) + "\n"


def test_import_cycle_reports_ht_maint_004(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    _write_cyclic_package(tmp_path)

    code, payload = _run_check_json(tmp_path, capsys)

    assert code == 0  # MEDIUM does not trip the default HIGH gate
    findings = _rule_findings(payload, CYCLE_RULE)
    assert len(findings) == 1
    finding = findings[0]
    assert finding["check"] == CYCLE_CHECK
    assert finding["category"] == "maintainability"
    assert finding["severity"] == "medium"
    assert finding["symbol"] == "pkg/a.py -> pkg/b.py"
    assert finding["properties"]["modules"] == ["pkg/a.py", "pkg/b.py"]


def test_acyclic_package_reports_no_import_cycle(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    _write_acyclic_package(tmp_path)

    code, payload = _run_check_json(tmp_path, capsys)

    assert code == 0
    assert _rule_findings(payload, CYCLE_RULE) == []


def test_duplicated_block_reports_ht_maint_005(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    _write(tmp_path, "one.py", _duplicated_block())
    _write(tmp_path, "two.py", _duplicated_block())

    code, payload = _run_check_json(tmp_path, capsys)

    assert code == 0  # LOW never trips the default HIGH gate
    findings = _rule_findings(payload, DUPLICATION_RULE)
    assert len(findings) == 1
    finding = findings[0]
    assert finding["check"] == DUPLICATION_CHECK
    assert finding["category"] == "maintainability"
    assert finding["severity"] == "low"
    assert finding["properties"]["tokens"] >= 60
    assert finding["properties"]["files"] == ["one.py", "two.py"]


def test_distinct_files_report_no_duplication(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    # Each file is tokenized (well over the 50-token floor) but shares no window.
    _write(tmp_path, "one.py", _distinct_source("alpha"))
    _write(tmp_path, "two.py", _distinct_source("beta"))

    code, payload = _run_check_json(tmp_path, capsys)

    assert code == 0
    assert _rule_findings(payload, DUPLICATION_RULE) == []


def test_cycle_scan_is_byte_identical_across_runs(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    _write_cyclic_package(tmp_path)

    assert main(["check", str(tmp_path), "--format", "json", "--skip", "ci.*"]) == 0
    first = capsys.readouterr().out
    assert main(["check", str(tmp_path), "--format", "json", "--skip", "ci.*"]) == 0
    second = capsys.readouterr().out

    assert first == second


def test_only_runs_the_cycle_check(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    _write_cyclic_package(tmp_path)
    _write(tmp_path, "one.py", _duplicated_block())
    _write(tmp_path, "two.py", _duplicated_block())

    code, payload = _run_check_json(tmp_path, capsys, "--only", CYCLE_CHECK)

    assert code == 0
    assert _checks_run(payload) == [CYCLE_CHECK]
    assert {finding["check"] for finding in payload["findings"]} == {CYCLE_CHECK}


def test_min_tokens_override_suppresses_small_duplication(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    _write(tmp_path, "one.py", _duplicated_block())
    _write(tmp_path, "two.py", _duplicated_block())
    (tmp_path / "pyproject.toml").write_text(
        f'[tool.healthytoon.rules."{DUPLICATION_CHECK}"]\noptions = {{ min_tokens = 200 }}\n',
        encoding="utf-8",
    )

    code, payload = _run_check_json(tmp_path, capsys)

    assert code == 0
    assert DUPLICATION_CHECK in _checks_run(payload)
    assert _rule_findings(payload, DUPLICATION_RULE) == []
