# Batch media import and association

## Goal

Add batch entry/association to the media manager: users can add rows with a plus button or load rows from a JSON file, and can download a ready-to-edit JSON template.

## Phases

- [complete] Inspect the current manager form, import service, frontend behavior, and tests.
- [complete] Define the batch request contract, validation, partial-success behavior, and JSON template.
- [complete] Implement the backend endpoint and manager-page interactions.
- [complete] Add backend/frontend contract tests and run regression checks.
- [in_progress] Review the rendered page and final diff without altering unrelated work.

## Constraints

- Reuse the existing `import_media` service for each item.
- Preserve the existing single-item workflow and current Bootstrap visual style.
- Keep `app.py` as orchestration; no database schema changes.
- Validate uploaded JSON on both client and server; never trust client-only validation.
- Preserve unrelated uncommitted detail/rating/AList-startup work.

## Design decisions

- Keep `/manager/import` for compatibility; add `/manager/import/batch` for the new unified multi-row form.
- Accept either repeated form fields or an uploaded UTF-8/UTF-8-BOM JSON file. JSON may be `{ "items": [...] }` or a top-level array; the supplied template uses the object form.
- Limit batches to 100 items and JSON uploads to 1 MiB.
- Process items sequentially through the existing `import_media()` service. Report success and failure counts plus indexed failure messages.
- Redirect back to the association tab after processing so batch feedback remains visible.

## Errors Encountered

| Error | Attempt | Resolution |
|---|---:|---|
| Initial patch inserted the batch route before the single-route redirect, leaving that redirect unreachable below a helper. | 1 | Moved the original redirect back into `import_media()` before proceeding; caught during immediate code inspection, before tests. |
| Focused-test patch used a stale association-action assertion as context. | 1 | Re-read the exact test sections and split the test changes into smaller scoped patches. |
| Playwright CLI defaulted to the absent Google Chrome executable. | 1 | Confirmed Microsoft Edge is installed and selected the supported `--browser msedge` channel. |
| Playwright tried to click the visually hidden file input, which was covered by its visible label. | 1 | Use the fresh snapshot's visible upload-label reference to open the file chooser, then call the explicit upload command. |
