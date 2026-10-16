# Healthytoon Roadmap

## 1. Project goal

Healthytoon is a Python CLI tool for assessing Python project maintainability and CI/CD configuration quality.

It should provide deterministic findings, actionable reports, and a consistent scoring model. It should support offline repository analysis wherever practical.

## 2. Development principles

- Prefer simple, maintainable, typed Python.
- Use the standard library when it provides a suitable solution.
- Keep dependencies justified and limited.
- Define shared interfaces before parallel implementation.
- Prioritize behavior and integration tests for externally observable behavior.
- Keep output deterministic.
- Never expose detected secret values in reports or logs.
- Document implemented features only; distinguish planned features from available functionality.
- Do not start a new phase until the current phase has been reviewed and accepted.

## 3. Architecture

- `core/`: shared types, discovery, context, registry, runner, policy, scoring, and protocols.
- `config/`: configuration models, defaults, loading, merging, and validation.
- `cli/`: command-line interface and commands. use Typer lib
- `reporters/`: terminal, JSON, SARIF, and Markdown output.
- `checks/`: maintainability checks.
- `cicd/`: CI/CD configuration loading, platform adapters, intermediate representation, and checks.
- `tests/`: behavior, integration, and focused unit tests where appropriate.

Follow the existing implementation and established `Check` protocol. The builder owns shared contracts and integration decisions.

## 4. Reporting and finding contracts

Findings must have stable rule identifiers, severity, location where applicable, and actionable descriptions. Output ordering and scoring must be deterministic.

Supported output formats and severity behavior must be determined by the implementation and documented accurately. SARIF output must follow SARIF 2.1.0.

The scoring formula and severity mapping must be confirmed before implementing the scoring-dependent maintainability checks.

## 5. Tooling and quality

Use the tooling configured in `pyproject.toml`.

Expected quality gates include:

- Ruff linting and formatting.
- Strict mypy checks.
- Pytest.
- Deterministic-output checks.
- CLI smoke tests and installation checks where relevant.

The final hardening phase will introduce the self-CI coverage target and wheel smoke test.

## 6. Scoring

The scoring model is fixed and fully specified (see Phase 2 decision 10); it is
implemented in `core/scoring.py` and pinned by exact-value tests.

- **Penalty table** (per finding, scaled by confidence in `[0, 1]`): CRITICAL 40,
  HIGH 20, MEDIUM 8, LOW 3, INFO 0.
- **Category score** = `clamp(100 - sum of penalties, 0, 100)`, rounded to 2 dp.
- **Fixed shape**: `by_category` always contains every `Category` in canonical
  order; a category with no findings scores 100 with 0 findings.
- **Category weights**: MAINTAINABILITY 1.0, SECURITY 1.0, CI_CD 0.8, TESTING 0.8,
  DEPENDENCIES 0.7, TYPING 0.6, DOCUMENTATION 0.5, PACKAGING 0.5.
- **Overall** = fixed-weight mean over all categories; capped at 40 when any
  CRITICAL finding is present; rounded to 2 dp.
- **Grades**: A ≥ 90, B ≥ 80, C ≥ 70, D ≥ 60, else F.

The overall score is informational: it never fails the command on its own. Only
the `fail_on` finding gate (and an incomplete analysis) affect the exit code.
Because zero-finding categories score 100, the overall score is intentionally
lenient and meant as a stable, comparable indicator rather than a strict gate.

## 7. Agent orchestration

- The primary builder owns planning, delegation, shared contracts, integration, and final decisions.
- Delegate implementation to `@implementer`.
- Delegate behavior and integration test work to `@test-engineer`.
- Delegate independent, read-only reviews to `@code-reviewer`.
- Delegate user-facing documentation to `@docs-writer`.
- Give each agent a specific task, context, file ownership, constraints, and acceptance criteria.
- Run independent tasks in parallel only when dependencies permit.
- Do not let agents edit the same files concurrently.
- Review agent reports and inspect their changes before integration.
- The builder owns final verification; do not accept unverified claims that checks passed.
- Agent reports do not automatically trigger further tasks or review cycles.
- Update this roadmap when phase status changes.
- Do not commit unless explicitly authorized.

## 8. Phased task breakdown

### Phase 0 — Foundation (status: complete)

- **T0.1 — `core/types.py` and `errors.py`:** Define enums, dataclasses, and exit-code mapping. Acceptance: ordering and serialization tests.
- **T0.2 — Configuration loader:** Implement four-layer configuration merging and validation. Acceptance: precedence and invalid-TOML cases tested.
- **T0.3 — CLI skeleton:** Implement `cli/app.py`, `list` and `version` commands, and the console script. Acceptance: `python -m healthytoon version` works; exit codes 2, 3, and 4 are tested.
- **T0.4 — Discovery and context:** Implement `core/discovery.py` and `context.py`, including glob patterns, gitignore handling, encoding, and symlink-safe traversal. Acceptance: fixture-repository traversal tests.

### Phase 1 — Reporting spine (status: complete)

This phase establishes the reporting infrastructure needed by the maintainability and CI/CD engines.

- **T1.1 — Runner, registry, and policy:** Implement the two-phase runner, check registry, and `policy.py`. Acceptance: deterministic ordering across different `PYTHONHASHSEED` values.
- **T1.2 — JSON and terminal reporters:** Implement `reporters/json.py` and `terminal.py`; Rich support is optional. Acceptance: repeated JSON output is identical.
- **T1.3 — SARIF and Markdown reporters:** Implement `reporters/sarif.py` and `markdown.py`. Acceptance: SARIF 2.1.0 schema validation and golden-tested severity mapping.

### Phase 2 — Product decisions (status: confirmed 2026-10-16)

The following defaults were proposed and are now CONFIRMED as implementation requirements.

1. **Security scanning:** Hardcoded-secret detection is a CI/CD check that lives in `cicd/`, but its findings are categorized `SECURITY`. Detected secret values must be redacted from every report and log.
2. **Failure policy:** Default to `fail_on=high`. The overall score is informational and does not independently fail the command.
3. **Fingerprints:** Use `rule_id` + normalized file path + `symbol` where available. Exclude line numbers and message text so a baseline survives refactors and threshold changes. Algorithm id: `healthytoon/v2`.
4. **Python EOL data:** Bundle versioned EOL data with the application and expose `data_as_of` in reports. Refresh the data with releases.
5. **Branch protection:** Out of scope for offline scans in v1. Any future branch-protection checks must be explicitly enabled and online.
6. **HTML, Docker, and Jenkins:** Defer HTML reports and Docker-specific checks. Support Jenkins presence detection only.

Additional confirmed decisions:

7. **Scan scope:** Default to scanning the whole repository (`include = ("**",)`) minus safe exclusions (`.git`, `.venv`/`venv`, `node_modules`, build outputs, caches, `*.egg-info`, `site-packages`). Checks declare the file kinds they consume; when a repository contains none of a check's kinds, the check is recorded as a visible `SKIP`.
8. **Check failures:** A crashed check is recorded as an `ERROR` outcome, surfaced explicitly in output, and returns exit code 5 (analysis incomplete). A skipped optional check is visible but does not fail the command.
9. **Dependencies:** Radon is optional and detected at runtime; if unavailable, complexity analysis is reported as skipped (no internal McCabe fallback in v1). Vulture is an optional external-tool adapter, not a mandatory runtime dependency. `ruamel.yaml` is a core dependency for the tolerant CI loader.
10. **Scoring formula:** fixed category weights and a fixed `by_category` shape (every category, with zero-finding categories scoring 100). Penalty table: CRITICAL 40, HIGH 20, MEDIUM 8, LOW 3, INFO 0, scaled by finding confidence. Weights: MAINTAINABILITY 1.0, SECURITY 1.0, CI_CD 0.8, TESTING 0.8, DEPENDENCIES 0.7, TYPING 0.6, DOCUMENTATION 0.5, PACKAGING 0.5. Overall = fixed-weight mean over all categories, capped at 40 when any CRITICAL finding is present. Grades: A ≥ 90, B ≥ 80, C ≥ 70, D ≥ 60, else F. Informational only.

### Phase 2a — Maintainability engine (status: complete — T2a.1a–T2a.4)

- **T2a.1a — AST structural checks:** Implement function length, nesting depth, module length, annotation coverage, and docstring coverage.
- **T2a.1b — Dependency and duplication checks:** Implement import-cycle detection and token-based duplication detection.
- **T2a.2 — Complexity:** Evaluate Radon for cyclomatic complexity analysis. Keep it optional unless a justified dependency decision makes it mandatory. If Radon is unavailable, report that complexity analysis was skipped; do not implement an internal McCabe fallback in v1.
- **T2a.3 — Dead code:** Add a Vulture adapter and framework-specific whitelists.
- **T2a.4 — Scoring:** Implement score aggregation according to §6 after the scoring formula and severity mapping are confirmed.

**Acceptance criteria:** Realistic fixture projects trigger the expected findings and severities. Tests verify observable CLI results, deterministic output, and relevant failure cases. Each check must have appropriate focused coverage.

### Phase 2b — CI/CD engine (status: in progress)

- **T2b.1 — Loader:** Implement `cicd/loader.py` using a tolerant `ruamel` loader, including YAML `on:` handling, `RawTag`, duplicate-key detection, and file-size limits.
- **T2b.2 — GitHub adapter:** Parse GitHub Actions workflows into a shared intermediate representation (IR).
- **T2b.3 — P0 checks:** Implement workflow presence, workflow parsing, tests-present, pinned actions, explicit permissions, and hardcoded-secret detection with redaction.
- **T2b.4 — P1 checks:** Implement Python EOL, matrix and consistency checks, coverage gate, lockfile, dependency-update, and trigger checks.
- **T2b.5 — Other platforms:** Add GitLab CI and CircleCI adapters. Jenkins support is presence-only in v1.

**Acceptance criteria:** Golden fixtures cover valid and invalid configurations for each supported platform. Tests verify findings, severity, secret redaction, and CLI output.

### Phase 2 implementation rules

- Phase 2a and Phase 2b may run in parallel only after shared interfaces and file ownership are agreed.
- The builder owns shared contracts, registry integration, scoring, configuration changes, and final integration.
- Assign explicit file ownership and acceptance criteria to every task.
- Delegate implementation to `@implementer`, behavior and integration tests to `@test-engineer`, read-only review to `@code-reviewer`, and user-facing documentation to `@docs-writer`.
- Agents must not edit the same files concurrently.
- Review all agent results and run final verification.
- Do not begin Phase 3 until Phase 2 is accepted.

### Phase 3 — Hardening

- **T3.1 — Baseline and suppression:** Implement finding baselines and suppression using the agreed fingerprint algorithm.
- **T3.2 — `init` and `explain`:** Add initialization and finding-explanation commands.
- **T3.3 — Self-CI and dogfooding:** Add Ruff, strict mypy, pytest with at least 90% coverage, and wheel-install smoke tests. Run Healthytoon against its own repository.

## 9. Product decisions and deferred scope

The six decisions in Phase 2 must be confirmed before implementation. Additional scope may be reconsidered after v1, but should not be added implicitly during implementation.

Deferred for v1:

- HTML reporting.
- Docker-specific analysis.
- Online branch-protection checks.
- Jenkins workflow analysis beyond presence detection.
- An internal McCabe fallback when Radon is unavailable.

## 10. Completion criteria

A phase is complete only when:

- Its acceptance criteria are met.
- Relevant tests and quality checks have actually been run.
- Results have been reviewed by the builder.
- Known limitations and unresolved issues are recorded.
- User-facing documentation accurately describes any newly available functionality.
- The roadmap status has been updated.

The builder must not begin the next phase without explicit authorization.