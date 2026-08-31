# AList process orchestration

## Goal

Add safe AList startup orchestration to the MovieDB application based on the official manual-install CLI behavior, without disturbing unrelated working-tree changes.

## Phases

- [complete] Verify official AList commands and inspect the current application/config/test structure.
- [complete] Design configuration and process lifecycle behavior that fits the existing service-oriented architecture.
- [complete] Implement the AList launcher and wire it into the application entry flow.
- [complete] Add focused tests and run relevant regression checks.
- [complete] Review the diff, document configuration, and summarize verified boundaries.

## Constraints

- Preserve existing user changes and do not modify unrelated files.
- Keep `app.py` as orchestration; place process behavior in a focused service/connector module.
- Do not require AList to be installed at a hard-coded path for tests or ordinary imports.
- Avoid starting real external processes during automated tests.

## Design decisions

- Enable automatic startup only when `ALIST_DIR` is explicitly configured; imports and tests remain side-effect free otherwise.
- Probe `ALIST_ORIGIN` first, run `[alist.exe, "start"]` only when unavailable, and poll until the HTTP service responds.
- Use `alist.exe`'s parent directory as `cwd` to keep AList data and daemon files in one location.
- Log a startup failure without preventing the read-only MovieDB pages from starting; AList-dependent operations can be restored independently.
- Do not stop AList when Flask exits because `alist start` creates a separately managed background process and debug reloads must not kill it.

## Errors Encountered

| Error | Attempt | Resolution |
|---|---:|---|
| PowerShell `Join-Path` received three positional arguments while inspecting the pre-existing active plan. | 1 | Use explicit nested paths or `-AdditionalChildPath`; no project files were changed by the failed read. |
| Full-worktree `git diff --check` reported a pre-existing blank line at EOF in user-modified `models/library.py`. | 1 | Leave the unrelated file untouched and run the check against this task's tracked files separately. |
