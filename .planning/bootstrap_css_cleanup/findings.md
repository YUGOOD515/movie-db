# Findings

- Bootstrap 5.3.8 and Bootstrap Icons 1.13.1 are already loaded globally.
- The current stylesheet is 976 lines / 20,499 bytes; all 68 custom classes are referenced, but many repeat Bootstrap utilities.
- Templates already use Bootstrap rows, columns, offcanvas, buttons, tables, spacing utilities, and theme attributes.
- The strongest duplication is between `container-xl` and `.page-main`, Bootstrap column widths and custom rail widths, `.btn` and `.btn-accent`, plus repeated frame/icon/text primitives.
- Custom behavior that should remain: horizontal overflow widths, centered overlay play buttons, genre/detail image overlays, detail theme scrims, and mobile detail composition.
- The Bootstrap-first rewrite reduced `style.css` from 976 lines / 20,499 bytes to 217 lines / 3,811 bytes after final pruning.
- Final CSS contains 25 custom class selectors; all are referenced. Decorative hover transforms, custom palette/font system, duplicated card primitives, and global motion overrides were removed.
