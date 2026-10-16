"""Unit tests for the P0 CI/CD checks (:mod:`healthytoon.cicd.checks_p0`)."""

from __future__ import annotations

import json
from pathlib import Path

from healthytoon.cicd import (
    ActionsPinnedCheck,
    PermissionsExplicitCheck,
    SecretsCheck,
    TestsPresentCheck,
    WorkflowParseCheck,
    WorkflowPresentCheck,
)
from healthytoon.cicd.context import build_context, clear_cache
from healthytoon.cicd.github import collect_artifacts
from healthytoon.cicd.ir import CIContext
from healthytoon.config.models import Config
from healthytoon.core.context import CheckContext
from healthytoon.core.protocols import Check
from healthytoon.core.runner import run_checks
from healthytoon.core.types import Category, Finding, Severity, ToolInfo
from healthytoon.reporters.json import JsonReporter

_SHA = "a" * 40
_DIGEST = "b" * 64
_GH_TOKEN = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

_EOL = {
    "3.8": {"release": "2019-10-14", "eol": "2024-10-07"},
    "3.11": {"release": "2022-10-24", "eol": "2027-10-31"},
    "3.12": {"release": "2023-10-02", "eol": "2028-10-31"},
    "3.13": {"release": "2024-10-07", "eol": "2029-10-31"},
}


def _write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _workflow(root: Path, text: str, name: str = "ci.yml") -> Path:
    return _write(root, f".github/workflows/{name}", text)


def _ci(root: Path, *, today: str = "2026-10-16") -> CIContext:
    return CIContext(
        root=root,
        artifacts=collect_artifacts(root),
        python_eol=_EOL,
        data_as_of="2026-10-16",
        today=today,
    )


def _by_raw(findings: list[Finding]) -> dict[str, Severity]:
    result: dict[str, Severity] = {}
    for finding in findings:
        raw = finding.properties.get("raw")
        if isinstance(raw, str):
            result[raw] = finding.severity
    return result


# --- ci.workflow_present -----------------------------------------------------


def test_workflow_present_fires_when_no_pipeline(tmp_path: Path) -> None:
    findings = WorkflowPresentCheck().analyze(_ci(tmp_path))

    assert len(findings) == 1
    assert findings[0].rule_id == "HT-CI-001"
    assert findings[0].severity is Severity.HIGH
    assert findings[0].location is not None
    assert findings[0].location.file == "."


def test_workflow_present_passes_with_a_pipeline(tmp_path: Path) -> None:
    _workflow(tmp_path, "on: push\njobs: {}\n")

    assert WorkflowPresentCheck().analyze(_ci(tmp_path)) == []


def test_workflow_present_runs_through_the_check_protocol(tmp_path: Path) -> None:
    ctx = CheckContext.from_config(tmp_path, Config())

    result = WorkflowPresentCheck().run(ctx)

    assert isinstance(WorkflowPresentCheck(), Check)
    assert result.check == "ci.workflow_present"
    assert len(result.findings) == 1


# --- ci.workflow_parse -------------------------------------------------------


def test_workflow_parse_reports_parse_errors(tmp_path: Path) -> None:
    _workflow(tmp_path, "on: [push\n", "broken.yml")

    findings = WorkflowParseCheck().analyze(_ci(tmp_path))

    assert len(findings) == 1
    assert findings[0].rule_id == "HT-CI-002"
    assert findings[0].severity is Severity.HIGH
    assert findings[0].location is not None
    assert findings[0].location.file == ".github/workflows/broken.yml"
    assert "could not be parsed" in findings[0].message


def test_workflow_parse_passes_for_valid_yaml(tmp_path: Path) -> None:
    _workflow(tmp_path, "on: push\njobs: {}\n")

    assert WorkflowParseCheck().analyze(_ci(tmp_path)) == []


# --- ci.tests_present --------------------------------------------------------


def test_tests_present_fires_without_a_test_command(tmp_path: Path) -> None:
    _workflow(
        tmp_path,
        "on: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hi\n",
    )

    findings = TestsPresentCheck().analyze(_ci(tmp_path))

    assert len(findings) == 1
    assert findings[0].rule_id == "HT-CI-003"
    assert findings[0].severity is Severity.HIGH


def test_tests_present_passes_with_pytest(tmp_path: Path) -> None:
    _workflow(
        tmp_path,
        "on: push\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - run: python -m pytest -q\n",
    )

    assert TestsPresentCheck().analyze(_ci(tmp_path)) == []


def test_tests_present_passes_with_hatch_run(tmp_path: Path) -> None:
    _workflow(
        tmp_path,
        "on: push\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - run: hatch run test:unit\n",
    )

    assert TestsPresentCheck().analyze(_ci(tmp_path)) == []


# --- ci.actions_pinned -------------------------------------------------------


def test_actions_pinned_classifies_every_reference(tmp_path: Path) -> None:
    _workflow(
        tmp_path,
        "on: push\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
        "    permissions:\n"
        "      contents: read\n"
        "    steps:\n"
        "      - uses: actions/checkout@v5\n"
        f"      - uses: actions/checkout@{_SHA}\n"
        "      - uses: ./.github/actions/local\n"
        "      - uses: docker://alpine:3.19\n"
        f"      - uses: docker://alpine@sha256:{_DIGEST}\n"
        "      - uses: ${{ matrix.action }}\n"
        "      - uses: actions/checkout@main\n",
    )

    findings = ActionsPinnedCheck().analyze(_ci(tmp_path))
    severity = _by_raw(findings)

    assert severity == {
        "actions/checkout@v5": Severity.HIGH,
        "actions/checkout@main": Severity.CRITICAL,
        "docker://alpine:3.19": Severity.HIGH,
        "${{ matrix.action }}": Severity.INFO,
    }
    assert len(findings) == 4


# --- ci.permissions_explicit -------------------------------------------------


def test_permissions_missing_is_flagged_per_job(tmp_path: Path) -> None:
    _workflow(
        tmp_path,
        "on: push\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps: []\n"
        "  lint:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps: []\n",
    )

    findings = PermissionsExplicitCheck().analyze(_ci(tmp_path))

    assert len(findings) == 2
    assert {finding.properties["pointer"] for finding in findings} == {
        "/jobs/test",
        "/jobs/lint",
    }
    assert all(finding.severity is Severity.MEDIUM for finding in findings)


def test_permissions_read_all_scalar_is_accepted(tmp_path: Path) -> None:
    _workflow(
        tmp_path,
        "on: push\npermissions: read-all\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps: []\n",
    )

    assert PermissionsExplicitCheck().analyze(_ci(tmp_path)) == []


def test_permissions_write_all_scalar_is_high(tmp_path: Path) -> None:
    _workflow(
        tmp_path,
        "on: push\npermissions: write-all\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps: []\n",
    )

    findings = PermissionsExplicitCheck().analyze(_ci(tmp_path))

    assert len(findings) == 1
    assert findings[0].severity is Severity.HIGH
    assert findings[0].rule_id == "HT-CI-005"


def test_permissions_workflow_mapping_covers_jobs(tmp_path: Path) -> None:
    _workflow(
        tmp_path,
        "on: push\npermissions:\n  contents: read\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps: []\n",
    )

    assert PermissionsExplicitCheck().analyze(_ci(tmp_path)) == []


# --- ci.secrets --------------------------------------------------------------


def test_secrets_detects_tokens_and_redacts_the_value(tmp_path: Path) -> None:
    _workflow(
        tmp_path,
        "on: push\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        f'      - run: echo "token={_GH_TOKEN}"\n'
        '      - run: echo "password: hunter2"\n',
    )

    ctx = CheckContext.from_config(tmp_path, Config())
    report = run_checks((SecretsCheck(),), ctx, tool=ToolInfo(version="test"))
    output = JsonReporter().render(report)

    payload = json.loads(output)
    findings = [f for f in payload["findings"] if f["rule_id"] == "HT-SEC-001"]
    assert len(findings) == 2
    assert all(f["category"] == "security" for f in findings)
    assert all(f["severity"] == "critical" for f in findings)
    assert all("hardcoded secret detected (value redacted)" in f["message"] for f in findings)

    # The raw values must never reach the rendered report.
    assert _GH_TOKEN not in output
    assert "hunter2" not in output
    assert all("hunter2" not in str(f.get("properties", {})) for f in findings)


def test_secrets_ignores_expression_references(tmp_path: Path) -> None:
    _workflow(
        tmp_path,
        "on: push\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        '      - run: echo "token=${{ secrets.TOKEN }}"\n',
    )

    assert SecretsCheck().analyze(_ci(tmp_path)) == []


def test_secrets_category_is_security() -> None:
    assert SecretsCheck().category is Category.SECURITY
    assert SecretsCheck().default_severity is Severity.CRITICAL


# --- shared context bridge ---------------------------------------------------


def test_build_context_is_memoized_and_clearable(tmp_path: Path) -> None:
    _workflow(tmp_path, "on: push\njobs: {}\n")
    clear_cache()

    first = build_context(tmp_path, Config())
    second = build_context(tmp_path, Config())

    assert first is second
    assert first.today

    clear_cache()
    third = build_context(tmp_path, Config())
    assert third is not first
    clear_cache()
