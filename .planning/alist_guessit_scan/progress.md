# Progress

- 2026-09-03: Started scoped plan; read Ponytail and planning skill instructions; completed memory quick pass.
- 2026-09-03: Confirmed the current AList list endpoint contract from upstream source and exercised GuessIt 4.4.0 against movie, episode, Unicode, and multi-episode samples.
- 2026-09-03: Added `connectors.alist.list_directory`, the functional JSON-producing script, and focused recursive/API tests.
- 2026-09-03: Focused tests, compilation, CLI help, diff check, and all 54 repository tests passed.
- 2026-09-03: Verified local AList is reachable but requires authentication; hardened shared response handling and reran all 54 tests successfully.
- 2026-09-04: Reopened the task after the user reported bracketed directory names are not handled correctly; current code and worktree inspected.
- 2026-09-04: Inspected four live representative directories through the existing authenticated local AList browser session; reproduced full-path GuessIt contamination.
- 2026-09-04: Queried the four live directories through their authenticated AList API session without extracting credentials; all returned code 200 and correct full virtual paths.
- 2026-09-04: Patched GuessIt input to basename-first with a narrow prefix-only parent fallback; the three focused tests now pass.
- 2026-09-04: All 55 repository tests, Python compilation, and diff checks passed; no media names or databases were changed.
- 2026-09-04: Started a read-only quality audit of `media_names.json` against `library.db`; no database or media mutations authorized.
- 2026-09-04: Compared all 882 scan rows with 771 database media rows; exact path coverage is 771/771 and revealed 328 type, 138 season, and 218 episode GuessIt disagreements.
- 2026-09-04: Implemented read-only database-first normalization and conservative unknown fallback; focused tests and an exhaustive 882-row dry run passed.
- 2026-09-04: All 56 repository tests, compilation, filename legality checks, and diff checks passed; `library.db` SHA-256 was unchanged.
- 2026-09-04: User corrected the objective; started redesigning output as IMDb-resolved import candidates, with `library.db` limited to optional already-imported detection.
- 2026-09-04: Re-traced the batch JSON contract and live IMDb schema; confirmed local title/alias/year lookup plus episode-coordinate resolution can produce import candidates without external search.
