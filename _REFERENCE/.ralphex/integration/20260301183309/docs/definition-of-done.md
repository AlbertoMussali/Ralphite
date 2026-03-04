# Project Definition of Done and Minimal Smoke Plan

## Definition of Done

A work item is done only when all of the following are true:

1. Scope is complete and matches acceptance criteria.
2. Code builds and runs locally in the target environment.
3. Required tests pass for each affected layer:
   - Backend: unit tests for business logic and API/integration tests for changed endpoints.
   - Frontend: component-level tests for changed UI behavior and route-level smoke checks.
   - Infrastructure/dev tooling: verification of compose/make/dev scripts touched by the change.
4. Linting and type checks pass for changed code.
5. No critical or high-severity regressions are introduced.
6. Documentation is updated when behavior, setup, or operations changed.
7. Change is reviewable (clear diff, no unrelated edits).

## Minimal Smoke Plan

Run this plan after a merge candidate build and before release handoff.

1. Environment starts successfully.
   - Expected: local stack is up with no fatal errors in logs.
2. Health checks respond.
   - Expected: core services return healthy/ready responses.
3. Authentication baseline flow.
   - Expected: signup/login (or existing account login) succeeds and session is usable.
4. Core read/write path.
   - Expected: create one representative record, list/query it, and verify persistence.
5. Frontend critical route load.
   - Expected: app shell and at least one primary route render without runtime errors.
6. Background/async path (if enabled).
   - Expected: one queued job processes to completion and status updates are visible.
7. Basic failure handling.
   - Expected: invalid input returns a controlled error (no crash, no stack trace leak).

## How To Run Locally

1. Install dependencies for each workspace/service.
2. Start local dependencies (database, cache, queues) via project dev tooling.
3. Launch backend and frontend in development mode.
4. Run automated checks:
   - Backend tests.
   - Frontend tests.
   - Lint + typecheck.
5. Execute the manual smoke steps listed above and capture pass/fail notes.

Note: command names may vary by repository; use the project-standard scripts/targets (for example, `make`, `npm`, `pnpm`, or compose-based commands).
