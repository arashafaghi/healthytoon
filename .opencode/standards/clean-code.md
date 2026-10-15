# Healthytoon Clean Code Standards

## Correctness first
- Findings must identify a concrete defect, risk, or violation of an explicit requirement.
- Do not report personal preferences as defects.
- Prefer the smallest change that fixes the underlying problem.

## Simplicity and maintainability
- Prefer straightforward solutions over unnecessary abstractions or indirection.
- Avoid duplication when a clear, cohesive shared implementation is appropriate.
- Keep functions and modules focused on a clear responsibility.
- Avoid speculative generalization and premature optimization.
- Use descriptive names that communicate intent.

## Types and interfaces
- Use type annotations consistently.
- Keep public interfaces explicit, stable, and minimal.
- Follow existing project protocols and data contracts.
- Handle invalid inputs and expected failures explicitly.
- Avoid broad exception handling that hides failures.

## Dependencies and architecture
- Prefer the standard library when it provides a suitable solution.
- Reuse dependencies already configured in pyproject.toml.
- Do not introduce dependencies without a clear, justified benefit.
- Keep responsibilities separated; avoid unnecessary coupling and circular imports.

## Determinism and side effects
- Make output ordering and scoring deterministic where required.
- Avoid hidden global state and unintended side effects.
- Keep filesystem traversal and file handling safe and predictable.
- Do not expose secrets or sensitive file contents in reports or logs.

## Error handling
- Provide actionable error messages.
- Preserve useful context when propagating errors.
- Do not silently swallow errors or disguise failures as success.
- Keep exit codes consistent with the CLI's documented contract.

## Testing and review
- Judge tests by the behavior and risk they cover, not their quantity.
- Prefer behavior and integration coverage for externally observable contracts.
- Treat security, correctness, and data-loss risks as higher priority than style.
- Report only actionable findings, with evidence and impact.
