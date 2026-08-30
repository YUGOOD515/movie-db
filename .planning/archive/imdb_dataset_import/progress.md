# Progress

- Listed all TSV files under `imdb/` and inspected headers plus sample rows of
  each dataset.
- Verified the toolchain: uv 0.11.8 and the project `.venv` on Python 3.13.9;
  confirmed ~234 GB free disk space.
- Ran a full streaming scan of `title.akas.tsv` (59.1M rows) to measure the
  Chinese/English subset: language-only filter keeps 3.7% (0.11 GB), the chosen
  language-or-region filter keeps 9.6% (0.28 GB, saving ~2.51 GB).
- Agreed scope with the user: lowercase `imdb.db`, skip `title.principals.tsv`
  for now, filter `title.akas` by language or English/Chinese region without
  retaining original-title rows; `title.basics.tsv` added and included.
- Recorded the plan, findings, and schema in this planning directory.
- Implemented `scripts/import_imdb.py`: streaming TSV reader with `\N` → NULL
  conversion, natural-key `WITHOUT ROWID` tables, batched inserts with
  bulk-import pragmas, akas language/region filtering, post-load index builds,
  and row-count plus spot-check verification; supports `--tables` partial runs.
- Smoke-tested on `title_ratings`: 1,710,605 rows imported and verified
  (`tt0111161` → 9.3 rating / 3,230,054 votes); fixed verify() to only touch
  tables included in a partial run.
- Started the full import of all six tables into `imdb.db`.
- Full import completed in ~4.5 minutes with zero dropped rows; final database
  is 4.87 GB, `PRAGMA integrity_check` returns `ok`, all five secondary
  indexes plus `sqlite_stat1` (ANALYZE) present.
- Verified spot checks: tt0111161 basics/rating/crew, nm0000001 person data,
  Breaking Bad episode ordering via the parent index, and Chinese akas rows.
- Confirmed that `title_basics` holds no Chinese-character titles; Chinese
  titles are served exclusively by `title_akas` (documented in findings).
