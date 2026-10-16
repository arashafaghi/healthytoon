"""Unit tests for :mod:`healthytoon.cicd.github`."""

from __future__ import annotations

from pathlib import Path

from healthytoon.cicd.github import collect_artifacts, parse_github_workflow
from healthytoon.cicd.ir import PipelineFile
from healthytoon.cicd.loader import RawDocument, load_yaml

_SHA = "a" * 40

_WORKFLOW = f"""\
name: CI
on:
  push:
    branches: [main]
  pull_request:
  workflow_call:
permissions:
  contents: read
concurrency:
  group: ${{{{ github.workflow }}}}
  cancel-in-progress: true
env:
  PYTHONUNBUFFERED: "1"
jobs:
  test:
    name: Test
    runs-on: ubuntu-latest
    timeout-minutes: 30
    strategy:
      matrix:
        python-version: ["3.11", "3.12"]
        os: [ubuntu-latest, macos-latest]
        include:
          - python-version: "3.13"
    permissions:
      contents: read
      id-token: write
    env:
      FOO: bar
    steps:
      - uses: actions/checkout@{_SHA}
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Run tests
        run: pytest -q
        if: ${{{{ success() }}}}
        continue-on-error: true
      - uses: docker://alpine:3.19
      - uses: ./.github/actions/local
      - uses: ${{{{ matrix.action }}}}
  deploy:
    uses: octo-org/example/.github/workflows/reusable.yml@v1
    permissions:
      contents: read
"""


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _parse(tmp_path: Path, text: str, name: str = "wf.yml") -> PipelineFile:
    path = _write(tmp_path / name, text)
    return parse_github_workflow(load_yaml(path), tmp_path)


def _parse_fixture(tmp_path: Path) -> PipelineFile:
    return _parse(tmp_path, _WORKFLOW, ".github/workflows/ci.yml")


def test_parse_representative_workflow(tmp_path: Path) -> None:
    pipeline = _parse_fixture(tmp_path)

    assert pipeline.platform == "github"
    assert pipeline.path == ".github/workflows/ci.yml"
    assert pipeline.triggers == ("push", "pull_request", "workflow_call")
    assert pipeline.is_reusable is True
    assert pipeline.parse_error is None
    assert dict(pipeline.permissions or {}) == {"contents": "read"}
    assert dict(pipeline.concurrency or {}) == {
        "group": "${{ github.workflow }}",
        "cancel-in-progress": True,
    }

    jobs = {job.id: job for job in pipeline.jobs}
    assert set(jobs) == {"deploy", "test"}

    test_job = jobs["test"]
    assert test_job.pointer == "/jobs/test"
    assert test_job.name == "Test"
    assert test_job.runs_on == ("ubuntu-latest",)
    assert test_job.timeout_minutes == 30
    assert dict(test_job.matrix) == {
        "python-version": ("3.11", "3.12"),
        "os": ("ubuntu-latest", "macos-latest"),
    }
    assert test_job.python_versions == ("3.11", "3.12")
    assert dict(test_job.permissions or {}) == {"contents": "read", "id-token": "write"}
    assert dict(test_job.env) == {"FOO": "bar"}
    assert test_job.triggers == ("push", "pull_request", "workflow_call")

    assert [step.pointer for step in test_job.steps] == [
        "/jobs/test/steps/0",
        "/jobs/test/steps/1",
        "/jobs/test/steps/2",
        "/jobs/test/steps/3",
        "/jobs/test/steps/4",
        "/jobs/test/steps/5",
    ]


def test_action_refs_classify_pinned_first_party_docker_local_expression(
    tmp_path: Path,
) -> None:
    test_job = {job.id: job for job in _parse_fixture(tmp_path).jobs}["test"]

    checkout = test_job.steps[0].action
    assert checkout is not None
    assert checkout.pinned_to_sha is True
    assert checkout.is_first_party is True
    assert (checkout.owner, checkout.repo, checkout.ref) == ("actions", "checkout", _SHA)
    assert checkout.location is not None
    assert checkout.location.file == ".github/workflows/ci.yml"

    setup = test_job.steps[1].action
    assert setup is not None
    assert setup.owner == "actions"
    assert setup.repo == "setup-python"
    assert setup.ref == "v5"
    assert setup.pinned_to_sha is False

    run_step = test_job.steps[2]
    assert run_step.run == "pytest -q"
    assert run_step.if_ == "${{ success() }}"
    assert run_step.continue_on_error is True
    assert run_step.action is None

    docker = test_job.steps[3].action
    assert docker is not None
    assert docker.is_docker is True
    assert docker.owner is None and docker.ref is None

    local = test_job.steps[4].action
    assert local is not None
    assert local.is_local is True

    expression = test_job.steps[5].action
    assert expression is not None
    assert expression.is_expression is True


def test_reusable_job_uses_is_parsed(tmp_path: Path) -> None:
    deploy = {job.id: job for job in _parse_fixture(tmp_path).jobs}["deploy"]

    assert deploy.is_reusable is True
    assert len(deploy.uses) == 1
    reusable = deploy.uses[0]
    assert (reusable.owner, reusable.repo, reusable.ref) == ("octo-org", "example", "v1")
    assert reusable.is_first_party is False
    assert deploy.steps == ()


def test_trigger_normalization_from_string_list_and_mapping(tmp_path: Path) -> None:
    assert _parse(tmp_path, "on: push\n").triggers == ("push",)
    assert _parse(tmp_path, "on: [push, pull_request]\n").triggers == (
        "push",
        "pull_request",
    )
    assert _parse(tmp_path, "on:\n  push:\n  workflow_call:\n").triggers == (
        "push",
        "workflow_call",
    )


def test_python_versions_fall_back_to_setup_python(tmp_path: Path) -> None:
    text = (
        "on: push\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/setup-python@v5\n"
        "        with:\n"
        '          python-version: "3.13"\n'
    )

    pipeline = _parse(tmp_path, text)

    assert pipeline.jobs[0].python_versions == ("3.13",)


def test_scalar_permissions_are_preserved(tmp_path: Path) -> None:
    text = (
        "on: push\n"
        "permissions: read-all\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
        "    permissions: write-all\n"
        "    steps: []\n"
    )

    pipeline = _parse(tmp_path, text)

    assert pipeline.permissions == "read-all"
    assert pipeline.jobs[0].permissions == "write-all"


def test_mapping_permissions_stay_mappings(tmp_path: Path) -> None:
    text = (
        "on: push\n"
        "permissions:\n"
        "  contents: read\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps: []\n"
    )

    pipeline = _parse(tmp_path, text)

    assert dict(pipeline.permissions or {}) == {"contents": "read"}


def test_unquoted_python_version_is_preserved(tmp_path: Path) -> None:
    text = (
        "on: push\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
        "    strategy:\n"
        "      matrix:\n"
        "        python-version: [3.10, 3.12]\n"
        "    steps:\n"
        "      - uses: actions/setup-python@v5\n"
        "        with:\n"
        "          python-version: 3.10\n"
    )

    pipeline = _parse(tmp_path, text)

    assert pipeline.jobs[0].python_versions == ("3.10", "3.12")


def test_parse_error_is_propagated(tmp_path: Path) -> None:
    doc = RawDocument(path=str(tmp_path / "x.yml"), data=None, raw_text="", error="boom")

    pipeline = parse_github_workflow(doc, tmp_path)

    assert pipeline.parse_error == "boom"
    assert pipeline.jobs == ()


def test_non_mapping_root_is_a_parse_error(tmp_path: Path) -> None:
    doc = RawDocument(path=str(tmp_path / "x.yml"), data=[1, 2], raw_text="- 1\n", error=None)

    pipeline = parse_github_workflow(doc, tmp_path)

    assert pipeline.parse_error is not None
    assert pipeline.jobs == ()


def test_collect_artifacts_discovers_repository_inputs(tmp_path: Path) -> None:
    _write(tmp_path / ".github" / "workflows" / "ci.yml", "on: push\njobs: {}\n")
    _write(tmp_path / ".github" / "dependabot.yml", "version: 2\n")
    _write(tmp_path / "renovate.json", "{}\n")
    _write(tmp_path / ".pre-commit-config.yaml", "repos: []\n")
    _write(tmp_path / "uv.lock", "")
    _write(tmp_path / "requirements-dev.txt", "pytest\n")
    _write(tmp_path / ".python-version", "3.12\n")
    _write(
        tmp_path / "pyproject.toml",
        '[project]\nname = "demo"\nrequires-python = ">=3.11"\n',
    )
    _write(tmp_path / "packages" / "lib" / "pyproject.toml", '[project]\nname = "lib"\n')
    _write(
        tmp_path / ".git" / "config",
        '[remote "origin"]\n\turl = git@github.com:acme/demo.git\n',
    )

    artifacts = collect_artifacts(tmp_path)

    assert [pipeline.path for pipeline in artifacts.pipelines] == [".github/workflows/ci.yml"]
    assert artifacts.dependabot == ".github/dependabot.yml"
    assert artifacts.renovate == "renovate.json"
    assert artifacts.pre_commit == ".pre-commit-config.yaml"
    assert artifacts.lockfiles == ("requirements-dev.txt", "uv.lock")
    assert artifacts.python_version_file == ".python-version"
    assert artifacts.pyproject_requires_python == ">=3.11"
    assert artifacts.pyproject is not None
    assert artifacts.is_monorepo is True
    assert artifacts.package_roots == (".", "packages/lib")
    assert artifacts.vcs_host == "github"


def test_vcs_host_unknown_without_git_remote(tmp_path: Path) -> None:
    artifacts = collect_artifacts(tmp_path)

    assert artifacts.vcs_host == "unknown"
    assert artifacts.pipelines == ()
