# Healthytoon

Audit Python repositories for maintainability and CI/CD health.

## Status

Healthytoon is a pre-1.0 foundation (version 0.1.0). Implemented so far:

- **Phase 0 — Foundation:** configuration loading, CLI skeleton (`check`, `list`, `version`), exit-code contract, repository discovery.
- **Phase 1 — Reporting spine:** check runner and registry, plus terminal, JSON, SARIF 2.1.0, and Markdown reporters.
- **Phase 2a T2a.1a — AST structural checks:** `maintainability.function_length` (HT-MAINT-001), `maintainability.nesting_depth` (HT-MAINT-002), `maintainability.module_length` (HT-MAINT-003), `typing.annotation_coverage` (HT-TYPE-001), `documentation.docstring_coverage` (HT-DOC-001).

**Not implemented yet:** the CI/CD engine (Phase 2b), complexity analysis via optional Radon (T2a.2), dead-code detection via Vulture (T2a.3), import-cycle and duplication checks (T2a.1b), baselines and suppression, and the `init` and `explain` commands. `check` runs only the five checks above.

## Install

Requires Python 3.12+. `uv sync` creates a `.venv` providing the `healthytoon` console script; invoke it via `uv run healthytoon ...` or activate `.venv` and call `healthytoon` directly.

```bash
uv sync
uv run healthytoon --help
```

## Quick start

```bash
uv run healthytoon version    # tool + interpreter version
uv run healthytoon list       # the registered checks
uv run healthytoon check .    # audit the current directory
```

`list` prints one line per registered check: id, category, default severity (for example `maintainability.function_length  maintainability  medium`).

`check` prints a grade line and, when there are findings, a `<N> findings: …` summary followed by one line per finding. A clean run prints `No findings.` instead. When a check is skipped or errored, an additional `checks: <ran> ran, <skipped> skipped, <errored> errored` line is printed and each errored check is listed as `error: <check id>: <message>`. The command exits 0 unless a finding is at or above the `--fail-on` threshold (default `high`), or a check errored (exit 5); exact scores and counts depend on the repository audited.

```
healthytoon <version> — grade <A-F> (<0-100>/100)
No findings.
# or, when issues exist:
healthytoon <version> — grade <A-F> (<0-100>/100)
<N> findings: <severity counts>
<path>:<line> <SEVERITY> <HT-RULE-ID> <message>
```

## Commands

### `healthytoon check [PATH]`

Audit a target directory (default: `.`). Exactly one target is accepted; passing additional positional arguments is a usage error (exit 2).

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

Print one line per registered check (id, category, default severity). `--category` restricts output to a single category.

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

Example `pyproject.toml` (the same keys go at the top level of a standalone `healthytoon.toml`, without the `[tool.healthytoon]` table):

```toml
[tool.healthytoon]
include = ["src/**"]
exclude = ["tests/**"]
fail_on = "high"

[tool.healthytoon.output]
format = "terminal"
color = "auto"
output = "-"

[tool.healthytoon.rules."maintainability.function_length"]
enabled = true
severity = "medium"
options = { max_length = 50 }
```

- `include` — path globs to scan (default: `["**"]`, i.e. the whole repository).
- `exclude` — path globs to skip. Defaults exclude VCS, virtualenv, build, and cache directories (`.git`, `.hg`, `.svn`, `.venv`, `venv`, `.tox`, `.nox`, `.eggs`, `__pycache__`, `build`, `dist`, `site-packages`, `*.egg-info`, `node_modules`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`, `htmlcov`). CLI `--exclude` values are added to these.
- `respect_gitignore` — honor `.gitignore` during discovery (default: `true`).
- `fail_on` — default gate severity (default: `high`). `baseline` — reserved, accepted and stored but not applied yet.
- `jobs` — maximum parallel workers; currently accepted but checks run sequentially.
- `output` — table with `format` (`terminal` | `json` | `sarif` | `markdown`), `color` (`auto` | `always` | `never`), and `output` (`-` or a file path).
- `rules."<check-id>"` — per-check settings keyed by the **check id** (for example `maintainability.function_length`), **not** by the `HT-*` rule id. An `HT-*` key is accepted but is a silent no-op: it neither selects nor configures a check. Each entry supports `enabled` (bool), `severity` (overrides the check's default severity), and `options` (table).

Per-check `options` (defaults in parentheses): `maintainability.function_length` `max_length` (50); `maintainability.nesting_depth` `max_depth` (5), `count_comprehensions` (false), `count_lambdas` (false); `maintainability.module_length` `max_lines` (1000); `typing.annotation_coverage` `min_ratio` (0.8); `documentation.docstring_coverage` `min_ratio` (0.8).

## Severity and scoring

Five severities, ordered from least to most severe: `info`, `low`, `medium`, `high`, `critical`. A finding fails the gate when its severity is at or above `--fail-on`. Reports also include an overall score (0–100) with a letter grade (A–F), aggregated per category; the score is **informational only** and never fails the command on its own. The exit status depends on findings versus the `--fail-on` gate (plus the internal-error case above).

## Documentation

This README documents implemented functionality only. Planned work (the CI/CD engine, Radon complexity analysis, Vulture dead-code detection, import-cycle and duplication checks, baselines and suppression, and `init`/`explain`) is listed in `ROADMAP.md`.
