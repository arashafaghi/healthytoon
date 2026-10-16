# Healthytoon

Audit Python repositories for maintainability and CI/CD health.

## Status

Healthytoon is a pre-1.0 tool (version 0.1.0). Phases 0–3 are implemented: the CLI and reporting spine, the maintainability engine, the CI/CD engine, and hardening (baselines, `init`, `explain`, and self-CI). The tool ships **22 built-in checks**.

Code-quality checks (9: 7 maintainability, 1 typing, 1 documentation), grouped by category and default severity:

- maintainability, medium: `maintainability.function_length` (HT-MAINT-001), `maintainability.nesting_depth` (HT-MAINT-002), `maintainability.import_cycles` (HT-MAINT-004), `maintainability.complexity` (HT-MAINT-006)
- maintainability, low: `maintainability.module_length` (HT-MAINT-003), `maintainability.duplication` (HT-MAINT-005), `maintainability.dead_code` (HT-MAINT-007)
- typing, low: `typing.annotation_coverage` (HT-TYPE-001)
- documentation, low: `documentation.docstring_coverage` (HT-DOC-001)

CI/CD checks (13), covering GitHub Actions, GitLab CI, and CircleCI pipelines plus Jenkins presence detection:

- ci_cd, high: `ci.workflow_present` (HT-CI-001), `ci.workflow_parse` (HT-CI-002), `ci.tests_present` (HT-CI-003), `ci.actions_pinned` (HT-CI-004), `ci.python_eol` (HT-CI-006)
- ci_cd, medium: `ci.permissions_explicit` (HT-CI-005), `ci.python_matrix` (HT-CI-007), `ci.python_consistency` (HT-CI-008), `ci.coverage_gate` (HT-CI-009), `ci.lockfile` (HT-CI-010), `ci.triggers_pull_request` (HT-CI-012)
- ci_cd, low: `ci.dependency_updates` (HT-CI-011)
- security, critical: `ci.secrets` (HT-SEC-001)

`maintainability.complexity` (Radon) and `maintainability.dead_code` (Vulture) are **optional** backends. When the backend is unavailable the check is reported as skipped rather than passing silently: complexity analysis is skipped when Radon is not importable, and dead-code analysis is skipped when the `vulture` executable is not on `PATH`. `ci.secrets` scans CI YAML for hardcoded secrets and never reports the matched value.

Deferred for v1: HTML reporting, Docker-specific analysis, online branch-protection checks, Jenkins analysis beyond presence detection, and an internal McCabe fallback when Radon is absent.

## Install

Requires Python 3.12+. `uv sync` creates a `.venv` providing the `healthytoon` console script; invoke it via `uv run healthytoon ...` or activate `.venv` and call `healthytoon` directly.

```bash
uv sync
uv run healthytoon --help
```

The optional Radon complexity backend ships in the `analysis` extra: `uv sync --extra analysis`. Vulture is an external executable, not a Python dependency; install it separately to enable `maintainability.dead_code`.

## Quick start

```bash
uv run healthytoon version              # tool + interpreter version
uv run healthytoon list                 # the registered checks
uv run healthytoon init .               # write a starter healthytoon.toml
uv run healthytoon explain ci.lockfile  # describe a single check
uv run healthytoon check .              # audit the current directory
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
| `--baseline PATH` | Suppress findings whose fingerprint is recorded in this baseline file. Overrides a configured `baseline`; a missing file is a configuration error. |
| `--write-baseline PATH` | Write the current findings' fingerprints to `PATH` as a baseline, print a one-line confirmation, and exit 0 instead of rendering a report or evaluating the gate. |
| `--config PATH` | Load this configuration file instead of discovering one. |
| `--jobs N` | Maximum parallel workers (overrides configured `jobs`). Checks currently run sequentially; parallelism arrives in a later phase. |
| `--context N` | Attach `N` lines of source context around each source-code finding (default: `0` = off). CI/CD and security findings are never shown with context. |
| `--color {auto,always,never}` | Color output mode (default: `auto`). |
| `-q, --quiet` | Suppress the human-readable terminal report (other formats still print). |
| `-v, --verbose` | Increase verbosity; unexpected errors then print a traceback. |
| `--no-progress` | Accepted, but progress reporting is not implemented yet (no-op). |

### `healthytoon init [PATH] [--force]`

Write a documented starter `healthytoon.toml` inside `PATH` (default: `.`). It refuses to overwrite an existing file unless `--force` is passed. A `PATH` that exists but is not a directory, and an existing config without `--force`, are both usage errors (exit 2).

### `healthytoon explain CHECK_ID`

Print one check's id, category, default severity, and a one-line summary. `CHECK_ID` is the check id shown by `healthytoon list` (for example `ci.lockfile` or `maintainability.function_length`), not the `HT-*` rule id. An unknown id is a usage error (exit 2).

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
- `fail_on` — default gate severity (default: `high`). `baseline` — path to a baseline file; findings whose fingerprint it records are suppressed before the gate is evaluated. A missing configured file is ignored (a missing `--baseline` file is an error).
- `jobs` — maximum parallel workers; currently accepted but checks run sequentially.
- `output` — table with `format` (`terminal` | `json` | `sarif` | `markdown`), `color` (`auto` | `always` | `never`), `output` (`-` or a file path), and `context` (integer ≥ 0; when > 0, source-code findings include that many surrounding lines, and each finding's excerpt is shown by the terminal/Markdown reporters and under `properties.context` in SARIF).
- `rules."<check-id>"` — per-check settings keyed by the **check id** (for example `maintainability.function_length`), **not** by the `HT-*` rule id. An `HT-*` key is accepted but is a silent no-op: it neither selects nor configures a check. Each entry supports `enabled` (bool), `severity` (overrides the check's default severity), and `options` (table).

There are **22** built-in rules. Every rule is keyed by its **check id** (not its `HT-*` rule id).

| Check id | Rule id | Category | Default severity | Options (default) |
| --- | --- | --- | --- | --- |
| `maintainability.function_length` | `HT-MAINT-001` | maintainability | medium | `max_length` (50) |
| `maintainability.nesting_depth` | `HT-MAINT-002` | maintainability | medium | `max_depth` (5), `count_comprehensions` (false), `count_lambdas` (false) |
| `maintainability.module_length` | `HT-MAINT-003` | maintainability | low | `max_lines` (1000) |
| `maintainability.import_cycles` | `HT-MAINT-004` | maintainability | medium | — |
| `maintainability.duplication` | `HT-MAINT-005` | maintainability | low | `min_tokens` (50), `min_occurrences` (2), `max_findings` (50) |
| `maintainability.complexity` | `HT-MAINT-006` | maintainability | medium | `max_cyclomatic` (10) |
| `maintainability.dead_code` | `HT-MAINT-007` | maintainability | low | `min_confidence` (60), `whitelist` ([]) |
| `typing.annotation_coverage` | `HT-TYPE-001` | typing | low | `min_ratio` (0.8) |
| `documentation.docstring_coverage` | `HT-DOC-001` | documentation | low | `min_ratio` (0.8) |
| `ci.workflow_present` | `HT-CI-001` | ci_cd | high | — |
| `ci.workflow_parse` | `HT-CI-002` | ci_cd | high | — |
| `ci.tests_present` | `HT-CI-003` | ci_cd | high | — |
| `ci.actions_pinned` | `HT-CI-004` | ci_cd | high | — |
| `ci.permissions_explicit` | `HT-CI-005` | ci_cd | medium | — |
| `ci.python_eol` | `HT-CI-006` | ci_cd | high | — |
| `ci.python_matrix` | `HT-CI-007` | ci_cd | medium | — |
| `ci.python_consistency` | `HT-CI-008` | ci_cd | medium | — |
| `ci.coverage_gate` | `HT-CI-009` | ci_cd | medium | — |
| `ci.lockfile` | `HT-CI-010` | ci_cd | medium | — |
| `ci.dependency_updates` | `HT-CI-011` | ci_cd | low | — |
| `ci.triggers_pull_request` | `HT-CI-012` | ci_cd | medium | — |
| `ci.secrets` | `HT-SEC-001` | security | critical | — |

Copy-paste configuration for every rule at its defaults; delete the tables you do not need, and remember that changing a rule's `severity` moves its findings, which can change the gate result.

```toml
[tool.healthytoon.rules."maintainability.function_length"]
enabled = true
severity = "medium"
options = { max_length = 50 }

[tool.healthytoon.rules."maintainability.nesting_depth"]
enabled = true
severity = "medium"
options = { max_depth = 5, count_comprehensions = false, count_lambdas = false }

[tool.healthytoon.rules."maintainability.module_length"]
enabled = true
severity = "low"
options = { max_lines = 1000 }

[tool.healthytoon.rules."maintainability.import_cycles"]
enabled = true
severity = "medium"

[tool.healthytoon.rules."maintainability.duplication"]
enabled = true
severity = "low"
options = { min_tokens = 50, min_occurrences = 2, max_findings = 50 }

[tool.healthytoon.rules."maintainability.complexity"]
enabled = true
severity = "medium"
options = { max_cyclomatic = 10 }

[tool.healthytoon.rules."maintainability.dead_code"]
enabled = true
severity = "low"
options = { min_confidence = 60, whitelist = [] }

[tool.healthytoon.rules."typing.annotation_coverage"]
enabled = true
severity = "low"
options = { min_ratio = 0.8 }

[tool.healthytoon.rules."documentation.docstring_coverage"]
enabled = true
severity = "low"
options = { min_ratio = 0.8 }

[tool.healthytoon.rules."ci.workflow_present"]
enabled = true
severity = "high"

[tool.healthytoon.rules."ci.workflow_parse"]
enabled = true
severity = "high"

[tool.healthytoon.rules."ci.tests_present"]
enabled = true
severity = "high"

[tool.healthytoon.rules."ci.actions_pinned"]
enabled = true
severity = "high"

[tool.healthytoon.rules."ci.permissions_explicit"]
enabled = true
severity = "medium"

[tool.healthytoon.rules."ci.python_eol"]
enabled = true
severity = "high"

[tool.healthytoon.rules."ci.python_matrix"]
enabled = true
severity = "medium"

[tool.healthytoon.rules."ci.python_consistency"]
enabled = true
severity = "medium"

[tool.healthytoon.rules."ci.coverage_gate"]
enabled = true
severity = "medium"

[tool.healthytoon.rules."ci.lockfile"]
enabled = true
severity = "medium"

[tool.healthytoon.rules."ci.dependency_updates"]
enabled = true
severity = "low"

[tool.healthytoon.rules."ci.triggers_pull_request"]
enabled = true
severity = "medium"

[tool.healthytoon.rules."ci.secrets"]
enabled = true
severity = "critical"
```

## Severity and scoring

Five severities, ordered from least to most severe: `info`, `low`, `medium`, `high`, `critical`. A finding fails the gate when its severity is at or above `--fail-on`. Reports also include an overall score (0–100) with a letter grade (A–F), aggregated per category; the score is **informational only** and never fails the command on its own. The exit status depends on findings versus the `--fail-on` gate (plus the internal-error case above).

## Self-CI

Healthytoon dogfoods itself. `.github/workflows/ci.yml` runs on every push to `master`/`main` and every pull request: Ruff lint and `ruff format --check`, strict mypy over `src`, and pytest with `--cov-fail-under=90`. The pytest job runs on Python 3.12 and 3.13; a separate build job builds the wheel and installs it into a clean virtual environment as a smoke test.

## Documentation

This README documents implemented functionality only. Deferred work (HTML reporting, Docker-specific analysis, online branch-protection checks, Jenkins analysis beyond presence detection, and an internal McCabe fallback) is listed in `ROADMAP.md`.
