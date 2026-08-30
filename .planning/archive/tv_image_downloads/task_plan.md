# TV image downloads

## Goal

Download and persist local TV series posters, Chinese logos, backdrops, and the
manually linked episode's still image without bloating `app.py` or redownloading
series art for every added episode.

## Phases

- [complete] Confirm current image and TV persistence boundaries.
- [complete] Add baseline schema and migration 003 for TV image paths.
- [complete] Add TMDB image lookup/download helpers and model persistence.
- [complete] Integrate the concise TV import workflow and update tests.
- [complete] Validate with live TMDB assets, back up, and migrate production.
- [complete] Run final regression and database integrity checks.

## Boundaries

- Keep movie image behavior unchanged.
- Download up to five series images per category; posters and logos remain
  Chinese-only, while backdrops may use any language.
- Download only the still for the episode manually linked in the current form.
- Reuse already persisted series art when later episodes of that series are added.
- Store paths relative to Flask's static directory.

## Errors encountered

None yet.
