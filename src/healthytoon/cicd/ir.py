"""Shared intermediate representation for CI/CD configuration.

Every platform adapter (GitHub Actions today; GitLab and CircleCI later) lowers
its raw configuration into these immutable dataclasses so CI/CD checks can be
written once against a single, platform-neutral shape.

The types here are deliberately stdlib-only plus :mod:`healthytoon.core.types`
so adapters and checks can depend on them cheaply. All collections are immutable
(tuples and :class:`~types.MappingProxyType`) so a parsed pipeline cannot be
mutated after construction, which keeps deterministic analysis honest.

Locations are best-effort: the tolerant loader uses ruamel's *safe* loader,
which discards line numbers, so ``Location.line`` is ``None`` and objects carry
a JSON-pointer-like ``pointer`` (for example ``/jobs/test/steps/0/uses``)
instead. Checks should report the pointer when they need to name an exact node.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from healthytoon.core.types import Location

__all__ = [
    "ActionRef",
    "Step",
    "Job",
    "PipelineFile",
    "CIArtifacts",
    "CIContext",
]

#: A commit SHA is exactly 40 hexadecimal characters.
_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")

#: Action owners whose references are maintained by the platform itself.
_FIRST_PARTY_OWNERS = frozenset({"actions", "github"})


def _empty_mapping() -> Mapping[str, object]:
    return MappingProxyType({})


def _empty_matrix() -> Mapping[str, tuple[str, ...]]:
    return MappingProxyType({})


def _empty_eol() -> Mapping[str, Mapping[str, str]]:
    return MappingProxyType({})


@dataclass(frozen=True, slots=True)
class ActionRef:
    """A ``uses:`` reference to an action or reusable workflow.

    ``owner``/``repo``/``ref`` are ``None`` for local (``./...``), Docker
    (``docker://...``), and expression (``${{ ... }}``) references.
    """

    raw: str
    owner: str | None = None
    repo: str | None = None
    ref: str | None = None
    is_local: bool = False
    is_docker: bool = False
    is_expression: bool = False
    location: Location | None = None

    @property
    def pinned_to_sha(self) -> bool:
        """True when ``ref`` is a full 40-character commit SHA."""

        return self.ref is not None and _SHA_RE.fullmatch(self.ref) is not None

    @property
    def is_first_party(self) -> bool:
        """True when the action is published by GitHub itself."""

        return self.owner in _FIRST_PARTY_OWNERS


@dataclass(frozen=True, slots=True)
class Step:
    """A single build step inside a job."""

    pointer: str
    name: str | None = None
    run: str | None = None
    uses: str | None = None
    action: ActionRef | None = None
    with_: Mapping[str, object] = field(default_factory=_empty_mapping)
    env: Mapping[str, object] = field(default_factory=_empty_mapping)
    if_: str | None = None
    timeout_minutes: int | None = None
    continue_on_error: bool = False


@dataclass(frozen=True, slots=True)
class Job:
    """A job in a pipeline file.

    ``triggers`` are the triggers that apply to this job; for platforms whose
    jobs inherit file-level triggers (GitHub Actions) this mirrors
    :attr:`PipelineFile.triggers`, while platforms with per-job triggers
    (GitLab) populate it from the job itself.
    """

    id: str
    platform: str
    pointer: str
    name: str | None = None
    runs_on: tuple[str, ...] = ()
    steps: tuple[Step, ...] = ()
    matrix: Mapping[str, tuple[str, ...]] = field(default_factory=_empty_matrix)
    python_versions: tuple[str, ...] = ()
    triggers: tuple[str, ...] = ()
    permissions: Mapping[str, object] | str | None = None
    env: Mapping[str, object] = field(default_factory=_empty_mapping)
    uses: tuple[ActionRef, ...] = ()
    is_reusable: bool = False
    timeout_minutes: int | None = None


@dataclass(frozen=True, slots=True)
class PipelineFile:
    """A parsed CI configuration file.

    ``parse_error`` is set (and ``jobs`` left empty) when the file could not be
    parsed, so a single bad file never hides the rest of the repository.
    """

    path: str
    platform: str
    jobs: tuple[Job, ...] = ()
    triggers: tuple[str, ...] = ()
    permissions: Mapping[str, object] | str | None = None
    concurrency: str | Mapping[str, object] | None = None
    env: Mapping[str, object] = field(default_factory=_empty_mapping)
    is_reusable: bool = False
    parse_error: str | None = None
    raw_text: str = ""


@dataclass(frozen=True, slots=True)
class CIArtifacts:
    """Repository-level CI/CD artifacts discovered under the target root.

    Path-valued fields are repo-relative POSIX paths; ``package_roots`` uses
    ``"."`` for the repository root itself.
    """

    pipelines: tuple[PipelineFile, ...] = ()
    dependabot: str | None = None
    renovate: str | None = None
    pre_commit: str | None = None
    lockfiles: tuple[str, ...] = ()
    python_version_file: str | None = None
    pyproject_requires_python: str | None = None
    pyproject: Mapping[str, object] | None = None
    is_monorepo: bool = False
    package_roots: tuple[str, ...] = ()
    vcs_host: str = "unknown"


@dataclass(frozen=True, slots=True)
class CIContext:
    """Inputs a CI/CD check needs: artifacts plus bundled EOL data and options.

    ``today`` is an ISO date supplied by the caller so EOL checks are
    deterministic and testable. ``python_eol`` maps a major.minor cycle to its
    ``{"release": ..., "eol": ...}`` dates.
    """

    root: Path
    artifacts: CIArtifacts
    python_eol: Mapping[str, Mapping[str, str]] = field(default_factory=_empty_eol)
    data_as_of: str = ""
    today: str = ""
    options: Mapping[str, object] = field(default_factory=_empty_mapping)
