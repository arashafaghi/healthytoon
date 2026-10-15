# Healthytoon

Audit Python repositories for maintainability and CI/CD health.

## Status

Healthytoon is a pre-1.0 foundation (version 0.1.0). Implemented so far:

- **Phase 0 — Foundation:** configuration loading, CLI skeleton (`check`, `list`, `version`), exit-code contract, repository discovery.
- **Phase 1 — Reporting spine:** check runner and registry, plus terminal, JSON, SARIF 2.1.0, and Markdown reporters.

The audit checks themselves — the maintainability engine and the CI/CD engine — are **not implemented yet**. `healthytoon check` currently runs zero checks and reports a clean result (grade A, "No findings", exit code 0). A green run does not mean the repository has been verified; it means nothing has been checked. See `ROADMAP.md` for upcoming phases.

## Install

Requires Python 3.12+. `uv sync` creates a `.venv` providing the `healthytoon` console script; invoke it via `uv run healthytoon ...` or activate `.venv` and call `healthytoon` directly.

```bash
uv sync
uv run healthytoon --help
```

## Quick start

```bash
uv run healthytoon version    # tool + interpreter version
uv run healthytoon list       # registered checks (none yet)
uv run healthytoon check .    # audit the current directory
```

`check` today prints `healthytoon 0.1.0 — grade A (100.0/100)` and `No findings.`, exiting 0.

## Commands

### `healthytoon check [PATH]`

Audit a target directory (default: `.`; the first path argument is used).

| Option | Description |
| --- | --- |
| `--format {terminal,json,sarif,markdown}` | Report format (default: `terminal`). |
| `-o, --output PATH` | Write the report to `PATH`; `-` means stdout (default). |
| `--fail-on {critical,high,medium,low,info,none}` | Minimum severity that fails the gate (default: `high`). `none` disables the gate. |
| `--only PATTERN` | Run only checks whose id matches `PATTERN` (repeatable, glob). |
| `--skip PATTERN` | Skip checks whose id matches `PATTERN` (repeatable, glob). |
| `--exclude GLOB` | Exclude a path glob (repeatable; added on top of configured excludes). |
| `--config PATH` | Load this configuration file instead of discovering one. |
| `--jobs N` | Maximum parallel workers (overrides configured `jobs`). Checks currently run sequentially; parallelism arrives in a later phase. |
| `--color {auto,always,never}` | Color output mode (default: `auto`). |
| `-q, --quiet` | Suppress the human-readable terminal report (other formats still print). |
| `-v, --verbose` | Increase verbosity; unexpected errors then print a traceback. |
| `--no-progress` | Accepted, but progress reporting is not implemented yet (no-op). |

### `healthytoon list [--category CATEGORY]`

Print one line per registered check: id, category, default severity. With none registered it prints `No checks are registered yet.` and exits 0.

### `healthytoon version`

Print the Healthytoon version and the running Python version. `healthytoon --version` prints only the tool version.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | OK — no findings at or above the `--fail-on` threshold. |
| 1 | Findings at or above `--fail-on` (a finding exactly equal to the threshold fails). |
| 2 | Usage error (invalid arguments or unknown options). |
| 3 | Configuration error (missing, unreadable, malformed, or invalid config). |
| 4 | Target error (path missing or not a directory). |
| 5 | Internal error, including an **incomplete audit**: if a registered check crashes, the run exits 5 and can never produce a silent green result. |

Ctrl-C exits 130.

## Configuration

Configuration is TOML. Sources are merged in this precedence order (lowest to highest):

1. Built-in defaults.
2. User config: `~/.config/healthytoon/config.toml`.
3. `[tool.healthytoon]` in the nearest `pyproject.toml` (from the target directory upward).
4. A standalone `healthytoon.toml` in that directory (wins per key over the `pyproject.toml` table).
5. An explicit `--config PATH` file — replaces the discovered project layers; user config and defaults still apply.

Scalars and lists replace lower layers per key; the `output` table and each rule's `options` table are merged per key. Unknown keys are a configuration error (exit 3). Only a `pyproject.toml` carrying a `[tool.healthytoon]` table is considered.

Example `pyproject.toml` — the same keys go at the top level of a standalone `healthytoon.toml` (no `[tool.healthytoon]` table):

```toml
[tool.healthytoon]
include = ["src/**"]
exclude = ["tests/**", "build/**"]
fail_on = "high"
baseline = "healthytoon-baseline.json"  # reserved; not applied yet
jobs = 4

[tool.healthytoon.output]
format = "terminal"
color = "auto"
output = "-"

[tool.healthytoon.rules."<rule-id>"]
enabled = false
severity = "low"
options = { threshold = 10 }
```

- `include` — path globs to scan (default: `["**"]`, i.e. the whole repository).
- `respect_gitignore` — honor `.gitignore` during discovery (default: `true`).
- `exclude` — path globs to skip. Defaults exclude VCS, virtualenv, build, and cache directories (`.git`, `.hg`, `.svn`, `.venv`, `venv`, `.tox`, `.nox`, `.eggs`, `__pycache__`, `build`, `dist`, `site-packages`, `*.egg-info`, `node_modules`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`, `htmlcov`). CLI `--exclude` values are added to these.
- `fail_on` — default gate severity (default: `high`); `baseline` — reserved for future baseline support: accepted and stored, not applied yet.
- `jobs` — maximum parallel workers; currently accepted but checks run sequentially.
- `output` — table with `format` (`terminal` | `json` | `sarif` | `markdown`), `color` (`auto` | `always` | `never`), and `output` (`-` or a file path).
- `rules."<id>"` — per-rule settings: `enabled` (bool), `severity`, and `options` (table).

## Severity and scoring

Five severities, ordered from least to most severe: `info`, `low`, `medium`, `high`, `critical`. A finding fails the gate when its severity is at or above `--fail-on`. Reports also include an overall score (0–100) with a letter grade (A–F), aggregated per category; the score is **informational only** and never fails the command on its own. The exit status depends on findings versus the `--fail-on` gate (plus the internal-error case above).

## Documentation

This README is the user-facing documentation, limited to implemented functionality per the roadmap's completion criteria. Planned work (maintainability and CI/CD engines, baselines, `init` and `explain` commands) is listed in `ROADMAP.md`.
