# Developing Healthytoon

Contributor guide for Healthytoon itself; usage is in `README.md`, plans in
`ROADMAP.md`.

## Setup

Healthytoon requires Python 3.12+ and is developed with `uv`.

- `uv sync` creates `.venv` with runtime and dev dependencies (pytest, mypy,
  ruff, jsonschema, nox, radon, vulture).
- `uv sync --extra analysis` adds the optional Radon backend for
  `maintainability.complexity`; Radon is detected at runtime and complexity is
  reported as skipped when it is not importable.
- Vulture is an external executable (in the dev group, not a runtime
  dependency); `maintainability.dead_code` needs `vulture` on `PATH` and is
  skipped when absent.

```bash
uv sync
uv run healthytoon version
uv run healthytoon check . --fail-on none
```

## Architecture

Source lives under `src/healthytoon/`:

- `core/` — contracts and engine: `types.py` (Category, Severity, FileKind,
  Status, Finding, Report, fingerprints, ordering), `protocols.py` (`Check`,
  `Reporter`, `CheckResult`), `discovery.py`, `context.py`, `runner.py`,
  `registry.py`, `policy.py`, `scoring.py`, `baseline.py`.
- `config/` — `models.py`, `defaults.py`, `loader.py` (layered TOML).
- `cli/` — the Typer app in `app.py`, one module per command in `commands/`.
- `reporters/` — `terminal.py`, `json.py`, `sarif.py`, `markdown.py`.
- `checks/` — maintainability, typing, and documentation checks.
- `cicd/` — `loader.py` (tolerant YAML), `ir.py`, adapters (`github.py`,
  `gitlab.py`, `circleci.py`, `jenkins.py`), `checks_p0.py`/`checks_p1.py`, and
  `data.py` (bundled EOL snapshot).

### The `Check` protocol

Each check implements `healthytoon.core.protocols.Check`:

- `id: str`, `category: Category`, `default_severity: Severity`.
- `api_version: int` — set to `CHECK_API_VERSION` (currently 1).
- `cross_file: bool` — whether it inspects more than one file at a time.
- `file_kinds: frozenset[FileKind]` — inputs consumed; empty means all files.
- `supports(ctx) -> bool` — whether it applies to this context.
- `run(ctx) -> CheckResult` — findings, plus optional metrics/notes.

Checks read thresholds via `ctx.rule_options`, select inputs with
`ctx.files_for(self.file_kinds)`, and parse Python through `ctx.parse_ast`. A
`Finding` carries the check's `rule_id` (the `HT-*` id in reports) and should set
`location` and, when needed, a distinct `symbol` so fingerprints stay unique.

### Registration

`core/registry.py:builtin_checks()` imports `healthytoon.checks` and
`healthytoon.cicd` and concatenates each package's `builtin_checks()` tuple. Add
a check in a deterministic position; order affects report output. Third-party
checks use the `healthytoon.checks` entry-point group, and built-ins win on id
collisions.

## Adding a check

1. Implement the protocol under `src/healthytoon/checks/` (maintainability,
   typing, documentation) or `src/healthytoon/cicd/`.
2. Register the instance in that package's `builtin_checks()` tuple.
3. Add focused unit tests under `tests/unit/`.
4. Add a CLI behavior test under `tests/integration/`.
5. Document the rule id and its options in the `README.md` check list.

## Quality gates and nox

`noxfile.py` defines these sessions (the default is `check`):

- `format` — apply `ruff format`.
- `lint` — `ruff check` and strict `mypy src`.
- `tests` — full suite per version (`tests-3.12`, `tests-3.13`).
- `check` — `ruff format --check`, `ruff check`, `mypy src`, then pytest with a
  90% coverage floor.
- `release` — build locally; `release -- --remote` publishes (requires
  `UV_PUBLISH_TOKEN`/`UV_PUBLISH_USERNAME`/`UV_PUBLISH_PASSWORD` or `~/.pypirc`).

```bash
uv run --no-sync nox -l
uv run --no-sync nox -s check
uv run --no-sync nox -s release -- --remote
```

If nox is unavailable, run `uv run ruff format .`, `uv run ruff format --check .`,
`uv run ruff check .`, `uv run mypy src`, and
`uv run pytest --cov=healthytoon --cov-fail-under=90` directly.

CI (`.github/workflows/ci.yml`) runs Ruff lint/format checks, `mypy src`, and
pytest with `--cov-fail-under=90` on Python 3.12/3.13, plus a wheel smoke test.

## Tests layout

- `tests/unit/` — focused tests per module: checks, reporters, config, registry,
  runner, scoring, policy, baseline, and CLI argument handling.
- `tests/integration/` — CLI behavior (`test_checks_behavior.py`,
  `test_cycles_duplication_behavior.py`) and determinism. `test_determinism.py`
  runs the CLI as a subprocess under several `PYTHONHASHSEED` values and asserts
  byte-identical JSON stdout.
- `tests/fixtures/` — shared fixtures; `schemas/sarif-2.1.0.json` is the vendored
  SARIF 2.1.0 schema, validated by `tests/unit/test_reporter_sarif_schema.py`.

## Determinism and contracts

- Fingerprints are `rule_id` + normalized POSIX path + `symbol`
  (`core.types.compute_fingerprint`, `healthytoon/v2`), excluding line numbers
  and message text so baselines survive refactors and threshold changes.
- Findings are ordered by `canonical_sort_key` (file, line, column, rule id,
  title); the runner deduplicates and orders them so a report is a pure function
  of the checks' output.
- Scoring is fixed-shape: `by_category` always lists every category in canonical
  order, zero-finding categories score 100, and the overall score is a fixed
  weighted mean capped at 40 under any CRITICAL finding. It is informational and
  never fails the command on its own; exact values are pinned in the tests.
- Secret values must never appear in reports or logs. `ci.secrets` scans CI YAML
  and reports the location only, never the matched value.
