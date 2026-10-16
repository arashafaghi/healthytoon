"""Unit tests for :mod:`healthytoon.cicd.circleci`."""

from __future__ import annotations

from pathlib import Path

from healthytoon.cicd.checks_p0 import TestsPresentCheck, WorkflowPresentCheck
from healthytoon.cicd.circleci import (
    circleci_orb_targets,
    is_orb_pinned,
    parse_circleci_config,
)
from healthytoon.cicd.ir import CIArtifacts, CIContext, PipelineFile
from healthytoon.cicd.loader import RawDocument, load_yaml

_CIRCLECI = """\
version: 2.1
orbs:
  python: circleci/python@2.1.1
  volatile-orb: owner/orb@volatile
  dev-orb: owner/other@dev:alpha
jobs:
  test:
    docker:
      - image: cimg/python:3.12
    steps:
      - checkout
      - run:
          name: Run tests
          command: pytest -q
      - python/test
  lint:
    docker:
      - image: cimg/python:3.12
    steps:
      - run: ruff check .
workflows:
  ci:
    jobs:
      - lint
      - test
"""


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _load(tmp_path: Path, text: str | None = None) -> RawDocument:
    path = _write(tmp_path / ".circleci" / "config.yml", text if text is not None else _CIRCLECI)
    return load_yaml(path)


def _parse(tmp_path: Path, text: str | None = None) -> PipelineFile:
    return parse_circleci_config(_load(tmp_path, text), tmp_path)


def test_parse_representative_circleci_config(tmp_path: Path) -> None:
    pipeline = _parse(tmp_path)

    assert pipeline.platform == "circleci"
    assert pipeline.path == ".circleci/config.yml"
    assert pipeline.parse_error is None

    jobs = {job.id: job for job in pipeline.jobs}
    assert set(jobs) == {"lint", "test"}


def test_parse_representative_circleci_test_job(tmp_path: Path) -> None:
    test_job = {job.id: job for job in _parse(tmp_path).jobs}["test"]

    assert test_job.pointer == "/jobs/test"
    assert test_job.runs_on == ("cimg/python:3.12",)
    assert any(step.run == "pytest -q" for step in test_job.steps)
    assert test_job.steps[0].uses == "checkout"
    assert test_job.steps[1].name == "Run tests"


def test_workflow_only_config_yields_placeholder_jobs(tmp_path: Path) -> None:
    text = "version: 2.1\nworkflows:\n  ci:\n    jobs:\n      - build\n      - test\n"

    pipeline = _parse(tmp_path, text)

    assert {job.id for job in pipeline.jobs} == {"build", "test"}


def test_orbs_are_pinning_targets_with_pinned_metadata(tmp_path: Path) -> None:
    doc = _load(tmp_path)

    targets = {target.raw: target for target in circleci_orb_targets(doc, tmp_path)}

    assert set(targets) == {
        "circleci/python@2.1.1",
        "owner/orb@volatile",
        "owner/other@dev:alpha",
    }
    python_orb = targets["circleci/python@2.1.1"]
    assert (python_orb.owner, python_orb.repo, python_orb.ref) == ("circleci", "python", "2.1.1")
    assert is_orb_pinned(python_orb) is True
    assert is_orb_pinned(targets["owner/orb@volatile"]) is False
    assert is_orb_pinned(targets["owner/other@dev:alpha"]) is False


def test_inline_orb_nested_orbs_are_collected(tmp_path: Path) -> None:
    text = "version: 2.1\norbs:\n  my-orb:\n    orbs:\n      node: circleci/node@5.1.0\n"

    targets = circleci_orb_targets(_load(tmp_path, text), tmp_path)

    assert [target.raw for target in targets] == ["circleci/node@5.1.0"]


def test_tests_present_check_recognizes_circleci_pytest(tmp_path: Path) -> None:
    pipeline = _parse(tmp_path)
    context = CIContext(root=tmp_path, artifacts=CIArtifacts(pipelines=(pipeline,)))

    assert TestsPresentCheck().analyze(context) == []
    assert WorkflowPresentCheck().analyze(context) == []


def test_parse_error_is_propagated(tmp_path: Path) -> None:
    doc = RawDocument(
        path=str(tmp_path / ".circleci" / "config.yml"), data=None, raw_text="", error="boom"
    )

    pipeline = parse_circleci_config(doc, tmp_path)

    assert pipeline.parse_error == "boom"
    assert pipeline.jobs == ()


def test_non_mapping_root_is_a_parse_error(tmp_path: Path) -> None:
    doc = RawDocument(
        path=str(tmp_path / ".circleci" / "config.yml"), data=[1, 2], raw_text="- 1\n", error=None
    )

    pipeline = parse_circleci_config(doc, tmp_path)

    assert pipeline.parse_error is not None
    assert pipeline.jobs == ()
