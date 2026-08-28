# Findings

- Current baseline has 13 movie-related tables and 7 indexes; production
  integrity is `ok` with no foreign-key violations.
- `app.py` currently has only two application functions and should remain an
  orchestration layer.
- Live TMDB TV responses provide series metadata plus season summaries. The
  season endpoint provides season metadata, networks, and full episode records.
- Episode records include ID, show/season/episode numbers, names, overview,
  air date, runtime, production code, episode type, vote data, still path,
  crew, and guest stars.
- TMDB series details also include creators, networks, genres, countries,
  companies, languages, latest/next episode summaries, and season summaries.
- The current UI has one movie-only form and one movie mapping table.
- The balanced schema adds nine TV tables. Scalar fields and shared dimensions
  remain relational; creator/network/runtime and episode crew/guest arrays are
  retained as JSON because they do not duplicate existing shared entities.
- The migration applies cleanly to an in-memory copy of production: nine tables,
  ten indexes, integrity `ok`, and no foreign-key violations.
- TV persistence upserts all season summaries, every episode in the selected
  season, latest/next episode pointers, shared relationships, and only the
  selected episode's AList source.
- The UI now switches between movie and TV episode inputs and lists the two
  relationship types separately.
- A live TMDB import of Breaking Bad season 1 stored six season summaries,
  eight selected-season episodes (including source data), one source mapping,
  and passed integrity and foreign-key checks in a temporary database.
- Production backup `movie-before-tv-20260827-220057-sqlite.db` has SHA-256
  `1619FB1F6BDADF9A143A545DDF2548B0A0CDB25612727D1382BDD0C25A53EC55`.
- Production migration now has nine TV tables and ten TV indexes; integrity is
  `ok`, foreign-key check is empty, and existing four movies/four sources/58
  image rows remain present.
- Cross-media checks prevent one AList virtual path from being associated with
  both a movie and a TV episode, while repeated imports within one media type
  remain idempotent.
- The running Flask development server auto-reloaded successfully and serves the
  new TV form with HTTP 200.
