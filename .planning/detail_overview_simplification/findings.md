# Findings

- The current implementation stores each overview in the visible span, a data
  attribute, and a second expanded panel.
- Manual binary search writes text and forces layout reads on every resize.
- Every overview instance owns a window resize listener.
- A single body-level shield is shared across instance closures, so its click
  handler can close the wrong overview.
- The active behavior has no focused regression tests.
- The established detail contract keeps episode cards as selectors and local
  interaction logic in `static/site.js`; this refactor does not alter playback,
  selection, or data preparation.
- The replacement will keep one paragraph and one button per overview. CSS owns
  clamping, JavaScript owns only overflow detection and expanded state, and the
  expanded paragraph participates in normal document flow.
- One shared `ResizeObserver` can cover initial and lazy-created panels. Existing
  `detail:shown` dispatch remains a useful explicit refresh when hidden panels
  become visible.
- The implementation diff removes 77 net lines from the three runtime files.
  No manual text mutation or overlay state remains.
- Syntax checking, `git diff --check`, and all 20 unit/integration tests pass.
- Real Edge rendering on the Interstellar movie detail reports a 32 px line
  height, 64 px collapsed height, 256 px full scroll height, and a computed
  two-line clamp. The overflow detector exposes exactly one “更多” button.
- Expanding changes the same paragraph to 256 px client/scroll height with no
  clamp and `display: block`; collapsing restores the “更多” control. There is
  no duplicate accessible paragraph in either browser snapshot.
- A short server-rendered episode overview exposes no expansion control. After
  selecting a different episode, the lazy-generated short overview also appears
  without a control, and the browser console remains free of errors/warnings.
- Revealing the initially hidden full-series panel triggers overflow detection:
  its long overview is clamped and receives one “更多” button without manual
  refresh code or per-instance window listeners.
- At a 375 × 812 viewport, the long movie overview remains exactly 64 px high
  at a 32 px line height, reports a two-line clamp, and exposes the control.
  The final browser console contains no errors or warnings.
- The Hero uses `min-height`, so normal-flow expansion is allowed to enlarge the
  container. Because the backdrop stage is absolutely inset to that container,
  its visible area changes too. Bottom alignment only produces upward growth
  when the flex container has a definite height.
- With a definite responsive height applied, the collapsed desktop movie Hero
  and backdrop both measure 672 px from y=0 to y=672. The content is bottom
  anchored at y=672 and begins at y=118.23.
- After expansion, Hero and backdrop remain 672 px while the content top moves
  from y=118.23 to y=-73.77 and its bottom remains y=672, confirming upward-only
  growth with no backdrop geometry change.
- Native line clamping can clip text, but it cannot make a live button part of
  the generated ellipsis across browsers. The clean compromise is one inline
  button positioned at the second-line end while collapsed and returned to
  normal inline flow after the same paragraph expands.
- The final paragraph uses native CSS two-line height clipping. On desktop the
  collapsed button's right and bottom edges exactly match the paragraph's, so
  it reads as the second-line ending rather than a separate control row.
- Native `interpolate-size` lets the 280 ms `max-height` transition move from
  two line-heights to `max-content`, so JavaScript no longer supplies a pixel
  endpoint. Unsupported browsers retain functional instant expansion.
- At 375 x 812, the Hero/backdrop remain 763.27 px through expansion and the
  toggle stays reachable at the expanded paragraph end. The series page also
  keeps a one-line episode overview's toggle hidden. Browser console: 0 errors,
  0 warnings (apart from Playwright's informational lazy-image notice).
- Final reviewed placement removes the gradient entirely and nests the toggle
  inside `data-detail-overview-text`. A `WeakMap` retains the full string while
  the collapsed live copy is fitted around a reserved fixed-width button box.
- On desktop, collapsed/expanded/collapsed button bounds remain exactly
  x=1130, y=564.81, 68 x 32 px. On 375 px mobile they remain x=295, y=656.08,
  68 x 32 px. Both viewports keep Hero/backdrop fixed and report no console
  errors or warnings.
