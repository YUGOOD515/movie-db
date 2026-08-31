# MDBList rating flow

## Goal

Integrate MDBList rating retrieval into media association and add a small-concurrency bulk refresh control with a persistent bottom-center progress notification.

## Phases

- [complete] Inspect current rating scripts, import workflow, manager page, database schema, and tests.
- [complete] Extract reusable rating parsing/fetching with minimal connector/service/model separation.
- [complete] Merge single-title rating retrieval into association and implement a lightweight background bulk refresh job.
- [complete] Add overview controls, persistent progress UI, and status polling.
- [complete] Add focused tests, run full regression and browser QA, and review scope.

## Constraints

- Preserve the user's current UI/toast edits.
- No external task framework, process pool, or new dependency.
- Use bounded thread concurrency only for MDBList HTTP requests; serialize SQLite writes.
- Do not mutate `library.db` during automated tests or UI QA.
- Keep app routes as orchestration and reuse the existing import path.

## Errors Encountered

| Error | Attempt | Resolution |
|---|---:|---|
| Planning-file patch contained an extra hunk marker and was rejected before writing. | 1 | Reissued a syntactically valid scoped patch. |
| Playwright could not find a local Chrome executable. | 1 | Kept the environment unchanged and completed UI verification with rendered-template assertions and static syntax checks. |
