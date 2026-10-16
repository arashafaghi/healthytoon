"""Jenkins adapter: presence detection only.

Groovy ``Jenkinsfile`` pipelines are deliberately not parsed in v1 (ROADMAP
Phase 2 decision 6 and the deferred-scope list): Jenkins support is limited to
detecting that a ``Jenkinsfile`` exists so ``ci.workflow_present`` sees a
pipeline. No Jenkins-specific checks are added and the returned
:class:`~healthytoon.cicd.ir.PipelineFile` carries no jobs or triggers.
"""

from __future__ import annotations

from pathlib import Path

from healthytoon.cicd.ir import PipelineFile

__all__ = ["PLATFORM", "JENKINSFILE", "detect_jenkins"]

#: Platform identifier stored on the presence-only pipeline.
PLATFORM = "jenkins"

#: File name recognised at the repository root.
JENKINSFILE = "Jenkinsfile"


def detect_jenkins(root: Path) -> PipelineFile | None:
    """Return a presence-only pipeline when ``root`` contains a ``Jenkinsfile``.

    Returns ``None`` when no ``Jenkinsfile`` exists at the repository root. The
    Groovy contents are intentionally not read or analysed.
    """

    candidate = Path(root) / JENKINSFILE
    if not candidate.is_file():
        return None
    return PipelineFile(
        platform=PLATFORM,
        path=JENKINSFILE,
        jobs=(),
        triggers=(),
        is_reusable=False,
    )
