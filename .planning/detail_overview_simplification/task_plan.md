# Detail overview simplification

## Goal

Preserve two-line overview truncation and explicit expand/collapse controls while
removing duplicate DOM text, global overlay state, click shields, and per-instance
resize listeners. A single collapsed preview may trim its live text to reserve the
fixed toggle position.

## Phases

- [completed] Define the smallest accessible markup and behavior contract.
- [completed] Simplify server-rendered and lazy episode overview markup.
- [completed] Replace overlay CSS and measurement-heavy JavaScript.
- [completed] Add focused regression coverage.
- [completed] Run syntax, unit, rendering, and diff validation.
- [completed] Keep the Hero/backdrop stable, place the toggle at the text end,
  and animate expansion upward.
- [completed] Share the navigation icon-button visual contract with the
  series/episode information toggle without sharing page positioning.
- [in_progress] Anchor the series/episode information toggle to the detail
  page's top-right corner, symmetric with the navigation control.

## Boundaries

- Keep the current detail-page visual language and two-line collapsed state.
- Expanded text grows upward from the bottom-anchored content area without
  changing the Hero/backdrop height; no custom modal behavior.
- Support server-rendered movie/series/episode blocks and lazy episode panels.
- Do not change unrelated detail interactions or data preparation.
- Use native CSS clipping/positioning for the collapsed presentation; JavaScript
  may fit the collapsed preview but must not measure animation height or duplicate
  the full text in DOM attributes.

## Errors encountered

- A combined `rg` audit pattern had an unclosed regex group. No files were
  changed; subsequent checks use fixed-string searches instead.
- `playwright-cli --help` printed the expected command list, then its Windows
  Node process emitted a libuv closing-handle assertion. The Flask server and
  repository were unaffected; proceed with a named CLI session and verify each
  command result.
- The first browser launch used the CLI default Chrome channel, but Chrome is
  not installed at the expected path. The CLI supports the installed `msedge`
  channel, so retry with `open --browser msedge` rather than installing another
  browser.
- PowerShell parsed the unquoted `&episode=` in a Playwright URL as an operator.
  The command did not run; quote query-string URLs on the retry.
- The broad ignored-file preview included databases, images, backups, `.env`,
  and `.venv`, so no broad clean was run. A guarded PowerShell cleanup command
  was then blocked before execution; use an explicit path-scoped Git clean for
  only the browser/session/cache artifacts instead.
- A final combined `rg` line-number expression was misquoted by PowerShell and
  failed without changing files. Use separate fixed-string queries instead.
