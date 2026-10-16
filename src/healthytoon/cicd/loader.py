"""Tolerant YAML loader for CI configuration files.

CI configuration is untrusted input: it may be malformed, enormous, contain
duplicate keys, or use platform-specific tags such as GitLab's ``!reference``.
A single bad file must never abort an audit, so :func:`load_yaml` never raises:
it returns a :class:`RawDocument` whose ``error`` is set instead.

The loader uses ruamel's *safe* loader, which resolves ``on`` as the string
``"on"`` (YAML 1.2 rather than YAML 1.1's boolean ``on``). Unknown tags are
captured as :class:`RawTag` sentinels rather than raising.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from ruamel.yaml.nodes import MappingNode, ScalarNode, SequenceNode
from ruamel.yaml.resolver import VersionedResolver

__all__ = ["DEFAULT_MAX_BYTES", "RawDocument", "RawTag", "load_yaml"]

#: Default maximum file size: 5 MiB of source, enough for any sane CI file.
DEFAULT_MAX_BYTES = 5_242_880

#: Tag that resolves a scalar to a plain string.
_STRING_TAG = "tag:yaml.org,2002:str"

#: A dotted numeric scalar such as ``3.10`` or ``3.12.1``. YAML resolves these
#: as floats, which silently turns a Python version into ``3.1``; CI files use
#: them as opaque version labels, so they must stay strings.
_VERSION_RE = re.compile(r"^[-+]?[0-9][0-9_]*\.[0-9]+(?:\.[0-9]+)*$")

#: Characters a version scalar may start with; the resolver is keyed by first
#: character, so these buckets must be patched to win before the float rules.
_VERSION_FIRST_CHARS = "0123456789-+"


@dataclass(frozen=True)
class RawDocument:
    """Result of loading one YAML document, successful or not.

    Exactly one of ``data`` and ``error`` is set on a well-behaved load; a file
    that fails to load keeps its decoded ``raw_text`` so callers can still
    surface it.
    """

    path: str
    data: object | None
    raw_text: str
    error: str | None


@dataclass(frozen=True)
class RawTag:
    """Sentinel for a YAML node carrying an unknown tag.

    ``tag`` is the full tag as written (for example ``"!reference"``) and
    ``value`` is the recursively constructed node value (scalar, list, or dict).
    """

    tag: str
    value: object


def _construct_raw_tag(constructor: Any, tag_suffix: str, node: Any) -> RawTag:
    """Construct an unknown-tagged node into a :class:`RawTag` sentinel."""

    value: object
    if isinstance(node, ScalarNode):
        value = constructor.construct_scalar(node)
    elif isinstance(node, SequenceNode):
        value = constructor.construct_sequence(node, deep=True)
    elif isinstance(node, MappingNode):
        value = constructor.construct_mapping(node, deep=True)
    else:  # pragma: no cover - ruamel only emits the three node kinds above
        value = None
    return RawTag(tag=str(node.tag), value=value)


def _preserve_version_strings(yaml: YAML) -> None:
    """Resolve dotted numeric scalars as strings instead of floats.

    YAML's core schema treats ``3.10`` as the float ``3.1``, which corrupts
    version labels such as ``python-version``. A string resolver is prepended to
    the numeric first-character buckets so it matches before the built-in float
    rule; every other scalar (booleans, null, integers, quoted text) is left to
    the standard resolvers.
    """

    resolver = yaml.resolver
    if not isinstance(resolver, VersionedResolver):  # pragma: no cover - safe loader always is
        return
    versioned = resolver.versioned_resolver
    for first in _VERSION_FIRST_CHARS:
        versioned.setdefault(first, []).insert(0, (_STRING_TAG, _VERSION_RE))


def _make_yaml() -> YAML:
    """Return a safe YAML loader configured for tolerant CI parsing."""

    yaml = YAML(typ="safe")
    yaml.allow_duplicate_keys = False
    # Registering the empty prefix makes *every* undefined tag (``!reference``,
    # ``!!python/name:...``, and friends) fall through to the sentinel instead
    # of raising a ConstructorError. Exact-tag constructors still win, so
    # standard scalars/sequences/mappings are unaffected.
    yaml.constructor.add_multi_constructor("", _construct_raw_tag)
    _preserve_version_strings(yaml)
    return yaml


def _format_error(exc: Exception) -> str:
    """Return a concise one-line message, including the line when available."""

    if isinstance(exc, YAMLError):
        mark = getattr(exc, "problem_mark", None)
        detail = getattr(exc, "problem", None) or str(exc)
        line = getattr(mark, "line", None)
        if isinstance(line, int):
            return f"{detail} (line {line + 1})"
        return str(detail)
    return str(exc)


def load_yaml(path: Path, *, max_bytes: int = DEFAULT_MAX_BYTES) -> RawDocument:
    """Load ``path`` as YAML without ever raising.

    Returns a :class:`RawDocument`; on any failure (unreadable file, oversized
    file, malformed YAML, or duplicate keys) ``data`` is ``None`` and ``error``
    describes the problem, including a line number when the loader provides one.
    """

    source = Path(path)
    try:
        size = source.stat().st_size
    except OSError as exc:
        return RawDocument(str(source), None, "", f"cannot read file: {exc}")
    if size > max_bytes:
        return RawDocument(
            str(source),
            None,
            "",
            f"file is too large: {size} bytes exceeds limit of {max_bytes} bytes",
        )

    try:
        raw_bytes = source.read_bytes()
    except OSError as exc:
        return RawDocument(str(source), None, "", f"cannot read file: {exc}")
    if len(raw_bytes) > max_bytes:
        return RawDocument(
            str(source),
            None,
            "",
            f"file is too large: {len(raw_bytes)} bytes exceeds limit of {max_bytes} bytes",
        )

    text = raw_bytes.decode("utf-8", errors="replace")
    try:
        data: object | None = _make_yaml().load(text)
    except Exception as exc:  # noqa: BLE001 - the tolerant loader must never raise
        return RawDocument(str(source), None, text, _format_error(exc))
    return RawDocument(str(source), data, text, None)
