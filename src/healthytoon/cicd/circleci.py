"""CircleCI adapter: lower ``.circleci/config.yml`` into the shared CI/CD IR.

CircleCI declares reusable ``jobs:`` with a ``steps:`` list, then binds them into
``workflows:``. This adapter lowers each job's ``steps`` into the neutral
:class:`~healthytoon.cicd.ir.Step` shape so the generic checks can inspect run
commands (for example ``run: pytest``); jobs referenced by a workflow but not
defined under ``jobs:`` are still surfaced as minimal placeholders, and when
``jobs:`` is absent entirely the workflow entries become the job list. A
shorthand string step (``checkout`` or an orb command such as ``python/test``)
is recorded on ``Step.uses`` rather than ``Step.run`` because it names a
reusable command rather than a literal shell command.

External ``orbs:`` (``owner/orb@version``) are not part of the shared IR, so
:func:`circleci_orb_targets` exposes them as :class:`ActionRef` objects and
:func:`is_orb_pinned` reports whether an orb ref is immutable. ``volatile`` and
``dev:`` refs are always considered unpinned; an exact ``major.minor.patch``
version (or a full commit SHA) is considered pinned.

The adapter never raises: a malformed root or a bad document yields a pipeline
with ``parse_error`` set.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType

from healthytoon.cicd.ir import ActionRef, Job, PipelineFile, Step
from healthytoon.cicd.loader import RawDocument
from healthytoon.core.types import Location

__all__ = [
    "PLATFORM",
    "parse_circleci_config",
    "circleci_orb_targets",
    "is_orb_pinned",
]

#: Platform identifier stored on every object this adapter emits.
PLATFORM = "circleci"

#: ``owner/orb@ref`` (the owner may itself be namespaced).
_ORB_RE = re.compile(r"^(?P<owner>[^/@\s]+)/(?P<orb>[^@\s]+)@(?P<ref>\S+)$")

#: An exact ``major.minor.patch`` release is treated as an immutable orb ref.
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


def _relative_posix(path: Path, root: Path) -> str:
    """Return ``path`` relative to ``root`` as a POSIX path, best effort."""

    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _escape_pointer(token: str) -> str:
    """Escape one JSON-pointer reference token (RFC 6901)."""

    return token.replace("~", "~0").replace("/", "~1")


def _scalar_str(value: object) -> str | None:
    """Render a YAML scalar as text; ``None`` for composite or null values."""

    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(value)
    return None


def _opt_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _as_mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): item for key, item in value.items()})
    return MappingProxyType({})


def _as_sequence(value: object) -> list[object]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return []


def _executor_images(raw: Mapping[str, object]) -> tuple[str, ...]:
    """Return the Docker image names a CircleCI job runs under."""

    images: list[str] = []
    for item in _as_sequence(raw.get("docker")):
        text = _scalar_str(item.get("image")) if isinstance(item, Mapping) else _scalar_str(item)
        if text is not None:
            images.append(text)
    return tuple(images)


def _parse_steps(value: object, job_pointer: str) -> tuple[Step, ...]:
    """Lower a CircleCI ``steps:`` list into neutral steps."""

    steps: list[Step] = []
    for index, item in enumerate(_as_sequence(value)):
        pointer = f"{job_pointer}/steps/{index}"
        if isinstance(item, str):
            steps.append(Step(pointer=pointer, uses=item))
            continue
        if not isinstance(item, Mapping):
            steps.append(Step(pointer=pointer))
            continue
        if "run" in item:
            run_value = item.get("run")
            if isinstance(run_value, Mapping):
                command = _scalar_str(run_value.get("command"))
                name = _opt_str(run_value.get("name"))
            else:
                command = _scalar_str(run_value)
                name = None
            steps.append(Step(pointer=pointer, name=name, run=command))
            continue
        steps.append(Step(pointer=pointer))
    return tuple(steps)


def _parse_job(job_id: str, raw: object, file: str) -> Job:
    """Lower one CircleCI job into a :class:`Job`."""

    pointer = f"/jobs/{_escape_pointer(job_id)}"
    if not isinstance(raw, Mapping):
        return Job(id=job_id, platform=PLATFORM, pointer=pointer)
    return Job(
        id=job_id,
        platform=PLATFORM,
        pointer=pointer,
        name=_opt_str(raw.get("name")),
        runs_on=_executor_images(raw),
        steps=_parse_steps(raw.get("steps"), pointer),
        env=_as_mapping(raw.get("environment")),
    )


def _workflow_job_name(entry: object) -> str | None:
    """Return the job name a workflow entry references, or ``None``."""

    if isinstance(entry, str):
        return entry
    if isinstance(entry, Mapping):
        keys = sorted(key for key in entry if isinstance(key, str))
        if len(keys) == 1:
            return keys[0]
        return _opt_str(entry.get("name"))
    return None


def _workflow_jobs(workflows: object) -> dict[str, Job]:
    """Return placeholder jobs referenced by ``workflows:`` entries."""

    jobs: dict[str, Job] = {}
    if not isinstance(workflows, Mapping):
        return jobs
    for name in sorted(key for key in workflows if isinstance(key, str)):
        workflow = workflows[name]
        if not isinstance(workflow, Mapping):
            continue
        entries = _as_sequence(workflow.get("jobs"))
        for index, entry in enumerate(entries):
            job_id = _workflow_job_name(entry)
            if job_id is None or job_id in jobs:
                continue
            pointer = f"/workflows/{_escape_pointer(name)}/jobs/{index}"
            jobs[job_id] = Job(id=job_id, platform=PLATFORM, pointer=pointer)
    return jobs


def parse_circleci_config(doc: RawDocument, root: Path) -> PipelineFile:
    """Parse one loaded CircleCI config document into a :class:`PipelineFile`.

    Never raises: a document that failed to load, or whose root is not a
    mapping, yields a pipeline whose ``parse_error`` is set and whose ``jobs``
    are empty.
    """

    path = _relative_posix(Path(doc.path), root)
    if doc.error is not None:
        return PipelineFile(
            path=path, platform=PLATFORM, parse_error=doc.error, raw_text=doc.raw_text
        )
    data = doc.data
    if not isinstance(data, Mapping):
        return PipelineFile(
            path=path,
            platform=PLATFORM,
            parse_error="CircleCI config root is not a mapping",
            raw_text=doc.raw_text,
        )

    jobs_by_id: dict[str, Job] = {}
    jobs_raw = data.get("jobs")
    if isinstance(jobs_raw, Mapping):
        for job_id in sorted(key for key in jobs_raw if isinstance(key, str)):
            jobs_by_id[job_id] = _parse_job(job_id, jobs_raw[job_id], path)
    for job_id, job in _workflow_jobs(data.get("workflows")).items():
        jobs_by_id.setdefault(job_id, job)

    return PipelineFile(
        path=path,
        platform=PLATFORM,
        jobs=tuple(jobs_by_id[job_id] for job_id in sorted(jobs_by_id)),
        triggers=(),
        env=_as_mapping(data.get("environment")),
        parse_error=None,
        raw_text=doc.raw_text,
    )


def _orb_ref(raw: str) -> ActionRef | None:
    """Build an :class:`ActionRef` from an ``owner/orb@ref`` orb reference."""

    text = raw.strip()
    match = _ORB_RE.match(text)
    if match is None:
        return None
    return ActionRef(
        raw=text,
        owner=match.group("owner"),
        repo=match.group("orb"),
        ref=match.group("ref"),
    )


def _collect_orbs(value: object) -> list[ActionRef]:
    """Collect external orb references, including nested inline-orb ``orbs:``."""

    refs: list[ActionRef] = []
    if not isinstance(value, Mapping):
        return refs
    for alias in sorted(key for key in value if isinstance(key, str)):
        entry = value[alias]
        if isinstance(entry, str):
            ref = _orb_ref(entry)
            if ref is not None:
                refs.append(ref)
        elif isinstance(entry, Mapping):
            refs.extend(_collect_orbs(entry.get("orbs")))
    return refs


def circleci_orb_targets(doc: RawDocument, root: Path) -> tuple[ActionRef, ...]:
    """Return the external orbs referenced by the config as pinning targets."""

    if doc.error is not None or not isinstance(doc.data, Mapping):
        return ()
    file = _relative_posix(Path(doc.path), root)
    refs = _collect_orbs(doc.data.get("orbs"))
    return tuple(
        ActionRef(
            raw=ref.raw,
            owner=ref.owner,
            repo=ref.repo,
            ref=ref.ref,
            location=Location(file=file, line=None),
        )
        for ref in refs
    )


def is_orb_pinned(target: ActionRef) -> bool:
    """True when an orb ref is immutable (full SHA or exact ``x.y.z``)."""

    if target.pinned_to_sha:
        return True
    return target.ref is not None and _SEMVER_RE.fullmatch(target.ref) is not None
