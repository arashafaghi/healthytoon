"""GitLab CI adapter: lower ``.gitlab-ci.yml`` into the shared CI/CD IR.

GitLab's schema is job-per-top-level-key rather than GitHub's ``jobs:`` mapping,
so every top-level key outside a small set of reserved keywords is treated as a
job. ``script`` and ``before_script`` become :class:`~healthytoon.cicd.ir.Step`
objects whose ``run`` text is what the generic P0/P1 checks (tests-present,
coverage, lockfile) inspect.

The adapter never raises: malformed roots, non-mapping jobs, and unknown YAML
tags such as ``!reference`` (which the tolerant loader already lowers to a
:class:`~healthytoon.cicd.loader.RawTag`) are tolerated and represented with
partial data plus a ``parse_error`` where appropriate.

Remote ``include:`` references are not part of the shared IR, so they are
exposed separately through :func:`gitlab_pinning_targets`, which returns them as
:class:`~healthytoon.cicd.ir.ActionRef` objects. That keeps the adapter
platform-specific metadata out of the neutral IR while still surfacing the
references for a future pinning check.

Global and per-job ``before_script`` commands are prepended to every job's steps
so the run text a job actually executes is visible to checks. ``variables`` and
``image`` are captured as context on the pipeline/job ``env`` mapping (the IR has
no dedicated field for them).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from types import MappingProxyType

from healthytoon.cicd._shared import (
    _as_mapping,
    _as_str_tuple,
    _escape_pointer,
    _mapping_document,
    _opt_str,
    _pipeline_root,
    _scalar_str,
)
from healthytoon.cicd.ir import ActionRef, Job, PipelineFile, Step
from healthytoon.cicd.loader import RawDocument, RawTag
from healthytoon.core.types import Location

__all__ = ["PLATFORM", "parse_gitlab_ci", "gitlab_pinning_targets"]

#: Platform identifier stored on every object this adapter emits.
PLATFORM = "gitlab"

#: Top-level keys that are global configuration, not jobs.
_RESERVED_KEYS = frozenset(
    {
        "stages",
        "variables",
        "include",
        "default",
        "workflow",
        "image",
        "before_script",
        "after_script",
    }
)

#: GitLab pipeline sources that mean "runs for a pull/merge request".
_MERGE_REQUEST_SOURCES = frozenset({"merge_requests", "merge_request", "merge_request_event"})

#: ``$CI_PIPELINE_SOURCE``-style tokens recognised inside ``rules: if:`` strings.
_SOURCE_TOKENS = (
    "merge_request_event",
    "merge_requests",
    "pull_request",
    "push",
    "schedule",
    "web",
    "tag",
)

#: GitLab raw-file URLs embed the ref as ``/-/raw/<ref>/...``.
_REMOTE_REF_RE = re.compile(r"/-/raw/([^/]+)/")


def _dedupe(names: Iterable[str]) -> tuple[str, ...]:
    """Return ``names`` in first-seen order with duplicates removed."""

    seen: dict[str, None] = {}
    for name in names:
        seen.setdefault(name, None)
    return tuple(seen)


def _normalize_trigger(name: str) -> str:
    """Map GitLab merge-request sources onto the neutral ``pull_request`` name."""

    return "pull_request" if name in _MERGE_REQUEST_SOURCES else name


def _triggers_from(value: object) -> list[str]:
    """Extract trigger names from an ``only:``/``except:`` scalar or sequence."""

    if isinstance(value, str):
        return [_normalize_trigger(value)]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_normalize_trigger(item) for item in value if isinstance(item, str)]
    return []


def _triggers_from_rules(rules: object) -> list[str]:
    """Extract trigger names from ``rules: - if: $CI_PIPELINE_SOURCE == ...``."""

    if not isinstance(rules, Sequence) or isinstance(rules, (str, bytes, bytearray)):
        return []
    names: list[str] = []
    for rule in rules:
        if not isinstance(rule, Mapping):
            continue
        condition = rule.get("if")
        if isinstance(condition, str):
            names.extend(
                _normalize_trigger(token) for token in _SOURCE_TOKENS if token in condition
            )
    return names


def _workflow_triggers(workflow: object) -> tuple[str, ...]:
    """Return file-level triggers declared under ``workflow:``."""

    if not isinstance(workflow, Mapping):
        return ()
    names: list[str] = []
    for key in ("only", "except"):
        names.extend(_triggers_from(workflow.get(key)))
    names.extend(_triggers_from_rules(workflow.get("rules")))
    return _dedupe(names)


def _job_triggers(raw: Mapping[str, object]) -> tuple[str, ...]:
    """Return the triggers declared on a single job."""

    names: list[str] = []
    for key in ("only", "except"):
        names.extend(_triggers_from(raw.get(key)))
    names.extend(_triggers_from_rules(raw.get("rules")))
    return _dedupe(names)


def _command_text(value: object) -> str | None:
    """Render one ``script`` entry as run text, tolerating ``!reference`` tags."""

    if isinstance(value, RawTag):
        return _scalar_str(value.value)
    return _scalar_str(value)


def _flatten_commands(value: object) -> list[str]:
    """Flatten GitLab's nested ``script`` structures into plain command strings."""

    if value is None:
        return []
    if isinstance(value, RawTag):
        text = _command_text(value)
        return [text] if text is not None else []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        commands: list[str] = []
        for item in value:
            commands.extend(_flatten_commands(item))
        return commands
    text = _command_text(value)
    return [text] if text is not None else []


def _image_text(value: object) -> str | None:
    """Return the image name from a scalar or ``{name: ...}`` image declaration."""

    if isinstance(value, Mapping):
        return _scalar_str(value.get("name"))
    if isinstance(value, RawTag):
        return _scalar_str(value.value)
    return _scalar_str(value)


def _parse_job(
    job_id: str,
    raw: object,
    file_triggers: tuple[str, ...],
    default_before_script: tuple[str, ...],
) -> Job:
    """Lower one GitLab job into a :class:`Job`."""

    pointer = f"/{_escape_pointer(job_id)}"
    if not isinstance(raw, Mapping):
        return Job(
            id=job_id,
            platform=PLATFORM,
            pointer=pointer,
            triggers=file_triggers,
        )

    steps: list[Step] = []
    before = tuple(_flatten_commands(raw.get("before_script"))) or default_before_script
    for index, command in enumerate(before):
        steps.append(Step(pointer=f"{pointer}/before_script/{index}", run=command))
    for index, command in enumerate(_flatten_commands(raw.get("script"))):
        steps.append(Step(pointer=f"{pointer}/script/{index}", run=command))

    own_triggers = _job_triggers(raw)
    env = dict(_as_mapping(raw.get("variables")))
    image = _image_text(raw.get("image"))
    if image is not None:
        env["image"] = image

    return Job(
        id=job_id,
        platform=PLATFORM,
        pointer=pointer,
        runs_on=_as_str_tuple(raw.get("tags")),
        steps=tuple(steps),
        triggers=own_triggers or file_triggers,
        env=MappingProxyType(env),
    )


def parse_gitlab_ci(doc: RawDocument, root: Path) -> PipelineFile:
    """Parse one loaded ``.gitlab-ci.yml`` document into a :class:`PipelineFile`.

    Never raises: a document that failed to load, or whose root is not a
    mapping, yields a pipeline whose ``parse_error`` is set and whose ``jobs``
    are empty.
    """

    loaded = _pipeline_root(doc, root, PLATFORM, ".gitlab-ci.yml root is not a mapping")
    if isinstance(loaded, PipelineFile):
        return loaded
    path, data = loaded

    file_triggers = _workflow_triggers(data.get("workflow"))
    default_before_script = tuple(_flatten_commands(data.get("before_script")))
    job_ids = sorted(key for key in data if isinstance(key, str) and key not in _RESERVED_KEYS)
    jobs = [
        _parse_job(job_id, data[job_id], file_triggers, default_before_script) for job_id in job_ids
    ]

    env = dict(_as_mapping(data.get("variables")))
    image = _image_text(data.get("image"))
    if image is not None:
        env["image"] = image

    triggers = _dedupe([*file_triggers, *(trigger for job in jobs for trigger in job.triggers)])

    return PipelineFile(
        path=path,
        platform=PLATFORM,
        jobs=tuple(jobs),
        triggers=triggers,
        env=MappingProxyType(env),
        parse_error=None,
        raw_text=doc.raw_text,
    )


def _is_remote_include(text: str) -> bool:
    """True when an include string names a remote resource rather than a path."""

    return "://" in text or text.startswith(("git@", "git://", "ssh://"))


def _remote_include_ref(raw: str, file: str) -> ActionRef:
    """Build an :class:`ActionRef` for a remote ``include:`` URL."""

    match = _REMOTE_REF_RE.search(raw)
    return ActionRef(
        raw=raw,
        ref=match.group(1) if match is not None else None,
        location=Location(file=file, line=None),
    )


def _split_owner_repo(target: str) -> tuple[str | None, str | None]:
    """Split ``group/subgroup/project`` into a best-effort owner/repo pair."""

    parts = [part for part in target.split("/") if part]
    if len(parts) >= 2:
        return parts[0], parts[1]
    if parts:
        return None, parts[0]
    return None, None


def _include_ref(entry: object, file: str) -> ActionRef | None:
    """Return the pinning target for one ``include`` entry, or ``None``."""

    if isinstance(entry, str):
        if _is_remote_include(entry):
            return _remote_include_ref(entry, file)
        return None
    if not isinstance(entry, Mapping):
        return None

    remote = entry.get("remote")
    if isinstance(remote, str):
        return _remote_include_ref(remote, file)

    component = entry.get("component")
    if isinstance(component, str):
        target, _, component_ref = component.partition("@")
        owner, repo = _split_owner_repo(target)
        return ActionRef(
            raw=component,
            owner=owner,
            repo=repo,
            ref=component_ref or None,
            location=Location(file=file, line=None),
        )

    project = entry.get("project")
    if isinstance(project, str):
        ref = _opt_str(entry.get("ref"))
        file_part = _opt_str(entry.get("file"))
        raw = f"{project}/{file_part.lstrip('/')}" if file_part else project
        if ref is not None:
            raw = f"{raw}@{ref}"
        owner, repo = _split_owner_repo(project)
        return ActionRef(
            raw=raw,
            owner=owner,
            repo=repo,
            ref=ref,
            location=Location(file=file, line=None),
        )
    return None


def _include_entries(value: object) -> list[object]:
    """Normalize an ``include:`` scalar, mapping, or list into a flat list."""

    if value is None:
        return []
    if isinstance(value, (str, Mapping)):
        return [value]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return []


def gitlab_pinning_targets(doc: RawDocument, root: Path) -> tuple[ActionRef, ...]:
    """Return the remote ``include:`` references that should be pinned.

    Local path/template includes are not pinning targets and are omitted. The
    list is deterministic (document order) and empty when the document failed to
    load or is not a mapping.
    """

    loaded = _mapping_document(doc, root)
    if loaded is None:
        return ()
    file, data = loaded
    targets: list[ActionRef] = []
    for entry in _include_entries(data.get("include")):
        ref = _include_ref(entry, file)
        if ref is not None:
            targets.append(ref)
    return tuple(targets)
