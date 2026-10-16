"""Tests for the layered configuration loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from healthytoon.config.defaults import DEFAULT_EXCLUDES, DEFAULT_INCLUDE, default_config
from healthytoon.config.loader import find_config_file, load_config
from healthytoon.config.models import Config, OutputConfig
from healthytoon.core.types import Severity
from healthytoon.errors import ConfigError


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return home_dir


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_user_config(home: Path, text: str) -> Path:
    return write(home / ".config" / "healthytoon" / "config.toml", text)


def test_defaults_when_nothing_found(tmp_path: Path, home: Path) -> None:
    start = tmp_path / "project"
    start.mkdir()

    config = load_config(start=start)

    assert isinstance(config, Config)
    assert config.include == DEFAULT_INCLUDE
    assert config.exclude == DEFAULT_EXCLUDES
    assert config.fail_on is Severity.HIGH
    assert config.output == OutputConfig()
    assert config.rules == {}
    assert config.source is None


def test_default_config_factory_matches_model() -> None:
    assert default_config() == Config()


def test_pyproject_tool_table_is_parsed(tmp_path: Path, home: Path) -> None:
    start = tmp_path / "project"
    start.mkdir()
    pyproject = write(
        start / "pyproject.toml",
        """
[tool.healthytoon]
include = ["app"]
exclude = ["build", "*.gen"]
fail_on = "low"
jobs = 4
respect_gitignore = false
""",
    )

    config = load_config(start=start)

    assert config.include == ("app",)
    assert config.exclude == ("build", "*.gen")
    assert config.fail_on is Severity.LOW
    assert config.jobs == 4
    assert config.respect_gitignore is False
    assert config.source == str(pyproject)


def test_standalone_healthytoon_toml_is_parsed(tmp_path: Path, home: Path) -> None:
    start = tmp_path / "project"
    start.mkdir()
    standalone = write(
        start / "healthytoon.toml",
        """
include = ["lib"]
fail_on = "critical"
baseline = "baseline.json"
""",
    )

    config = load_config(start=start)

    assert config.include == ("lib",)
    assert config.fail_on is Severity.CRITICAL
    assert config.baseline == "baseline.json"
    assert config.source == str(standalone)


def test_standalone_layers_over_pyproject(tmp_path: Path, home: Path) -> None:
    start = tmp_path / "project"
    start.mkdir()
    write(start / "pyproject.toml", '[tool.healthytoon]\ninclude = ["from-pyproject"]\njobs = 2\n')
    standalone = write(start / "healthytoon.toml", 'include = ["from-standalone"]\n')

    config = load_config(start=start)

    assert config.include == ("from-standalone",)
    assert config.jobs == 2
    assert config.source == str(standalone)


def test_explicit_replaces_discovered_layers(tmp_path: Path, home: Path) -> None:
    start = tmp_path / "project"
    start.mkdir()
    write(
        start / "pyproject.toml",
        '[tool.healthytoon]\ninclude = ["from-pyproject"]\nfail_on = "low"\n',
    )
    explicit = write(tmp_path / "explicit.toml", 'include = ["from-explicit"]\n')

    config = load_config(start=start, explicit=explicit)

    assert config.include == ("from-explicit",)
    assert config.fail_on is Severity.HIGH
    assert config.source == str(explicit)


def test_explicit_layers_over_user_config(tmp_path: Path, home: Path) -> None:
    write_user_config(home, 'jobs = 4\nbaseline = "user-baseline.json"\n')
    explicit = write(tmp_path / "explicit.toml", 'include = ["explicit"]\n')

    config = load_config(start=tmp_path, explicit=explicit)

    assert config.include == ("explicit",)
    assert config.jobs == 4
    assert config.baseline == "user-baseline.json"


def test_user_config_layered_under_project(tmp_path: Path, home: Path) -> None:
    write_user_config(
        home,
        """
jobs = 3
fail_on = "low"
[output]
color = "never"
""",
    )
    start = tmp_path / "project"
    start.mkdir()
    pyproject = write(start / "pyproject.toml", "[tool.healthytoon]\njobs = 5\n")

    config = load_config(start=start)

    assert config.jobs == 5
    assert config.fail_on is Severity.LOW
    assert config.output.color == "never"
    assert config.source == str(pyproject)


def test_lists_replace_not_append(tmp_path: Path, home: Path) -> None:
    write_user_config(home, 'exclude = ["user-only"]\n')
    start = tmp_path / "project"
    start.mkdir()
    write(
        start / "pyproject.toml",
        '[tool.healthytoon]\ninclude = ["app"]\nexclude = ["vendor", "generated"]\n',
    )

    config = load_config(start=start)

    assert config.include == ("app",)
    assert config.exclude == ("vendor", "generated")
    assert DEFAULT_EXCLUDES[0] not in config.exclude


def test_rule_options_merge_per_key(tmp_path: Path, home: Path) -> None:
    write_user_config(
        home,
        """
[rules.mylint]
enabled = true
[rules.mylint.options]
alpha = 1
beta = 2
""",
    )
    start = tmp_path / "project"
    start.mkdir()
    write(
        start / "pyproject.toml",
        """
[tool.healthytoon.rules.mylint]
severity = "high"
[tool.healthytoon.rules.mylint.options]
beta = 3
gamma = 4
""",
    )

    config = load_config(start=start)

    rule = config.rules["mylint"]
    assert rule.enabled is True
    assert rule.severity is Severity.HIGH
    assert rule.options == {"alpha": 1, "beta": 3, "gamma": 4}


def test_unknown_rule_id_is_allowed(tmp_path: Path, home: Path) -> None:
    start = tmp_path / "project"
    start.mkdir()
    write(
        start / "healthytoon.toml",
        """
[rules."plugin.rule"]
enabled = false
[rules."plugin.rule".options]
threshold = 10
""",
    )

    config = load_config(start=start)

    assert config.rules["plugin.rule"].enabled is False
    assert config.rules["plugin.rule"].options == {"threshold": 10}


def test_malformed_toml_raises(tmp_path: Path, home: Path) -> None:
    start = tmp_path / "project"
    start.mkdir()
    write(start / "healthytoon.toml", "include = [")

    with pytest.raises(ConfigError):
        load_config(start=start)


@pytest.mark.parametrize("value", ['"nope"', "9"])
def test_invalid_fail_on_raises(tmp_path: Path, home: Path, value: str) -> None:
    start = tmp_path / "project"
    start.mkdir()
    write(start / "healthytoon.toml", f"fail_on = {value}\n")

    with pytest.raises(ConfigError):
        load_config(start=start)


def test_invalid_rule_severity_raises(tmp_path: Path, home: Path) -> None:
    start = tmp_path / "project"
    start.mkdir()
    write(start / "healthytoon.toml", '[rules.mylint]\nseverity = "bogus"\n')

    with pytest.raises(ConfigError):
        load_config(start=start)


@pytest.mark.parametrize(
    "body",
    [
        '[output]\nformat = "nope"\n',
        '[output]\ncolor = "rainbow"\n',
    ],
)
def test_invalid_output_values_raise(tmp_path: Path, home: Path, body: str) -> None:
    start = tmp_path / "project"
    start.mkdir()
    write(start / "healthytoon.toml", body)

    with pytest.raises(ConfigError):
        load_config(start=start)


def test_unknown_top_level_key_raises(tmp_path: Path, home: Path) -> None:
    start = tmp_path / "project"
    start.mkdir()
    write(start / "healthytoon.toml", "includee = ['src']\n")

    with pytest.raises(ConfigError):
        load_config(start=start)


def test_unknown_pyproject_key_raises(tmp_path: Path, home: Path) -> None:
    start = tmp_path / "project"
    start.mkdir()
    write(start / "pyproject.toml", "[tool.healthytoon]\nunknown_option = true\n")

    with pytest.raises(ConfigError):
        load_config(start=start)


def test_negative_jobs_raises(tmp_path: Path, home: Path) -> None:
    start = tmp_path / "project"
    start.mkdir()
    write(start / "healthytoon.toml", "jobs = -1\n")

    with pytest.raises(ConfigError):
        load_config(start=start)


def test_error_message_includes_source_path(tmp_path: Path, home: Path) -> None:
    start = tmp_path / "project"
    start.mkdir()
    standalone = write(start / "healthytoon.toml", "fail_on = 'nope'\n")

    with pytest.raises(ConfigError) as excinfo:
        load_config(start=start)

    assert str(standalone) in str(excinfo.value)


def test_find_config_file_returns_nearest(tmp_path: Path, home: Path) -> None:
    root = tmp_path / "repo"
    child = root / "pkg"
    child.mkdir(parents=True)
    pyproject = write(root / "pyproject.toml", '[tool.healthytoon]\ninclude = ["src"]\n')

    assert find_config_file(child) == pyproject

    standalone = write(root / "healthytoon.toml", 'include = ["src"]\n')
    assert find_config_file(child) == standalone

    nested = write(child / "healthytoon.toml", 'include = ["deep"]\n')
    assert find_config_file(child) == nested


def test_find_config_file_ignores_pyproject_without_table(tmp_path: Path, home: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    write(root / "pyproject.toml", "[project]\nname = 'demo'\n")

    assert find_config_file(root) is None


def test_find_config_file_from_file_path(tmp_path: Path, home: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    standalone = write(root / "healthytoon.toml", 'include = ["src"]\n')
    target = write(root / "module.py", "x = 1\n")

    assert find_config_file(target) == standalone


def test_output_context_is_parsed(tmp_path: Path, home: Path) -> None:
    write(tmp_path / "healthytoon.toml", "[output]\ncontext = 3\n")
    assert load_config(start=tmp_path).output.context == 3


@pytest.mark.parametrize("value", ["-1", "true", '"x"', "1.5"])
def test_output_context_invalid(tmp_path: Path, home: Path, value: str) -> None:
    write(tmp_path / "healthytoon.toml", f"[output]\ncontext = {value}\n")
    with pytest.raises(ConfigError):
        load_config(start=tmp_path)
