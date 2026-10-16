"""Unit tests for :mod:`healthytoon.cicd.gitlab`."""

from __future__ import annotations

from pathlib import Path

from healthytoon.cicd.checks_p0 import TestsPresentCheck, WorkflowPresentCheck
from healthytoon.cicd.gitlab import gitlab_pinning_targets, parse_gitlab_ci
from healthytoon.cicd.ir import CIArtifacts, CIContext, PipelineFile
from healthytoon.cicd.loader import RawDocument, load_yaml

_GITLAB = """\
stages:
  - test
image: python:3.12
variables:
  PIP_CACHE_DIR: "$CI_PROJECT_DIR/.cache"
workflow:
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
before_script:
  - echo setup
test:
  stage: test
  tags:
    - docker
  script:
    - python -m pytest
    - !reference [.shared, script]
  only:
    - merge_requests
include:
  - remote: https://example.com/ci.yml
  - local: templates/foo.yml
"""


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _load(tmp_path: Path, text: str | None = None) -> RawDocument:
    path = _write(tmp_path / ".gitlab-ci.yml", text if text is not None else _GITLAB)
    return load_yaml(path)


def _parse(tmp_path: Path, text: str | None = None) -> PipelineFile:
    return parse_gitlab_ci(_load(tmp_path, text), tmp_path)


def test_parse_representative_gitlab_ci(tmp_path: Path) -> None:
    pipeline = _parse(tmp_path)

    assert pipeline.platform == "gitlab"
    assert pipeline.path == ".gitlab-ci.yml"
    assert pipeline.parse_error is None

    jobs = {job.id: job for job in pipeline.jobs}
    assert "test" in jobs
    for reserved in (
        "stages",
        "variables",
        "include",
        "default",
        "workflow",
        "image",
        "before_script",
        "after_script",
    ):
        assert reserved not in jobs

    test_job = jobs["test"]
    assert test_job.pointer == "/test"
    assert test_job.runs_on == ("docker",)
    assert [step.run for step in test_job.steps] == ["echo setup", "python -m pytest"]
    assert test_job.triggers == ("pull_request",)
    assert "pull_request" in pipeline.triggers

    assert dict(pipeline.env)["image"] == "python:3.12"
    assert dict(pipeline.env)["PIP_CACHE_DIR"] == "$CI_PROJECT_DIR/.cache"


def test_reference_tag_does_not_crash(tmp_path: Path) -> None:
    text = "test:\n  script:\n    - !reference [.shared, script]\n    - python -m pytest\n"

    pipeline = _parse(tmp_path, text)

    assert pipeline.parse_error is None
    job = pipeline.jobs[0]
    assert [step.run for step in job.steps] == ["python -m pytest"]


def test_remote_include_is_a_pinning_target(tmp_path: Path) -> None:
    doc = _load(tmp_path)

    targets = gitlab_pinning_targets(doc, tmp_path)

    assert [target.raw for target in targets] == ["https://example.com/ci.yml"]
    assert targets[0].is_local is False


def test_project_include_records_ref(tmp_path: Path) -> None:
    text = (
        "include:\n"
        "  - project: group/subgroup/project\n"
        "    ref: main\n"
        "    file: /ci/templates.yml\n"
    )

    targets = gitlab_pinning_targets(_load(tmp_path, text), tmp_path)

    assert len(targets) == 1
    assert targets[0].owner == "group"
    assert targets[0].repo == "subgroup"
    assert targets[0].ref == "main"
    assert targets[0].raw == "group/subgroup/project/ci/templates.yml@main"


def test_tests_present_check_recognizes_gitlab_pytest(tmp_path: Path) -> None:
    pipeline = _parse(tmp_path)
    context = CIContext(root=tmp_path, artifacts=CIArtifacts(pipelines=(pipeline,)))

    assert TestsPresentCheck().analyze(context) == []
    assert WorkflowPresentCheck().analyze(context) == []


def test_parse_error_is_propagated(tmp_path: Path) -> None:
    doc = RawDocument(path=str(tmp_path / ".gitlab-ci.yml"), data=None, raw_text="", error="boom")

    pipeline = parse_gitlab_ci(doc, tmp_path)

    assert pipeline.parse_error == "boom"
    assert pipeline.jobs == ()


def test_non_mapping_root_is_a_parse_error(tmp_path: Path) -> None:
    doc = RawDocument(
        path=str(tmp_path / ".gitlab-ci.yml"), data=[1, 2], raw_text="- 1\n", error=None
    )

    pipeline = parse_gitlab_ci(doc, tmp_path)

    assert pipeline.parse_error is not None
    assert pipeline.jobs == ()


def test_pinning_targets_empty_for_bad_document(tmp_path: Path) -> None:
    doc = RawDocument(path=str(tmp_path / ".gitlab-ci.yml"), data=None, raw_text="", error="boom")

    assert gitlab_pinning_targets(doc, tmp_path) == ()
