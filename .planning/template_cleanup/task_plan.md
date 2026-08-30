# Template Cleanup

## Goal
Delete only templates that have no runtime, inheritance, test, or script references.

## Phases
- [complete] Inventory templates and all reference paths
- [complete] Confirm deletion candidates and remove them
- [complete] Run route/template tests and static checks

## Constraints
- Preserve current UI and existing unrelated working-tree changes.
- Do not modify database files.
- Treat dynamic template names conservatively.

## Errors Encountered
- Initial combined `rg` command failed because a PowerShell quote was not terminated; no files changed. The scan was split into simpler commands.
- A second dynamic-reference scan hit the same quoting issue; no files changed. It was replaced with literal searches for each loader API.
