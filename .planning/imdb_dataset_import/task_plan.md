# IMDb dataset import plan

## Goal

Import the IMDb TSV datasets from `imdb/` into a new SQLite database `imdb.db`
at the project root. One table per TSV, natural keys instead of SQLite's
implicit rowid, targeted indexes for lookup, and a filtered Chinese/English
subset for `title.akas`.

## Phases

- [complete] Explore the TSV files: headers, sizes, and akas language/region distribution.
- [complete] Write `scripts/import_imdb.py`: streaming TSV reader, batched inserts, `\N` → NULL.
- [complete] Create tables with explicit primary keys and `WITHOUT ROWID` clustering.
- [complete] Bulk-load data with batch import pragmas (`journal_mode=OFF`, `synchronous=OFF`, large cache).
- [complete] Create indexes after the data load.
- [complete] Verify row counts against source files and spot-check known titles (e.g. `tt0111161`).

## Boundaries

- Database file: `imdb.db` at project root, separate from `movie.db`.
- `title.principals.tsv` is deferred and not imported in this round.
- `title.akas` keeps only rows where `language` starts with `zh`/`en` or
  `region` is CN/TW/HK/MO/US/GB/AU/CA/NZ/IE; original-title rows are not
  retained separately.
- Multi-value fields (`directors`, `writers`, `knownForTitles`, `genres`) stay
  as raw TEXT; no lossy normalization.
- Source TSV files are read-only inputs; the script must not modify them.

## Schema

| Table | Source file | Primary key | Extra indexes |
|---|---|---|---|
| `name_basics` | name.basics.tsv | `nconst` | `primaryName` |
| `title_basics` | title.basics.tsv | `tconst` | `primaryTitle`, `titleType` |
| `title_akas` | title.akas.tsv (filtered) | `(titleId, ordering)` | `title` |
| `title_crew` | title.crew.tsv | `tconst` | — |
| `title_episode` | title.episode.tsv | `tconst` | `(parentTconst, seasonNumber, episodeNumber)` |
| `title_ratings` | title.ratings.tsv | `tconst` | — |

## Errors encountered

- (none yet)
