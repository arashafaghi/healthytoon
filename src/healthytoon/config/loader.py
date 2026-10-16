"""Layered configuration loader.

Discovers TOML configuration from the filesystem and merges it over built-in
defaults using a fixed precedence (lowest to highest):

    defaults
    < ``~/.config/healthytoon/config.toml``
    < ``[tool.healthytoon]`` in the nearest ``pyproject.toml``
    < ``healthytoon.toml`` next to it
    < an explicit file

Scalars and lists replace lower layers per key. ``output`` and each rule's
``options`` table are merged per key. An explicit file replaces the discovered
project layers entirely (it does not replace the user config or defaults).
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterable, Iterator
from pathlib import Path
from types import MappingProxyType

from healthytoon.config.defaults import DEFAULT_EXCLUDES, DEFAULT_INCLUDE
from healthytoon.config.models import Config, OutputConfig, RuleConfig
from healthytoon.core.types import Severity
from healthytoon.errors import ConfigError

__all__ = ["find_config_file", "load_config"]

_USER_CONFIG_PARTS = (".config", "healthytoon", "config.toml")
_STANDALONE_NAME = "healthytoon.toml"
_PYPROJECT_NAME = "pyproject.toml"
_TOOL_KEY = "healthytoon"

_OUTPUT_FORMATS = frozenset({"terminal", "json", "sarif", "markdown"})
_OUTPUT_COLORS = frozenset({"auto", "always", "never"})

_TOP_LEVEL_KEYS = frozenset(
    {
        "include",
        "exclude",
        "respect_gitignore",
        "fail_on",
        "baseline",
        "jobs",
        "output",
        "rules",
    }
)
_OUTPUT_KEYS = frozenset({"format", "color", "output"})
_RULE_KEYS = frozenset({"enabled", "severity", "options"})


def find_config_file(start: Path) -> Path | None:
    """Return the nearest config source at or above ``start``.

    A ``pyproject.toml`` only counts when it carries a ``[tool.healthytoon]``
    table. When both sources live in the same directory the standalone
    ``healthytoon.toml`` is preferred.
    """

    for directory in _iter_directories(start):
        standalone = directory / _STANDALONE_NAME
        if standalone.is_file():
            return standalone
        pyproject = directory / _PYPROJECT_NAME
        if pyproject.is_file() and _pyproject_table(pyproject) is not None:
            return pyproject
    return None


def load_config(*, start: Path, explicit: Path | None = None) -> Config:
    """Discover, parse, and merge every configuration layer into a :class:`Config`."""

    data = _default_data()
    source: str | None = None

    user_config = Path.home().joinpath(*_USER_CONFIG_PARTS)
    if user_config.is_file():
        _merge(data, _extract_layer(user_config))
        source = str(user_config)

    if explicit is not None:
        explicit_path = explicit.expanduser()
        if not explicit_path.is_file():
            raise ConfigError(f"{explicit_path}: configuration file not found")
        _merge(data, _extract_layer(explicit_path))
        source = str(explicit_path)
    else:
        for config_path in _discovered_layers(start):
            _merge(data, _extract_layer(config_path))
            source = str(config_path)

    return _build_config(data, source)


def _iter_directories(start: Path) -> Iterator[Path]:
    current = start.expanduser()
    if current.is_file() or not current.is_dir():
        current = current.parent
    current = current.resolve()
    while True:
        yield current
        parent = current.parent
        if parent == current:
            return
        current = parent


def _discovered_layers(start: Path) -> list[Path]:
    for directory in _iter_directories(start):
        layers: list[Path] = []
        pyproject = directory / _PYPROJECT_NAME
        if pyproject.is_file() and _pyproject_table(pyproject) is not None:
            layers.append(pyproject)
        standalone = directory / _STANDALONE_NAME
        if standalone.is_file():
            layers.append(standalone)
        if layers:
            return layers
    return []


def _load_toml(path: Path) -> dict[str, object]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read configuration: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: malformed TOML: {exc}") from exc


def _pyproject_table(path: Path) -> dict[str, object] | None:
    data = _load_toml(path)
    tool = data.get("tool")
    if tool is None:
        return None
    if not isinstance(tool, dict):
        raise ConfigError(f"{path}: [tool] must be a table")
    if _TOOL_KEY not in tool:
        return None
    table = tool[_TOOL_KEY]
    if not isinstance(table, dict):
        raise ConfigError(f"{path}: [tool.healthytoon] must be a table")
    return table


def _extract_layer(path: Path) -> dict[str, object]:
    if path.name == _PYPROJECT_NAME:
        table = _pyproject_table(path)
        if table is None:
            raise ConfigError(f"{path}: missing a [tool.healthytoon] table")
        return _normalize_layer(table, path)
    return _normalize_layer(_load_toml(path), path)


def _normalize_layer(raw: dict[str, object], path: Path) -> dict[str, object]:
    unknown = set(raw) - _TOP_LEVEL_KEYS
    if unknown:
        raise ConfigError(f"{path}: unknown configuration key(s): {_format_keys(unknown)}")

    layer: dict[str, object] = {}
    if "include" in raw:
        layer["include"] = _string_tuple(raw["include"], "include", path)
    if "exclude" in raw:
        layer["exclude"] = _string_tuple(raw["exclude"], "exclude", path)
    if "respect_gitignore" in raw:
        layer["respect_gitignore"] = _boolean(raw["respect_gitignore"], "respect_gitignore", path)
    if "fail_on" in raw:
        layer["fail_on"] = _severity(raw["fail_on"], "fail_on", path)
    if "baseline" in raw:
        layer["baseline"] = _optional_string(raw["baseline"], "baseline", path)
    if "jobs" in raw:
        layer["jobs"] = _jobs(raw["jobs"], path)
    if "output" in raw:
        layer["output"] = _output_layer(raw["output"], path)
    if "rules" in raw:
        layer["rules"] = _rules_layer(raw["rules"], path)
    return layer


def _string_tuple(value: object, key: str, path: Path) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        items: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ConfigError(f"{path}: {key} must be a list of strings")
            items.append(item)
        return tuple(items)
    raise ConfigError(f"{path}: {key} must be a list of strings")


def _boolean(value: object, key: str, path: Path) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{path}: {key} must be a boolean")
    return value


def _optional_string(value: object, key: str, path: Path) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"{path}: {key} must be a string or null")
    return value


def _severity(value: object, key: str, path: Path) -> Severity:
    try:
        return Severity.parse(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{path}: invalid {key}: {value!r} ({exc})") from exc


def _jobs(value: object, path: Path) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{path}: jobs must be an integer")
    if value < 0:
        raise ConfigError(f"{path}: jobs must be non-negative, got {value}")
    return value


def _output_choice(value: object, key: str, allowed: frozenset[str], path: Path) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ConfigError(f"{path}: unknown output.{key}: {value!r}")
    return value


def _output_layer(value: object, path: Path) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ConfigError(f"{path}: output must be a table")
    unknown = set(value) - _OUTPUT_KEYS
    if unknown:
        raise ConfigError(f"{path}: unknown output key(s): {_format_keys(unknown)}")

    layer: dict[str, object] = {}
    if "format" in value:
        layer["format"] = _output_choice(value["format"], "format", _OUTPUT_FORMATS, path)
    if "color" in value:
        layer["color"] = _output_choice(value["color"], "color", _OUTPUT_COLORS, path)
    if "output" in value:
        destination = value["output"]
        if not isinstance(destination, str):
            raise ConfigError(f"{path}: output.output must be a string")
        layer["output"] = destination
    return layer


def _rules_layer(value: object, path: Path) -> dict[str, dict[str, object]]:
    if not isinstance(value, dict):
        raise ConfigError(f"{path}: rules must be a table")

    layer: dict[str, dict[str, object]] = {}
    for rule_id, rule_value in value.items():
        if not isinstance(rule_value, dict):
            raise ConfigError(f"{path}: rules.{rule_id} must be a table")
        unknown = set(rule_value) - _RULE_KEYS
        if unknown:
            raise ConfigError(
                f"{path}: unknown key(s) for rule {rule_id!r}: {_format_keys(unknown)}"
            )

        entry: dict[str, object] = {}
        if "enabled" in rule_value:
            entry["enabled"] = _boolean(rule_value["enabled"], f"rules.{rule_id}.enabled", path)
        if "severity" in rule_value:
            entry["severity"] = _severity(rule_value["severity"], f"rules.{rule_id}.severity", path)
        if "options" in rule_value:
            options = rule_value["options"]
            if not isinstance(options, dict):
                raise ConfigError(f"{path}: rules.{rule_id}.options must be a table")
            entry["options"] = dict(options)
        layer[rule_id] = entry
    return layer


def _default_data() -> dict[str, object]:
    return {
        "include": DEFAULT_INCLUDE,
        "exclude": DEFAULT_EXCLUDES,
        "respect_gitignore": True,
        "fail_on": Severity.HIGH,
        "baseline": None,
        "jobs": 0,
        "output": {"format": "terminal", "color": "auto", "output": "-"},
        "rules": {},
    }


def _merge(base: dict[str, object], layer: dict[str, object]) -> None:
    for key, value in layer.items():
        if key == "output" and isinstance(value, dict):
            _merge_output(base, value)
        elif key == "rules" and isinstance(value, dict):
            _merge_rules(base, value)
        else:
            base[key] = value


def _merge_output(base: dict[str, object], value: dict[str, object]) -> None:
    output = base.get("output")
    if not isinstance(output, dict):
        output = {}
        base["output"] = output
    output.update(value)


def _merge_rules(base: dict[str, object], value: dict[str, object]) -> None:
    rules = base.get("rules")
    if not isinstance(rules, dict):
        rules = {}
        base["rules"] = rules
    for rule_id, rule_value in value.items():
        existing = rules.get(rule_id)
        if isinstance(existing, dict) and isinstance(rule_value, dict):
            rules[rule_id] = _merge_rule(existing, rule_value)
        elif isinstance(rule_value, dict):
            rules[rule_id] = dict(rule_value)


def _merge_rule(existing: dict[str, object], rule_value: dict[str, object]) -> dict[str, object]:
    merged = dict(existing)
    for rule_key, rule_item in rule_value.items():
        current = merged.get(rule_key)
        if rule_key == "options" and isinstance(current, dict) and isinstance(rule_item, dict):
            options = dict(current)
            options.update(rule_item)
            merged[rule_key] = options
        else:
            merged[rule_key] = rule_item
    return merged


def _build_rules(data: dict[str, object]) -> dict[str, RuleConfig]:
    rules_raw = data.get("rules")
    rules: dict[str, RuleConfig] = {}
    if not isinstance(rules_raw, dict):
        return rules
    for rule_id, rule_value in rules_raw.items():
        if not isinstance(rule_value, dict):
            continue
        options_raw = rule_value.get("options")
        options = dict(options_raw) if isinstance(options_raw, dict) else {}
        severity = rule_value.get("severity")
        rules[rule_id] = RuleConfig(
            enabled=rule_value.get("enabled", True) is not False,
            severity=severity if isinstance(severity, Severity) else None,
            options=MappingProxyType(options),
        )
    return rules


def _build_output(data: dict[str, object]) -> OutputConfig:
    output_raw = data.get("output")
    output_data = output_raw if isinstance(output_raw, dict) else {}
    return OutputConfig(
        format=str(output_data.get("format", "terminal")),
        color=str(output_data.get("color", "auto")),
        output=str(output_data.get("output", "-")),
    )


def _build_config(data: dict[str, object], source: str | None) -> Config:
    fail_on = data.get("fail_on")
    baseline = data.get("baseline")
    jobs = data.get("jobs")
    respect_gitignore = data.get("respect_gitignore")
    return Config(
        include=_coerce_tuple(data.get("include"), DEFAULT_INCLUDE),
        exclude=_coerce_tuple(data.get("exclude"), DEFAULT_EXCLUDES),
        respect_gitignore=respect_gitignore if isinstance(respect_gitignore, bool) else True,
        fail_on=fail_on if isinstance(fail_on, Severity) else Severity.HIGH,
        baseline=baseline if isinstance(baseline, str) else None,
        jobs=jobs if isinstance(jobs, int) and not isinstance(jobs, bool) else 0,
        output=_build_output(data),
        rules=MappingProxyType(_build_rules(data)),
        source=source,
    )


def _coerce_tuple(value: object, fallback: tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(value, tuple) and all(isinstance(item, str) for item in value):
        return value
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(value)
    return fallback


def _format_keys(keys: Iterable[str]) -> str:
    return ", ".join(repr(key) for key in sorted(keys))
