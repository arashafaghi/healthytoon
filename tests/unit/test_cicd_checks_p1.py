"""Unit tests for the P1 CI/CD checks (:mod:`healthytoon.cicd.checks_p1`)."""

from __future__ import annotations

from pathlib import Path

from healthytoon.cicd.checks_p1 import (
    CoverageGateCheck,
    DependencyUpdatesCheck,
    LockfileCheck,
    PythonConsistencyCheck,
    PythonEolCheck,
    PythonMatrixCheck,
    TriggersPullRequestCheck,
)
from healthytoon.cicd.github import collect_artifacts
from healthytoon.cicd.ir import CIContext
from healthytoon.core.types import Severity

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


def _ci(root: Path) -> CIContext:
    return CIContext(
        root=root,
        artifacts=collect_artifacts(root),
        python_eol=_EOL,
        data_as_of="2026-10-16",
        today="2026-10-16",
    )


def _matrix_workflow(versions: str) -> str:
    return (
        "on: push\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
        "    strategy:\n"
        "      matrix:\n"
        f"        python-version: [{versions}]\n"
        "    steps:\n"
        "      - run: pytest -q\n"
    )


# --- ci.python_eol -----------------------------------------------------------


def test_python_eol_flags_version_past_its_eol(tmp_path: Path) -> None:
    _workflow(tmp_path, _matrix_workflow('"3.8", "3.12"'))

    findings = PythonEolCheck().analyze(_ci(tmp_path))

    assert len(findings) == 1
    assert findings[0].rule_id == "HT-CI-006"
    assert findings[0].severity is Severity.HIGH
    assert findings[0].symbol == "3.8"
    assert "2024-10-07" in findings[0].message
    assert "2026-10-16" in findings[0].message


def test_python_eol_passes_for_supported_versions(tmp_path: Path) -> None:
    _workflow(tmp_path, _matrix_workflow('"3.12", "3.13"'))

    assert PythonEolCheck().analyze(_ci(tmp_path)) == []


# --- ci.python_matrix --------------------------------------------------------


def test_python_matrix_flags_a_single_supported_version(tmp_path: Path) -> None:
    _workflow(tmp_path, _matrix_workflow('"3.12"'))

    findings = PythonMatrixCheck().analyze(_ci(tmp_path))

    assert len(findings) == 1
    assert findings[0].rule_id == "HT-CI-007"
    assert findings[0].severity is Severity.MEDIUM


def test_python_matrix_passes_with_two_supported_versions(tmp_path: Path) -> None:
    _workflow(tmp_path, _matrix_workflow('"3.12", "3.13"'))

    assert PythonMatrixCheck().analyze(_ci(tmp_path)) == []


def test_python_matrix_ignores_eol_versions_for_the_count(tmp_path: Path) -> None:
    # 3.8 is past EOL, so only one *supported* minor remains.
    _workflow(tmp_path, _matrix_workflow('"3.8", "3.12"'))

    assert len(PythonMatrixCheck().analyze(_ci(tmp_path))) == 1


# --- ci.python_consistency ---------------------------------------------------


def test_python_consistency_flags_versions_outside_requires_python(tmp_path: Path) -> None:
    _write(tmp_path, "pyproject.toml", '[project]\nrequires-python = ">=3.12"\n')
    _workflow(tmp_path, _matrix_workflow('"3.11", "3.12"'))

    findings = PythonConsistencyCheck().analyze(_ci(tmp_path))

    assert len(findings) == 1
    assert findings[0].rule_id == "HT-CI-008"
    assert findings[0].severity is Severity.MEDIUM
    assert findings[0].symbol == "3.11"


def test_python_consistency_passes_when_versions_agree(tmp_path: Path) -> None:
    _write(tmp_path, "pyproject.toml", '[project]\nrequires-python = ">=3.12"\n')
    _workflow(tmp_path, _matrix_workflow('"3.12", "3.13"'))

    assert PythonConsistencyCheck().analyze(_ci(tmp_path)) == []


def test_python_consistency_skips_without_requires_python(tmp_path: Path) -> None:
    _workflow(tmp_path, _matrix_workflow('"3.11", "3.12"'))

    assert PythonConsistencyCheck().analyze(_ci(tmp_path)) == []


# --- ci.coverage_gate --------------------------------------------------------


def _coverage_workflow(command: str) -> str:
    return (
        "on: push\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        f"      - run: {command}\n"
    )


def test_coverage_gate_flags_measured_but_unenforced(tmp_path: Path) -> None:
    _workflow(tmp_path, _coverage_workflow("pytest --cov=src"))

    findings = CoverageGateCheck().analyze(_ci(tmp_path))

    assert len(findings) == 1
    assert findings[0].rule_id == "HT-CI-009"
    assert findings[0].severity is Severity.MEDIUM


def test_coverage_gate_passes_with_cov_fail_under(tmp_path: Path) -> None:
    _workflow(tmp_path, _coverage_workflow("pytest --cov=src --cov-fail-under=90"))

    assert CoverageGateCheck().analyze(_ci(tmp_path)) == []


def test_coverage_gate_passes_with_coverage_report_fail_under(tmp_path: Path) -> None:
    _workflow(
        tmp_path,
        "on: push\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - run: coverage run -m pytest\n"
        "      - run: coverage report --fail-under=90\n",
    )

    assert CoverageGateCheck().analyze(_ci(tmp_path)) == []


def test_coverage_gate_skips_when_coverage_is_not_measured(tmp_path: Path) -> None:
    _workflow(tmp_path, _coverage_workflow("pytest -q"))

    assert CoverageGateCheck().analyze(_ci(tmp_path)) == []


# --- ci.lockfile -------------------------------------------------------------


def test_lockfile_flags_a_missing_lockfile(tmp_path: Path) -> None:
    _workflow(tmp_path, _coverage_workflow("uv sync --frozen"))

    findings = LockfileCheck().analyze(_ci(tmp_path))

    assert len(findings) == 1
    assert findings[0].rule_id == "HT-CI-010"
    assert findings[0].severity is Severity.MEDIUM
    assert findings[0].location is not None
    assert findings[0].location.file == "."


def test_lockfile_flags_unfrozen_install(tmp_path: Path) -> None:
    _write(tmp_path, "uv.lock", "")
    _workflow(tmp_path, _coverage_workflow("pip install -r requirements.txt"))

    findings = LockfileCheck().analyze(_ci(tmp_path))

    assert len(findings) == 1
    assert findings[0].rule_id == "HT-CI-010"
    assert "--frozen" in findings[0].message


def test_lockfile_passes_with_frozen_install(tmp_path: Path) -> None:
    _write(tmp_path, "uv.lock", "")
    _workflow(tmp_path, _coverage_workflow("uv sync --frozen"))

    assert LockfileCheck().analyze(_ci(tmp_path)) == []


def test_lockfile_passes_without_any_install_step(tmp_path: Path) -> None:
    _write(tmp_path, "uv.lock", "")
    _workflow(tmp_path, _coverage_workflow("pytest -q"))

    assert LockfileCheck().analyze(_ci(tmp_path)) == []


# --- ci.dependency_updates ---------------------------------------------------


def _dependabot(ecosystem: str) -> str:
    return (
        "version: 2\n"
        "updates:\n"
        f'  - package-ecosystem: "{ecosystem}"\n'
        '    directory: "/"\n'
        "    schedule:\n"
        '      interval: "weekly"\n'
    )


def test_dependency_updates_flags_missing_automation(tmp_path: Path) -> None:
    findings = DependencyUpdatesCheck().analyze(_ci(tmp_path))

    assert len(findings) == 1
    assert findings[0].rule_id == "HT-CI-011"
    assert findings[0].severity is Severity.LOW


def test_dependency_updates_passes_with_actions_ecosystem(tmp_path: Path) -> None:
    _write(tmp_path, ".github/dependabot.yml", _dependabot("github-actions"))

    assert DependencyUpdatesCheck().analyze(_ci(tmp_path)) == []


def test_dependency_updates_flags_missing_actions_ecosystem(tmp_path: Path) -> None:
    _write(tmp_path, ".github/dependabot.yml", _dependabot("pip"))

    findings = DependencyUpdatesCheck().analyze(_ci(tmp_path))

    assert len(findings) == 1
    assert findings[0].rule_id == "HT-CI-011"
    assert findings[0].location is not None
    assert findings[0].location.file == ".github/dependabot.yml"


# --- ci.triggers_pull_request ------------------------------------------------


def test_triggers_pull_request_flags_push_only(tmp_path: Path) -> None:
    _workflow(tmp_path, "on: push\njobs: {}\n")

    findings = TriggersPullRequestCheck().analyze(_ci(tmp_path))

    assert len(findings) == 1
    assert findings[0].rule_id == "HT-CI-012"
    assert findings[0].severity is Severity.MEDIUM


def test_triggers_pull_request_passes_when_present(tmp_path: Path) -> None:
    _workflow(tmp_path, "on: [push, pull_request]\njobs: {}\n")

    assert TriggersPullRequestCheck().analyze(_ci(tmp_path)) == []


def test_triggers_pull_request_skips_without_pipelines(tmp_path: Path) -> None:
    assert TriggersPullRequestCheck().analyze(_ci(tmp_path)) == []
