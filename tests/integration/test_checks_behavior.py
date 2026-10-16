"""Integration tests: built-in checks observed through the CLI (T2a.1a).

Every scenario builds a small fixture repository under ``tmp_path`` and drives
the real command handler (``healthytoon.cli.app.main``) with ``--format json``,
then asserts on the parsed stdout document: which rule fired, at which
severity, at which location, and with which process exit code. Selection flags
(``--only``/``--skip``/``--fail-on``) and ``[tool.healthytoon.rules]`` config
knobs are exercised the same way, on observable outcomes only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pytest import CaptureFixture

from healthytoon.cli.app import main

#: Stable registration order of the checks bundled with Healthytoon.
BUILTIN_CHECK_IDS: tuple[str, ...] = (
    "maintainability.function_length",
    "maintainability.nesting_depth",
    "maintainability.module_length",
    "typing.annotation_coverage",
    "documentation.docstring_coverage",
    "maintainability.import_cycles",
    "maintainability.duplication",
)


def _write_py(repo: Path, name: str, source: str) -> None:
    (repo / name).write_text(source, encoding="utf-8")


def _long_function_source(statements: int = 55) -> str:
    """Return a function spanning ``statements + 2`` lines (well over 50)."""

    body = "\n".join(f"    value = value + {index}" for index in range(statements))
    return f"def process(value):\n{body}\n    return value\n"


def _deeply_nested_source(levels: int = 7) -> str:
    """Return a function with ``levels`` nested ``if`` statements (max depth is 5)."""

    lines = ["def deep(items):"]
    for level in range(levels):
        lines.append(f"{'    ' * (level + 1)}if items:")
    lines.append(f"{'    ' * (levels + 1)}return True")
    lines.append("    return False")
    return "\n".join(lines) + "\n"


def _large_module_source(statements: int = 1005) -> str:
    """Return a module with ``statements + 1`` source lines (over the 1000 cap)."""

    header = '"""Large module."""\n'
    body = "\n".join(f"CONSTANT_{index} = {index}" for index in range(statements))
    return header + body + "\n"


_UNANNOTATED_MODULE = (
    '"""Annotation gaps."""\n'
    "\n"
    "def alpha(a):\n"
    "    return a\n"
    "\n"
    "def beta(b):\n"
    "    return b\n"
    "\n"
    "def gamma(c):\n"
    "    return c\n"
    "\n"
    "def delta(d: int) -> int:\n"
    "    return d\n"
)

_UNDOCUMENTED_MODULE = (
    '"""Docstring gaps."""\n'
    "\n"
    "def alpha(a):\n"
    "    return a\n"
    "\n"
    "def beta(b):\n"
    "    return b\n"
    "\n"
    "def gamma(c):\n"
    "    return c\n"
)

_CLEAN_MODULE = (
    '"""Clean module."""\n'
    "\n"
    "\n"
    "def increment(value: int) -> int:\n"
    '    """Return ``value`` plus one."""\n'
    "    return value + 1\n"
)


def _run_check_json(
    repo: Path, capsys: CaptureFixture[str], *extra: str
) -> tuple[int, dict[str, Any]]:
    """Run ``healthytoon check`` in-process and return (exit code, JSON payload)."""

    code = main(["check", str(repo), "--format", "json", *extra])
    payload = json.loads(capsys.readouterr().out)
    return code, payload


def _rule_findings(payload: dict[str, Any], rule_id: str) -> list[dict[str, Any]]:
    return [finding for finding in payload["findings"] if finding["rule_id"] == rule_id]


def _checks_run(payload: dict[str, Any]) -> list[str]:
    return payload["target"]["checks_run"]


def test_long_function_triggers_ht_maint_001(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    _write_py(tmp_path, "long_function.py", _long_function_source())

    code, payload = _run_check_json(tmp_path, capsys)

    assert code == 0  # MEDIUM does not trip the default HIGH gate
    findings = _rule_findings(payload, "HT-MAINT-001")
    assert len(findings) == 1
    finding = findings[0]
    assert finding["check"] == "maintainability.function_length"
    assert finding["severity"] == "medium"
    assert finding["location"]["file"] == "long_function.py"
    assert finding["location"]["line"] == 1  # the def line
    assert finding["symbol"] == "process"
    assert finding["properties"]["length"] > 50


def test_deep_nesting_triggers_ht_maint_002(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    _write_py(tmp_path, "deep.py", _deeply_nested_source())

    code, payload = _run_check_json(tmp_path, capsys)

    assert code == 0
    findings = _rule_findings(payload, "HT-MAINT-002")
    assert len(findings) == 1
    finding = findings[0]
    assert finding["check"] == "maintainability.nesting_depth"
    assert finding["severity"] == "medium"
    assert finding["location"]["file"] == "deep.py"
    assert finding["location"]["line"] == 1
    assert finding["properties"]["depth"] > 5


def test_large_module_triggers_ht_maint_003_low(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    _write_py(tmp_path, "large.py", _large_module_source())

    code, payload = _run_check_json(tmp_path, capsys)

    assert code == 0  # LOW never trips the default HIGH gate
    findings = _rule_findings(payload, "HT-MAINT-003")
    assert len(findings) == 1
    finding = findings[0]
    assert finding["check"] == "maintainability.module_length"
    assert finding["severity"] == "low"
    assert finding["location"]["file"] == "large.py"
    assert finding["properties"]["lines"] > 1000


def test_unannotated_public_functions_trigger_ht_type_001(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    _write_py(tmp_path, "typed.py", _UNANNOTATED_MODULE)

    code, payload = _run_check_json(tmp_path, capsys)

    assert code == 0
    findings = _rule_findings(payload, "HT-TYPE-001")
    assert len(findings) == 1
    finding = findings[0]
    assert finding["check"] == "typing.annotation_coverage"
    assert finding["location"]["file"] == "typed.py"
    assert finding["properties"]["annotated"] < finding["properties"]["total"]


def test_missing_docstrings_trigger_ht_doc_001(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    _write_py(tmp_path, "documented.py", _UNDOCUMENTED_MODULE)

    code, payload = _run_check_json(tmp_path, capsys)

    assert code == 0
    findings = _rule_findings(payload, "HT-DOC-001")
    assert len(findings) == 1
    finding = findings[0]
    assert finding["check"] == "documentation.docstring_coverage"
    assert finding["location"]["file"] == "documented.py"
    assert finding["properties"]["documented"] < finding["properties"]["total"]


def test_clean_module_produces_no_builtin_findings(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    _write_py(tmp_path, "clean.py", _CLEAN_MODULE)

    code, payload = _run_check_json(tmp_path, capsys)

    assert code == 0
    assert payload["findings"] == []
    assert payload["summary"]["total"] == 0
    # All seven built-ins were applicable and ran; none of them fired.
    assert set(_checks_run(payload)) == set(BUILTIN_CHECK_IDS)


def test_two_runs_produce_byte_identical_stdout(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    _write_py(tmp_path, "long_function.py", _long_function_source())
    _write_py(tmp_path, "documented.py", _UNDOCUMENTED_MODULE)

    assert main(["check", str(tmp_path), "--format", "json"]) == 0
    first = capsys.readouterr().out
    assert main(["check", str(tmp_path), "--format", "json"]) == 0
    second = capsys.readouterr().out

    assert first == second


def test_fail_on_medium_exits_one_for_medium_finding(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    _write_py(tmp_path, "long_function.py", _long_function_source())

    # The default HIGH gate stays green...
    assert main(["check", str(tmp_path), "--format", "json", "--fail-on", "high"]) == 0
    capsys.readouterr()

    # ...while raising the gate to MEDIUM turns the same run red.
    code, payload = _run_check_json(tmp_path, capsys, "--fail-on", "medium")
    assert code == 1
    assert _rule_findings(payload, "HT-MAINT-001")


def test_only_runs_the_named_check(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    _write_py(tmp_path, "long_function.py", _long_function_source())

    code, payload = _run_check_json(tmp_path, capsys, "--only", "maintainability.function_length")

    assert code == 0
    assert _checks_run(payload) == ["maintainability.function_length"]
    assert payload["findings"]
    assert {finding["check"] for finding in payload["findings"]} == {
        "maintainability.function_length"
    }


def test_skip_removes_the_named_check(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    _write_py(tmp_path, "long_function.py", _long_function_source())

    code, payload = _run_check_json(tmp_path, capsys, "--skip", "maintainability.function_length")

    assert code == 0
    assert "maintainability.function_length" not in _checks_run(payload)
    assert _rule_findings(payload, "HT-MAINT-001") == []
    # The other built-ins still ran and still found their own issues.
    assert "typing.annotation_coverage" in _checks_run(payload)
    assert _rule_findings(payload, "HT-TYPE-001")


def _write_rule_config(repo: Path, body: str) -> None:
    (repo / "pyproject.toml").write_text(
        '[tool.healthytoon.rules."maintainability.function_length"]\n' + body,
        encoding="utf-8",
    )


def test_config_enabled_false_disables_the_check(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    _write_py(tmp_path, "long_function.py", _long_function_source())
    _write_rule_config(tmp_path, "enabled = false\n")

    code, payload = _run_check_json(tmp_path, capsys)

    assert code == 0
    assert "maintainability.function_length" not in _checks_run(payload)
    assert _rule_findings(payload, "HT-MAINT-001") == []


def test_config_severity_override_is_emitted(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    _write_py(tmp_path, "long_function.py", _long_function_source())
    _write_rule_config(tmp_path, 'severity = "critical"\n')

    code, payload = _run_check_json(tmp_path, capsys)

    # The override is applied before the gate: CRITICAL trips the default HIGH gate.
    assert code == 1
    findings = _rule_findings(payload, "HT-MAINT-001")
    assert len(findings) == 1
    assert findings[0]["severity"] == "critical"


def test_config_option_lowers_max_length(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    # Six lines: only a finding once max_length drops from 50 to 5.
    source = (
        "def tiny(a):\n    b = 1\n    c = 2\n    d = 3\n    e = 4\n    return a + b + c + d + e\n"
    )
    _write_py(tmp_path, "tiny.py", source)
    _write_rule_config(tmp_path, "options = { max_length = 5 }\n")

    code, payload = _run_check_json(tmp_path, capsys)

    assert code == 0
    findings = _rule_findings(payload, "HT-MAINT-001")
    assert len(findings) == 1
    assert findings[0]["properties"]["max"] == 5
    assert findings[0]["properties"]["length"] == 6
