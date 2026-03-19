# Open WebUI Repository Instructions

These instructions apply to all Copilot code suggestions in this repository.

## General Coding Guidelines

- Preserve existing behavior unless the task explicitly requires a change.
- Prefer small, targeted patches over broad refactors.
- Reuse existing project patterns, naming conventions, and module boundaries.
- Do not introduce new dependencies if existing project tooling can solve the problem.
- Keep logging concise and actionable.
- Never remove or weaken existing error handling without replacing it with equivalent or better handling.

## Testing Requirements

- Every new non-trivial code path must be backed by automated tests.
- Every bug fix should include at least one regression test.
- Cover happy path, edge cases, and failure paths.
- Prefer deterministic tests and mock external services/network calls.
- Run targeted tests for changed areas first, then broaden scope when needed.
- Do not submit code changes as complete when related tests fail.

## Debugging and Validation Workflow

- Reproduce issues before fixing when possible.
- Make one logical change at a time and validate after each step.
- Use concrete evidence (errors, logs, failing tests) to guide fixes.
- After changes, verify behavior with tests and runtime checks, not assumptions.

## Docker and Runtime Workflow

- When runtime behavior is affected, identify the active Docker Compose project first.
- Rebuild only the impacted service(s), not the entire stack, unless required.
- For this repository, default to rebuilding only the open-webui service.
- Verify service health after rebuild using compose status and health endpoint checks.
- Record the exact commands used for rebuild and verification in your response.

## Code Review Expectations

- Prioritize security, correctness, and performance risks.
- Validate input handling, authentication/authorization logic, and error paths.
- Flag unnecessary complexity and suggest simpler alternatives.
- Prefer readable, maintainable code over clever one-off implementations.

## Scope and Change Discipline

- Avoid unrelated refactors in feature or bug-fix tasks.
- Keep changes localized to the smallest set of files necessary.
- Update documentation when behavior, configuration, or operations change.

## Open WebUI Project-Specific Constraints

- Keep Azure Video Indexer and Soniox logic clearly separated.
- For Soniox media processing, preserve current audio-first preprocessing behavior for video inputs.
- Maintain iPhone compatibility for QuickTime/HEVC uploads.
- Keep code and comments in plain ASCII unless non-ASCII is necessary.
