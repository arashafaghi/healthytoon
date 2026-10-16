"""GitHub Actions adapter: lower workflows into the shared CI/CD IR.

The adapter is intentionally forgiving because workflows in the wild are messy:
non-mapping jobs, steps that are strings, or unparsable files are represented
with empty/partial data plus a ``parse_error`` instead of raising. Jobs are
emitted sorted by id and workflows by path so downstream checks are
deterministic.

Line numbers are unavailable: the tolerant loader uses ruamel's *safe* loader,
which discards them. Every parsed node therefore carries a JSON-pointer-like
``pointer`` (``/jobs/test/steps/0/uses``) and a :class:`Location` whose ``line``
is ``None``.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from urllib.parse import urlsplit

from healthytoon.cicd.ir import ActionRef, CIArtifacts, Job, PipelineFile, Step
from healthytoon.cicd.loader import RawDocument, load_yaml
from healthytoon.core.discovery import discover_files
from healthytoon.core.types import Location

__all__ = ["PLATFORM", "collect_artifacts", "parse_github_workflow"]

#: Platform identifier stored on every object this adapter emits.
PLATFORM = "github"

#: Matrix keys whose values name the Python versions a job runs under.
_PYTHON_MATRIX_KEYS = ("python-version", "python_version", "python")

#: Matrix sections that are lists of mappings, not version dimensions.
_NON_DIMENSION_MATRIX_KEYS = frozenset({"include", "exclude"})

#: Lockfiles recognised at any depth in the repository.
_LOCKFILE_NAMES = frozenset({"uv.lock", "poetry.lock", "Pipfile.lock"})

#: Project manifests that mark a package root.
_PROJECT_FILE_NAMES = frozenset({"pyproject.toml", "setup.py"})

#: Directories pruned while collecting artifacts (mirrors ROADMAP decision 7).
_ARTIFACT_EXCLUDES: tuple[str, ...] = (
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "build",
    "dist",
    "*.egg-info",
    "site-packages",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
)

_URL_RE = re.compile(r"^\s*url\s*=\s*(\S+)\s*$", re.MULTILINE)


def _relative_posix(path: Path, root: Path) -> str:
    """Return ``path`` relative to ``root`` as a POSIX path, best effort."""

    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _escape_pointer(token: str) -> str:
    """Escape one JSON-pointer reference token (RFC 6901)."""

    return token.replace("~", "~0").replace("/", "~1")


def _opt_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _opt_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


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


def _as_str_tuple(value: object) -> tuple[str, ...]:
    """Normalize a scalar or sequence into a tuple of strings."""

    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        rendered = (_scalar_str(item) for item in value)
        return tuple(item for item in rendered if item is not None)
    single = _scalar_str(value)
    return (single,) if single is not None else ()


def _as_mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): item for key, item in value.items()})
    return MappingProxyType({})


def _as_mapping_or_none(value: object) -> Mapping[str, object] | None:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): item for key, item in value.items()})
    return None


def _flatten_matrix(value: object) -> Mapping[str, tuple[str, ...]]:
    """Flatten ``strategy.matrix`` into dimension -> value tuple.

    ``include``/``exclude`` are lists of mappings rather than version
    dimensions and are intentionally dropped.
    """

    if not isinstance(value, Mapping):
        return MappingProxyType({})
    flattened: dict[str, tuple[str, ...]] = {}
    for key, item in value.items():
        if not isinstance(key, str) or key in _NON_DIMENSION_MATRIX_KEYS:
            continue
        flattened[key] = _as_str_tuple(item)
    return MappingProxyType(flattened)


def _concurrency(value: object) -> str | Mapping[str, object] | None:
    if isinstance(value, str):
        return value
    return _as_mapping_or_none(value)


def _normalize_triggers(value: object) -> tuple[str, ...]:
    """Normalize ``on:`` into an ordered, de-duplicated tuple of names."""

    names: list[str] = []
    if isinstance(value, str):
        names.append(value)
    elif isinstance(value, Mapping):
        names.extend(str(key) for key in value if isinstance(key, str))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        names.extend(item for item in value if isinstance(item, str))
    seen: dict[str, None] = {}
    for name in names:
        seen.setdefault(name, None)
    return tuple(seen)


def _on_value(data: Mapping[str, object]) -> object:
    """Return the raw ``on:`` value.

    YAML 1.2 keeps the key as the string ``"on"``; the identity check on
    ``True`` is a defensive fallback for a YAML 1.1 resolver.
    """

    for raw_key, value in data.items():
        key: object = raw_key
        if key == "on" or key is True:
            return value
    return None


def _build_action_ref(uses: str, file: str) -> ActionRef:
    """Build an :class:`ActionRef` from a ``uses:`` string."""

    raw = uses.strip()
    is_docker = raw.startswith("docker://")
    is_local = raw.startswith(("./", "../"))
    is_expression = "${{" in raw
    owner: str | None = None
    repo: str | None = None
    ref: str | None = None
    if not (is_docker or is_local or is_expression):
        target, _, ref_part = raw.partition("@")
        ref = ref_part or None
        parts = [part for part in target.split("/") if part]
        if len(parts) >= 2:
            owner, repo = parts[0], parts[1]
    return ActionRef(
        raw=raw,
        owner=owner,
        repo=repo,
        ref=ref,
        is_local=is_local,
        is_docker=is_docker,
        is_expression=is_expression,
        location=Location(file=file, line=None),
    )


def _parse_steps(value: object, job_pointer: str, file: str) -> tuple[Step, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    steps: list[Step] = []
    for index, item in enumerate(value):
        pointer = f"{job_pointer}/steps/{index}"
        if not isinstance(item, Mapping):
            steps.append(Step(pointer=pointer))
            continue
        uses = _opt_str(item.get("uses"))
        action = _build_action_ref(uses, file) if uses is not None else None
        steps.append(
            Step(
                pointer=pointer,
                name=_opt_str(item.get("name")),
                run=_opt_str(item.get("run")),
                uses=uses,
                action=action,
                with_=_as_mapping(item.get("with")),
                env=_as_mapping(item.get("env")),
                if_=_opt_str(item.get("if")),
                timeout_minutes=_opt_int(item.get("timeout-minutes")),
                continue_on_error=item.get("continue-on-error") is True,
            )
        )
    return tuple(steps)


def _python_versions_from(
    matrix: Mapping[str, tuple[str, ...]], steps: tuple[Step, ...]
) -> tuple[str, ...]:
    """Return Python versions from the matrix, else from ``setup-python``."""

    for key in _PYTHON_MATRIX_KEYS:
        versions = matrix.get(key)
        if versions:
            return versions
    for step in steps:
        action = step.action
        if action is None or action.owner != "actions" or action.repo != "setup-python":
            continue
        versions = _as_str_tuple(step.with_.get("python-version"))
        if versions:
            return versions
    return ()


def _parse_job(job_id: str, raw: object, file: str, triggers: tuple[str, ...]) -> Job:
    pointer = f"/jobs/{_escape_pointer(job_id)}"
    if not isinstance(raw, Mapping):
        return Job(id=job_id, platform=PLATFORM, pointer=pointer, triggers=triggers)

    steps = _parse_steps(raw.get("steps"), pointer, file)
    matrix = _flatten_matrix(_as_mapping(raw.get("strategy")).get("matrix"))
    uses_value = _opt_str(raw.get("uses"))
    uses = (_build_action_ref(uses_value, file),) if uses_value is not None else ()
    return Job(
        id=job_id,
        platform=PLATFORM,
        pointer=pointer,
        name=_opt_str(raw.get("name")),
        runs_on=_as_str_tuple(raw.get("runs-on")),
        steps=steps,
        matrix=matrix,
        python_versions=_python_versions_from(matrix, steps),
        triggers=triggers,
        permissions=_as_mapping_or_none(raw.get("permissions")),
        env=_as_mapping(raw.get("env")),
        uses=uses,
        is_reusable=bool(uses),
        timeout_minutes=_opt_int(raw.get("timeout-minutes")),
    )


def parse_github_workflow(doc: RawDocument, root: Path) -> PipelineFile:
    """Parse one loaded workflow document into a :class:`PipelineFile`.

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
            parse_error="workflow root is not a mapping",
            raw_text=doc.raw_text,
        )

    triggers = _normalize_triggers(_on_value(data))
    jobs: list[Job] = []
    jobs_raw = data.get("jobs")
    if isinstance(jobs_raw, Mapping):
        for job_id in sorted(key for key in jobs_raw if isinstance(key, str)):
            jobs.append(_parse_job(job_id, jobs_raw[job_id], path, triggers))
    return PipelineFile(
        path=path,
        platform=PLATFORM,
        jobs=tuple(jobs),
        triggers=triggers,
        permissions=_as_mapping_or_none(data.get("permissions")),
        concurrency=_concurrency(data.get("concurrency")),
        env=_as_mapping(data.get("env")),
        is_reusable="workflow_call" in triggers,
        parse_error=None,
        raw_text=doc.raw_text,
    )


def _first(rels: Sequence[str], predicate: Callable[[str], bool]) -> str | None:
    for rel in rels:
        if predicate(rel):
            return rel
    return None


def _is_workflow_file(rel: str) -> bool:
    parts = rel.split("/")
    return (
        len(parts) == 3
        and parts[0] == ".github"
        and parts[1] == "workflows"
        and parts[2].endswith((".yml", ".yaml"))
    )


def _is_dependabot(rel: str) -> bool:
    return rel in {".github/dependabot.yml", ".github/dependabot.yaml"}


def _is_renovate(rel: str) -> bool:
    name = Path(rel).name
    return name == "renovate.json" or name.startswith(".renovaterc")


def _is_pre_commit(rel: str) -> bool:
    return Path(rel).name == ".pre-commit-config.yaml"


def _is_lockfile(rel: str) -> bool:
    name = Path(rel).name
    if name in _LOCKFILE_NAMES:
        return True
    return name.startswith("requirements") and name.endswith(".txt")


def _is_python_version_file(rel: str) -> bool:
    return Path(rel).name == ".python-version"


def _package_roots(project_rels: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted({Path(rel).parent.as_posix() for rel in project_rels}))


def _read_pyproject(path: Path) -> tuple[Mapping[str, object] | None, str | None]:
    """Parse a ``pyproject.toml``; return its mapping and ``requires-python``."""

    try:
        raw_bytes = path.read_bytes()
    except OSError:
        return None, None
    try:
        data = tomllib.loads(raw_bytes.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        return None, None
    requires_python: str | None = None
    project = data.get("project")
    if isinstance(project, Mapping):
        value = project.get("requires-python")
        if isinstance(value, str):
            requires_python = value
    return data, requires_python


def _host_of(url: str) -> str | None:
    if "://" in url:
        hostname = urlsplit(url).hostname or ""
    elif "@" in url and ":" in url:
        hostname = url.rsplit("@", 1)[1].split(":", 1)[0]
    else:
        return None
    hostname = hostname.lower()
    if "github" in hostname:
        return "github"
    if "gitlab" in hostname:
        return "gitlab"
    return "unknown"


def _detect_vcs_host(root: Path) -> str:
    try:
        text = (root / ".git" / "config").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "unknown"
    hosts = {_host_of(match.group(1)) for match in _URL_RE.finditer(text)}
    for known in ("github", "gitlab"):
        if known in hosts:
            return known
    return "unknown"


def collect_artifacts(root: Path) -> CIArtifacts:
    """Discover CI/CD artifacts under ``root`` in deterministic order."""

    root_path = Path(root)
    discovered = discover_files(root_path, exclude=_ARTIFACT_EXCLUDES)
    by_rel: dict[str, Path] = {}
    for path in discovered:
        by_rel[_relative_posix(path, root_path)] = path
    rels = sorted(by_rel)

    workflow_rels = [rel for rel in rels if _is_workflow_file(rel)]
    pipelines = tuple(
        parse_github_workflow(load_yaml(by_rel[rel]), root_path) for rel in workflow_rels
    )

    project_rels = [rel for rel in rels if Path(rel).name in _PROJECT_FILE_NAMES]
    roots = _package_roots(project_rels)

    pyproject_rel: str | None
    if "pyproject.toml" in by_rel:
        pyproject_rel = "pyproject.toml"
    else:
        pyproject_rel = _first(
            [rel for rel in rels if Path(rel).name == "pyproject.toml"], lambda _rel: True
        )
    pyproject: Mapping[str, object] | None = None
    requires_python: str | None = None
    if pyproject_rel is not None:
        pyproject, requires_python = _read_pyproject(by_rel[pyproject_rel])

    return CIArtifacts(
        pipelines=pipelines,
        dependabot=_first(rels, _is_dependabot),
        renovate=_first(rels, _is_renovate),
        pre_commit=_first(rels, _is_pre_commit),
        lockfiles=tuple(rel for rel in rels if _is_lockfile(rel)),
        python_version_file=_first(rels, _is_python_version_file),
        pyproject_requires_python=requires_python,
        pyproject=pyproject,
        is_monorepo=len(roots) > 1,
        package_roots=roots,
        vcs_host=_detect_vcs_host(root_path),
    )
