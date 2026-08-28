# Progress

- 2026-08-28: Recovered prior planning context, audited the current playback chain, and isolated this task in a scoped plan.
- 2026-08-28: Defined a source-id-based Flask playback route and identified every browser-facing AList jump to remove.
- 2026-08-28: Implemented source lookup, local MPC-HC launch orchestration, local `/play/...` links, and plain-text mapping paths. Added movie/TV playback regression coverage.
- 2026-08-28: First regression run reached all tests; two stale presentation assertions failed and were corrected to match the intended UI contract.
- 2026-08-28: Compile check and all 10 unit tests passed, including mocked movie/TV MPC-HC handoff and missing-source behavior.
- 2026-08-28: Initial combined static-check command was rejected by PowerShell syntax before any check ran; switched to native PowerShell control flow.
- 2026-08-28: Final `git diff --check` and stale-link search passed. All planned phases are complete.
