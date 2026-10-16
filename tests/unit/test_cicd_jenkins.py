"""Unit tests for :mod:`healthytoon.cicd.jenkins`."""

from __future__ import annotations

from pathlib import Path

from healthytoon.cicd.checks_p0 import WorkflowPresentCheck
from healthytoon.cicd.github import collect_artifacts
from healthytoon.cicd.ir import CIArtifacts, CIContext
from healthytoon.cicd.jenkins import detect_jenkins


def test_detect_jenkins_returns_presence_only_pipeline(tmp_path: Path) -> None:
    (tmp_path / "Jenkinsfile").write_text("pipeline { agent any }\n", encoding="utf-8")

    pipeline = detect_jenkins(tmp_path)

    assert pipeline is not None
    assert pipeline.platform == "jenkins"
    assert pipeline.path == "Jenkinsfile"
    assert pipeline.jobs == ()
    assert pipeline.triggers == ()
    assert pipeline.is_reusable is False
    assert pipeline.parse_error is None


def test_detect_jenkins_returns_none_without_jenkinsfile(tmp_path: Path) -> None:
    assert detect_jenkins(tmp_path) is None


def test_detect_jenkins_ignores_nested_jenkinsfile(tmp_path: Path) -> None:
    nested = tmp_path / "ci"
    nested.mkdir()
    (nested / "Jenkinsfile").write_text("pipeline {}\n", encoding="utf-8")

    assert detect_jenkins(tmp_path) is None


def test_jenkinsfile_counts_as_a_pipeline(tmp_path: Path) -> None:
    (tmp_path / "Jenkinsfile").write_text("pipeline {}\n", encoding="utf-8")

    context = CIContext(root=tmp_path, artifacts=collect_artifacts(tmp_path))

    assert len(context.artifacts.pipelines) == 1
    assert context.artifacts.pipelines[0].platform == "jenkins"
    assert WorkflowPresentCheck().analyze(context) == []


def test_collect_artifacts_without_jenkinsfile_has_no_jenkins_pipeline(tmp_path: Path) -> None:
    artifacts = collect_artifacts(tmp_path)

    assert artifacts.pipelines == ()
    assert CIArtifacts(pipelines=artifacts.pipelines).pipelines == ()
