# Healthytoon Agent Instructions

## Project rules
- Read `ROADMAP.md` before starting work.
- Implement only the currently requested phase.
- Follow existing architecture and the `Check` protocol.
- Prefer simple, maintainable, typed Python.
- Use the libraries and tooling configured in `pyproject.toml`.
- Do not add dependencies without justification.
- Do not overwrite unrelated work or commit unless explicitly asked.

## Orchestration
- The primary builder is the sole orchestrator and decision-maker. It owns planning, delegation, integration, conflict resolution, and final decisions.
- Delegate implementation, test engineering, and independent review to their respective agents when appropriate. The builder may handle small or tightly coupled tasks directly.
- Give each subagent a specific task, relevant context, constraints, and acceptance criteria.
- Run independent tasks in parallel only when dependencies permit it.
- Define shared interfaces before parallel implementation.
- Do not assign overlapping file modifications to multiple agents concurrently.
- Evaluate each report and inspect relevant changes before accepting them.
- Reports and review findings do not automatically trigger further work. The builder decides whether another task or iteration is necessary.
- The builder owns final verification; independently verify relevant claims and results.
- Delegate implementation to `@implementer`.
- Delegate behavior and integration test work to `@test-engineer`.
- Delegate independent, read-only reviews to `@code-reviewer`. 
- Delegate user-facing documentation to `@docs-writer`.
- The builder may handle small or tightly coupled tasks directly when delegation
  would add unnecessary overhead.
- The builder owns phase boundaries and review baselines.
- After acceptance, commit the phase unless the user says otherwise.
- Every review must identify its base commit and review only changes since that baseline, plus relevant surrounding context.
- Include untracked files and deleted/renamed files in the review scope.
- Maintain a lightweight review ledger: phase, base commit, reviewed commit, findings, dispositions.
- Don't re-review unchanged code without a concrete reason.
- Don't start the next phase until the current phase's review findings and acceptance criteria are resolved.

## Agent reporting

Reports are returned to the builder, not treated as new tasks.

- Implementer: summarize changes, design decisions, checks run, and blockers.
- Test engineer: summarize behavior covered, tests added, commands run, and gaps.
- Code reviewer: report only actionable findings, with severity, file/line,
  rationale, and suggested fix. If no findings, say so.
- Other agents: report only the deliverables requested in their task.

The builder evaluates reports and decides what happens next.
A report does not automatically trigger another agent invocation or another
review/fix cycle. Iterate only when the builder explicitly decides it is needed.

## Completion
- Run relevant tests and configured quality checks.
- Update `ROADMAP.md` when a phase's status changes.
- Summarize delivered work, verification results, and known limitations.
- Do not start the next phase without explicit authorization.
