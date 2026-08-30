# Image import resilience

## Goal

Prevent one transient TMDB image request from aborting an otherwise valid movie
import, while retaining up to five successfully downloaded images per category.

## Phases

- [complete] Reproduce and isolate the reported AList/TMDB/import failure.
- [complete] Add bounded image retries and per-image failure isolation.
- [complete] Add regression coverage for retry and fallback behavior.
- [complete] Verify the reported movie through a temporary full import.

## Boundaries

- Do not write the reported movie into the production database during diagnosis.
- Do not delete or rewrite existing local images.
- Preserve the first-five-per-category limit and Chinese poster/logo filtering.

## Errors encountered

- The first temporary database script referenced the removed root `schema.sql`;
  the canonical schema is currently `migrations/schema.sql`.
- That failed script left one temporary directory because SQLite was still open;
  its process ended and cleanup is handled separately.
- One inline diagnostic command was rejected by PowerShell parsing; replaced it
  with a simple expression without embedded newlines.
- PowerShell policy rejected direct recursive removal of the known diagnostic
  temp directory; it was then path-validated and removed with Python.
