# Findings

## Prior project context

- `app.py` is responsible for Flask-level orchestration; connectors/services own external integrations and business workflows.
- Existing unrelated working-tree changes affect detail/rating files and must be preserved.

## Research

- Official manual-install documentation uses `.\\alist.exe server` for an attached foreground Windows process.
- The same page documents `alist start`, `alist stop`, and `alist restart` as silent PID-managed commands for v3.4.0+, but also recommends NSSM or a hidden VBS launcher for Windows daemon operation.
- The application must use AList's installation directory as `cwd`, so its relative `data/` and daemon files resolve consistently.
- Because the requested reference specifically uses the documented silent `start` command, `subprocess.run` is appropriate here: the command returns after launching AList's PID-managed background process, unlike the blocking `server` command.
- A historical upstream issue reports inconsistent data locations when mixing `server` and `start` on Windows; this reinforces using one configured working directory and one launch mode consistently.
- The current application is a module-level Flask app and README starts it through `flask --app app run`; therefore wiring only inside `if __name__ == "__main__"` would miss the documented startup path.
- No `alist.exe` was discoverable through `PATH`, and no running `alist` process was observed. The executable location must be configured rather than guessed.
- `.env` is ignored and not auto-loaded; `.env.example` is allowed by `.gitignore` but does not currently exist.
- Automated test imports explicitly disable autostart, so a developer's persistent `ALIST_DIR` environment cannot launch a real process during the suite.

## Verification

- 27 unit/integration tests passed.
- Python compilation passed for the application layers and tests.
- Scoped `git diff --check` passed; the only full-worktree warning is an unrelated pre-existing EOF blank line in user-modified `models/library.py`.
- Live Windows verification used the detected `C:\alist\alist.exe`: the new service returned `started=True`, `http://127.0.0.1:5244` returned 200, and `alist.exe stop` returned 0. No AList process remained afterward.
- Final scope review found only the intended tracked edits in `app.py`, `README.md`, and `tests/test_library_app.py`, plus new `.env.example`, `services/alist_process.py`, focused tests, and task planning records.
