# Local MPC-HC playback

## Goal
Replace browser navigation to AList with a Flask-controlled local MPC-HC playback flow using `helpers.get_video_link()` and `helpers.play_video()`.

## Phases
- [complete] Audit current routes, data contracts, templates, and tests.
- [complete] Add source lookup and playback orchestration.
- [complete] Replace AList playback links in Jinja templates.
- [complete] Update and run focused/full regression tests.
- [complete] Verify no playback path still navigates to AList.

## Boundaries
- Keep AList path storage and import validation; AList remains the media source backend.
- Remove only browser-facing AList jump playback behavior.
- Preserve unrelated working-tree changes.

## Errors
- First regression run had two stale assertions: the mapping table now renders a decoded path instead of an encoded URL, and ordinary homepage poster cards intentionally link only to details. Updated the assertions without changing product behavior.
- Final static-check command used Bash `||` under Windows PowerShell and did not execute. Replaced it with a PowerShell `$LASTEXITCODE` branch.
