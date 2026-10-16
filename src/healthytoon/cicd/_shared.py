"""Helpers shared verbatim by the CI/CD platform adapters.

The GitHub Actions, GitLab CI, and CircleCI adapters all lower untrusted YAML
into the shared IR with the same tolerant-parsing helpers. Those routines were
byte-for-byte (or, after the duplication check's string/number normalization,
token-for-token) identical copies in each module (HT-MAINT-005), so they live
here once and are imported by every adapter.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType

from healthytoon.cicd.ir import PipelineFile
from healthytoon.cicd.loader import RawDocument

__all__ = [
    "_as_mapping",
    "_as_str_tuple",
    "_escape_pointer",
    "_mapping_document",
    "_opt_str",
    "_pipeline_root",
    "_relative_posix",
    "_scalar_str",
]


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


def _pipeline_root(
    doc: RawDocument, root: Path, platform: str, root_error: str
) -> tuple[str, Mapping[str, object]] | PipelineFile:
    """Return ``(path, data)`` or the parse-error :class:`PipelineFile`.

    ``path`` is ``doc.path`` relative to ``root``. A document that failed to
    load keeps its own ``error`` as the pipeline's ``parse_error``; a document
    whose root is not a mapping uses ``root_error`` instead.
    """

    path = _relative_posix(Path(doc.path), root)
    if doc.error is not None:
        return PipelineFile(
            path=path, platform=platform, parse_error=doc.error, raw_text=doc.raw_text
        )
    data = doc.data
    if not isinstance(data, Mapping):
        return PipelineFile(
            path=path,
            platform=platform,
            parse_error=root_error,
            raw_text=doc.raw_text,
        )
    return path, data


def _mapping_document(doc: RawDocument, root: Path) -> tuple[str, Mapping[str, object]] | None:
    """Return ``(path, data)`` when ``doc`` is a usable mapping, else ``None``.

    Unlike :func:`_pipeline_root` this does not distinguish a load error from a
    non-mapping root: callers that treat both as "nothing to inspect" use it.
    """

    if doc.error is not None or not isinstance(doc.data, Mapping):
        return None
    return _relative_posix(Path(doc.path), root), doc.data
