"""Bundled Python end-of-life data.

The EOL snapshot ships with the package and is never fetched at runtime, so
reports stay deterministic and offline. Refresh it by editing
``python_eol.json`` and bumping ``data_as_of``; the file's ``data_as_of`` is
surfaced in reports (ROADMAP decision 4).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import lru_cache
from importlib.resources import files

__all__ = ["eol_data_as_of", "load_eol"]

#: Resource path of the vendored snapshot, relative to the package root.
_EOL_RESOURCE = ("data", "python_eol.json")


@lru_cache(maxsize=1)
def _load_payload() -> dict[str, object]:
    """Read and cache the bundled JSON payload."""

    text = files("healthytoon").joinpath(*_EOL_RESOURCE).read_text(encoding="utf-8")
    payload: object = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("python_eol.json must contain a JSON object")
    return payload


def load_eol() -> Mapping[str, Mapping[str, str]]:
    """Return ``{"<major.minor>": {"release": <date>, "eol": <date>}}``."""

    payload = _load_payload()
    cycles = payload.get("cycles")
    if not isinstance(cycles, dict):
        raise ValueError("python_eol.json is missing a 'cycles' object")
    result: dict[str, Mapping[str, str]] = {}
    for cycle, info in cycles.items():
        if not isinstance(cycle, str) or not isinstance(info, dict):
            continue
        entry: dict[str, str] = {}
        for key, value in info.items():
            if isinstance(key, str) and isinstance(value, str):
                entry[key] = value
        result[cycle] = entry
    return result


def eol_data_as_of() -> str:
    """Return the ISO date the bundled EOL snapshot was captured."""

    payload = _load_payload()
    value = payload.get("data_as_of")
    if not isinstance(value, str):
        raise ValueError("python_eol.json is missing a 'data_as_of' string")
    return value
