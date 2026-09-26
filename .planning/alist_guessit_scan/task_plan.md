# AList GuessIt Scan

## Goal
Add a small functional-style script that recursively reads configured local AList directories, parses video names with GuessIt, and emits normalized movie/episode names without renaming files or changing databases.

## Phases
- [complete] Inspect current AList connector, configuration, dependencies, and script/test conventions.
- [complete] Implement the smallest reusable pure-function pipeline plus AList I/O boundary.
- [complete] Add one focused runnable test and verify the script.

## Errors
- Initial Ponytail SKILL.md path resolved incorrectly; corrected to the installed plugin path.
- Live guest root listing returned AList code 401; kept credential-based login and added application-error handling.

## Parenthesized directory fix
- [complete] Reproduce against representative directories under `/Quark/来自：分享`.
- [complete] Isolate whether AList path traversal or GuessIt full-path parsing mishandles bracketed directory labels.
- [complete] Apply the smallest root-cause fix and add a regression check.
- [complete] Run targeted and repository verification without renaming media or writing databases.

## Current safeguards
- Preserve unrelated dirty files, including the untracked `MoviePilotV3/` tree.
- Read and normalize only; do not rename remote files.

## Database-aligned output quality
- [complete] Audit `media_names.json` against read-only `library.db` title and media records.
- [complete] Identify systematic mismatches and define the smallest useful output contract.
- [complete] Optimize the parser/report while preserving existing behavior where correct.
- [complete] Add focused regressions and run repository verification.

## Import-candidate correction
- [complete] Re-trace the actual media-import JSON contract and `imdb.db` lookup schema.
- [in_progress] Remove the incorrect `library.db`-authoritative normalization path.
- [pending] Resolve GuessIt movie/series/episode parses to local IMDb candidates and emit import-ready records.
- [pending] Validate representative AList paths without writing either database or renaming media.

## Errors
- Browser page-context storage inspection was unavailable; switched to visible, authenticated AList navigation without extracting credentials.
- A PowerShell `foreach (...) { ... } | Format-Table` command hit the known empty-pipe parser restriction; reran by collecting into an array first.
- The initial basename-only patch failed the existing generic `S01E02` regression; added the minimal parent-title fallback instead of weakening the test.
- A combined search/read command referenced the old root-level `db.py`; the current helper is `models/database.py`, so subsequent inspection uses that path.
- The first database-alignment patch used mojibake-rendered Chinese help text as context and did not apply; split subsequent patches around ASCII-only anchors.
- The first combined full-verification command had an unterminated PowerShell quote in an inline regex and did not run; replaced it with separate commands using the script's existing compiled regex.
