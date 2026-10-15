# Healthytoon Testing Standards

## Testing strategy
- Prioritize behavior and integration tests over isolated unit tests.
- Test observable behavior through public interfaces whenever practical.
- Use unit tests only when they provide meaningful coverage that cannot
  reasonably be obtained through behavior or integration tests.
- If isolated unit tests seem necessary, explain why to the builder.

## What to verify
- Expected behavior and acceptance criteria.
- CLI commands, arguments, output formats, and exit codes.
- Realistic repository layouts and representative configuration files.
- Invalid inputs, missing files, permissions, and filesystem errors where relevant.
- Boundary conditions and important failure paths.
- Deterministic output and stable ordering where required.
- Security-sensitive behavior, including secret redaction where applicable.

## Test quality
- Tests must be deterministic and independent of execution order.
- Prefer realistic fixtures with clear expected outcomes.
- Avoid excessive mocking when real components can be exercised safely.
- Do not test private implementation details without a strong reason.
- Do not duplicate existing coverage without justification.
- Never weaken or delete a test merely to make the suite pass.

## Dependencies and scope
- Use the test framework and dependencies already configured in pyproject.toml.
- Do not add dependencies without approval from the builder.
- Modify tests only unless the builder explicitly assigns production-code changes.

## Reporting
Report the behaviors covered, test files changed, commands actually run,
results, coverage gaps, and blockers to the builder.
Do not claim that unexecuted tests passed.
Do not automatically initiate fixes or additional test cycles.
