# Bootstrap CSS cleanup

## Goal
Reduce custom frontend CSS as far as practical by using Bootstrap 5.3 components and utilities while preserving the current page structure, media data, playback routes, horizontal overflow behavior, and detail-page theme gradients.

## Phases
- [complete] Audit templates against custom selectors and define the minimal CSS boundary.
- [complete] Refactor shared layout and homepage markup to Bootstrap utilities.
- [complete] Refactor detail markup to Bootstrap grid and components.
- [complete] Replace `static/style.css` with a compact exception layer.
- [complete] Update project guidance and run regression/static checks.

## Constraints
- Do not use `design-taste-frontend` for this project.
- Preserve unrelated playback changes and the existing detail backdrop width edit.
- Do not perform browser visual inspection; the user will review visuals.
- Retain custom CSS only for media overlays/ratios, horizontal rails, detail scrims, and small behavior Bootstrap cannot express directly.

## Errors
- A planning-file status patch failed because an earlier status update had duplicated the stylesheet phase. The duplicate was removed; product files were unaffected.
