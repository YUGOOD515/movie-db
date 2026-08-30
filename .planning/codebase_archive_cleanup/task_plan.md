# Codebase cleanup and archive audit

## Goal

Audit every project directory for unused code, stale documentation, generated
artifacts, obsolete migrations, and completed working records; delete items
whose history is already safely retained by Git and archive material that still
has reference value but should not remain in the active project surface.

## Phases

- [completed] Inventory every directory, tracked/untracked state, size, age, and runtime entry point.
- [completed] Trace code, template, static-asset, script, and configuration references.
- [completed] Classify candidates as keep, delete, archive, or user-data protected.
- [completed] Apply narrowly scoped deletions and archival moves.
- [completed] Update active documentation and ignore rules.
- [completed] Run tests, compilation, reference checks, database checks, and final inventory.

## Boundaries

- Inspect hidden and generated directories as well as source directories.
- Do not delete `imdb.db`, `library.db`, media images, backups, or user output
  without evidence that the exact artifact is disposable.
- Preserve current runtime behavior and the seven-table schema.
- Treat Git history as recovery for deleted tracked legacy code.
- Prefer a small explicit archive over leaving historical files mixed with active code.

## Errors encountered

- None.
