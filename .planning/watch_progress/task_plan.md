# Watch progress and MPC-HC API

## Goal
Record movie and per-episode playback progress in SQLite, resume from the stored position, and synchronize position/duration from the local MPC-HC Web API.

## Phases
- [completed] Verify MPC-HC API behavior and audit current schema/playback chain.
- [completed] Draft and validate a reviewable SQL migration without applying it to `movie.db`.
- [completed] Receive explicit migration approval.
- [completed] Re-verify the MPC-HC 2.8.1 API contract and migrate with backup/integrity checks.
- [completed] Implement model/helper/app contracts and automated tests against the approved schema.
- [completed] Complete live-safe integration checks without launching user media unnecessarily.

## Constraints
- Do not use `design-taste-frontend`.
- Preserve current Bootstrap-first frontend and local MPC-HC playback route.
- Keep SQL in SQL Style Guide river formatting.
- Do not execute schema changes against `movie.db` before explicit approval.
- Do not claim live MPC-HC API behavior unless observed locally or supported by primary documentation.
- Do not perform visual frontend inspection; the user will review the UI.

## Errors
- The first in-memory validation command had a PowerShell quoting error; rerunning through standard input succeeded.
- The first regression run had 2 expected mock-call failures because `play_video()` now receives resume and callback keyword arguments; production code compiled and all other tests passed.
