"""P1 CI/CD checks: Python support, matrix, coverage, lockfiles, updates, triggers.

Like the P0 checks, these reason over the repository-level
:class:`~healthytoon.cicd.ir.CIContext` (built once per run) rather than a single
file kind. Python-version checks compare against a declared range and pass only
when at least two current minor versions are exercised.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

from healthytoon.cicd.checks_p0 import BaseCICheck, _finding
from healthytoon.cicd.ir import CIContext, PipelineFile
from healthytoon.cicd.loader import load_yaml
from healthytoon.core.types import Finding, Severity

__all__ = [
    "PythonEolCheck",
    "PythonMatrixCheck",
    "PythonConsistencyCheck",
    "CoverageGateCheck",
    "LockfileCheck",
    "DependencyUpdatesCheck",
    "TriggersPullRequestCheck",
]

#: ``major.minor`` prefix of a version label.
_CYCLE_RE = re.compile(r"^(\d+)\.(\d+)")

#: A dependency install command that can be frozen.
_INSTALL_RE = re.compile(
    r"\b(?:uv\s+(?:sync|pip\s+install)|pip\s+install|poetry\s+install|pipenv\s+install)\b"
)

#: Flags that force a reproducible, lockfile-backed install.
_FROZEN_RE = re.compile(r"--(?:frozen|locked|require-hashes)\b|--no-update\b")

#: Ways to measure coverage.
_COVERAGE_MEASURE_RE = re.compile(r"coverage\s+run|--cov\b|pytest-cov")

#: Ways to fail the build when coverage drops.
_COVERAGE_ENFORCE_RE = re.compile(r"--(?:cov-)?fail-under\b|fail_ci_if_error")


def _cycle(version: str) -> str | None:
    """Return the ``major.minor`` cycle of ``version``, or ``None`` if unparseable."""

    match = _CYCLE_RE.match(version.strip())
    if match is None:
        return None
    return f"{int(match.group(1))}.{int(match.group(2))}"


def _eol_date(cycle: str, ci: CIContext) -> str | None:
    entry = ci.python_eol.get(cycle)
    if entry is None:
        return None
    value = entry.get("eol")
    return value if isinstance(value, str) else None


def _is_supported(cycle: str, ci: CIContext) -> bool:
    """True when ``cycle`` is known and not yet end-of-life."""

    eol = _eol_date(cycle, ci)
    return eol is not None and eol >= ci.today


def _ci_versions(ci: CIContext) -> list[tuple[PipelineFile, str, str]]:
    """Return ``(pipeline, pointer, cycle)`` for every tested Python cycle."""

    versions: list[tuple[PipelineFile, str, str]] = []
    for pipeline in ci.artifacts.pipelines:
        for job in pipeline.jobs:
            for version in job.python_versions:
                cycle = _cycle(version)
                if cycle is not None:
                    versions.append((pipeline, job.pointer, cycle))
    return versions


class PythonEolCheck(BaseCICheck):
    """Flag CI that still tests a Python version past its end-of-life date."""

    id: str = "ci.python_eol"
    rule_id: str = "HT-CI-006"
    default_severity: Severity = Severity.HIGH

    def analyze(self, ci: CIContext) -> list[Finding]:
        findings: list[Finding] = []
        seen: set[tuple[str, str, str]] = set()
        for pipeline, pointer, cycle in _ci_versions(ci):
            eol = _eol_date(cycle, ci)
            if eol is None or eol >= ci.today:
                continue
            key = (pipeline.path, pointer, cycle)
            if key in seen:
                continue
            seen.add(key)
            findings.append(
                _finding(
                    self,
                    severity=Severity.HIGH,
                    title="Python version past end of life",
                    message=(
                        f"Python {cycle} reached end of life on {eol} "
                        f"(EOL data as of {ci.data_as_of})."
                    ),
                    file=pipeline.path,
                    pointer=pointer,
                    symbol=cycle,
                    extra={"version": cycle, "eol": eol, "data_as_of": ci.data_as_of},
                )
            )
        return findings


class PythonMatrixCheck(BaseCICheck):
    """Flag CI that tests fewer than two currently supported Python minors."""

    id: str = "ci.python_matrix"
    rule_id: str = "HT-CI-007"
    default_severity: Severity = Severity.MEDIUM

    def analyze(self, ci: CIContext) -> list[Finding]:
        if not ci.artifacts.pipelines:
            return []
        cycles = {
            cycle for _pipeline, _pointer, cycle in _ci_versions(ci) if _is_supported(cycle, ci)
        }
        if len(cycles) >= 2:
            return []
        return [
            _finding(
                self,
                severity=Severity.MEDIUM,
                title="Too few Python versions tested",
                message=(
                    f"CI tests {len(cycles)} currently supported Python minor version(s); "
                    "at least 2 are recommended."
                ),
                file=".",
                pointer="/",
                extra={"supported_versions": sorted(cycles)},
            )
        ]


def _parse_version(text: str) -> tuple[int, ...]:
    parts = [int(part) for part in re.findall(r"\d+", text)]
    return tuple(parts) if parts else (0,)


def _compare(left: tuple[int, ...], right: tuple[int, ...]) -> int:
    size = max(len(left), len(right))
    padded_left = left + (0,) * (size - len(left))
    padded_right = right + (0,) * (size - len(right))
    if padded_left == padded_right:
        return 0
    return 1 if padded_left > padded_right else -1


def _satisfies(version: str, specifier: str) -> bool:
    """Return True when ``version`` satisfies a comma-separated PEP 440 range."""

    candidate = _parse_version(version)
    for raw in specifier.split(","):
        constraint = raw.strip()
        if not constraint:
            continue
        match = re.match(r"(===|==|!=|>=|<=|~=|>|<)\s*(\S+)", constraint)
        if match is None:
            continue
        operator, target = match.group(1), match.group(2)
        if operator in ("==", "===") and target.endswith(".*"):
            base = _parse_version(target[:-2])
            if candidate[: len(base)] != base:
                return False
            continue
        bound = _parse_version(target)
        comparison = _compare(candidate, bound)
        if operator == ">=" and comparison < 0:
            return False
        if operator == ">" and comparison <= 0:
            return False
        if operator == "<=" and comparison > 0:
            return False
        if operator == "<" and comparison >= 0:
            return False
        if operator in ("==", "===") and comparison != 0:
            return False
        if operator == "!=" and comparison == 0:
            return False
        if operator == "~=":
            if comparison < 0:
                return False
            upper: tuple[int, ...] = (
                (bound[0] + 1, 0) if len(bound) <= 2 else (*bound[:-2], bound[-2] + 1)
            )
            if _compare(candidate, upper) >= 0:
                return False
    return True


class PythonConsistencyCheck(BaseCICheck):
    """Flag CI Python versions that fall outside ``requires-python``."""

    id: str = "ci.python_consistency"
    rule_id: str = "HT-CI-008"
    default_severity: Severity = Severity.MEDIUM

    def analyze(self, ci: CIContext) -> list[Finding]:
        requires = ci.artifacts.pyproject_requires_python
        if not requires:
            return []
        findings: list[Finding] = []
        seen: set[tuple[str, str, str]] = set()
        for pipeline, pointer, cycle in _ci_versions(ci):
            if _satisfies(cycle, requires):
                continue
            key = (pipeline.path, pointer, cycle)
            if key in seen:
                continue
            seen.add(key)
            findings.append(
                _finding(
                    self,
                    severity=Severity.MEDIUM,
                    title="CI Python version is inconsistent with pyproject.toml",
                    message=f"CI tests Python {cycle}, outside requires-python '{requires}'.",
                    file=pipeline.path,
                    pointer=pointer,
                    symbol=cycle,
                    extra={"version": cycle, "requires_python": requires},
                )
            )
        return findings


class CoverageGateCheck(BaseCICheck):
    """Flag CI that measures coverage but never enforces a threshold."""

    id: str = "ci.coverage_gate"
    rule_id: str = "HT-CI-009"
    default_severity: Severity = Severity.MEDIUM

    def analyze(self, ci: CIContext) -> list[Finding]:
        if not ci.artifacts.pipelines:
            return []
        if not any(_measures_coverage(pipeline) for pipeline in ci.artifacts.pipelines):
            return []
        if any(_enforces_coverage(pipeline) for pipeline in ci.artifacts.pipelines):
            return []
        return [
            _finding(
                self,
                severity=Severity.MEDIUM,
                title="Coverage is measured but not enforced",
                message=(
                    "Coverage is collected without a failing threshold "
                    "(--cov-fail-under or coverage report --fail-under)."
                ),
                file=".",
                pointer="/",
            )
        ]


def _run_commands(pipeline: PipelineFile) -> list[str]:
    return [step.run for job in pipeline.jobs for step in job.steps if step.run is not None]


def _measures_coverage(pipeline: PipelineFile) -> bool:
    return any(_COVERAGE_MEASURE_RE.search(command) for command in _run_commands(pipeline))


def _enforces_coverage(pipeline: PipelineFile) -> bool:
    return any(_COVERAGE_ENFORCE_RE.search(command) for command in _run_commands(pipeline))


class LockfileCheck(BaseCICheck):
    """Flag a missing lockfile or a dependency install that is not frozen."""

    id: str = "ci.lockfile"
    rule_id: str = "HT-CI-010"
    default_severity: Severity = Severity.MEDIUM

    def analyze(self, ci: CIContext) -> list[Finding]:
        findings: list[Finding] = []
        if not ci.artifacts.lockfiles:
            findings.append(
                _finding(
                    self,
                    severity=Severity.MEDIUM,
                    title="No dependency lockfile found",
                    message="No lockfile (uv.lock, poetry.lock, or Pipfile.lock) was found.",
                    file=".",
                    pointer="/",
                )
            )
        for pipeline in ci.artifacts.pipelines:
            for job in pipeline.jobs:
                for step in job.steps:
                    command = step.run
                    if command is None or not _INSTALL_RE.search(command):
                        continue
                    if _FROZEN_RE.search(command):
                        continue
                    findings.append(
                        _finding(
                            self,
                            severity=Severity.MEDIUM,
                            title="Dependency install is not frozen",
                            message=(
                                "A dependency install runs without --frozen/--locked/"
                                "--require-hashes, so it can resolve different versions."
                            ),
                            file=pipeline.path,
                            pointer=step.pointer,
                        )
                    )
        return findings


class DependencyUpdatesCheck(BaseCICheck):
    """Flag missing or incomplete dependency-update automation."""

    id: str = "ci.dependency_updates"
    rule_id: str = "HT-CI-011"
    default_severity: Severity = Severity.LOW

    def analyze(self, ci: CIContext) -> list[Finding]:
        artifacts = ci.artifacts
        if artifacts.dependabot is None and artifacts.renovate is None:
            return [
                _finding(
                    self,
                    severity=Severity.LOW,
                    title="No dependency update automation",
                    message="Neither Dependabot nor Renovate is configured.",
                    file=".",
                    pointer="/",
                )
            ]
        findings: list[Finding] = []
        if artifacts.dependabot is not None and not _dependabot_tracks_actions(
            ci.root, artifacts.dependabot
        ):
            findings.append(
                _finding(
                    self,
                    severity=Severity.LOW,
                    title="Dependabot does not track GitHub Actions",
                    message="Dependabot is configured without a 'github-actions' ecosystem entry.",
                    file=artifacts.dependabot,
                    pointer="/",
                )
            )
        if artifacts.renovate is not None and not _renovate_tracks_actions(
            ci.root, artifacts.renovate
        ):
            findings.append(
                _finding(
                    self,
                    severity=Severity.LOW,
                    title="Renovate does not track GitHub Actions",
                    message="Renovate is configured without a 'github-actions' manager entry.",
                    file=artifacts.renovate,
                    pointer="/",
                )
            )
        return findings


def _dependabot_tracks_actions(root: Path, relative: str) -> bool:
    doc = load_yaml(root / relative)
    data = doc.data
    if not isinstance(data, Mapping):
        return True  # Unreadable/unparseable: do not assert a missing ecosystem.
    updates = data.get("updates")
    if not isinstance(updates, list):
        return False
    for entry in updates:
        if isinstance(entry, Mapping) and entry.get("package-ecosystem") == "github-actions":
            return True
    return False


def _renovate_tracks_actions(root: Path, relative: str) -> bool:
    try:
        text = (root / relative).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return True
    return "github-actions" in text


class TriggersPullRequestCheck(BaseCICheck):
    """Flag CI that never runs on ``pull_request``."""

    id: str = "ci.triggers_pull_request"
    rule_id: str = "HT-CI-012"
    default_severity: Severity = Severity.MEDIUM

    def analyze(self, ci: CIContext) -> list[Finding]:
        pipelines = [
            pipeline
            for pipeline in ci.artifacts.pipelines
            if pipeline.platform in {"github", "gitlab"}
        ]
        if not pipelines:
            return []
        if any("pull_request" in pipeline.triggers for pipeline in pipelines):
            return []
        return [
            _finding(
                self,
                severity=Severity.MEDIUM,
                title="No pipeline runs on pull_request",
                message="No CI pipeline is triggered by 'pull_request'.",
                file=".",
                pointer="/",
            )
        ]
