# Findings

- `imdb/` holds seven IMDb-format TSV datasets; `title.basics.tsv` was added by
  the user on 2026-08-29 after the initial exploration.
- File sizes: name.basics 920 MB, title.akas 2,855 MB, title.basics 1,055 MB,
  title.crew 400 MB, title.episode 247 MB, title.principals 4,326 MB,
  title.ratings 28 MB.
- Environment: uv 0.11.8 with a project `.venv` on Python 3.13.9; C drive has
  ~234 GB free, enough for the ~10–15 GB database.
- `title.akas.tsv` has 59,120,646 rows (2.79 GB of data). Most rows carry a
  populated `region` but an empty `language`.
- Language distribution is skewed: en = 2,209,043 rows, cmn = 53,240,
  yue = 11,070; major European/Asian languages each hold ~5.3M rows.
- Region filter for English (US/GB/CA/AU/NZ/IE) adds ~3.4M rows whose
  `language` is empty, so the combined language-or-region filter is required
  for full English coverage.
- Filter outcome (language starts with zh/en OR region in CN/TW/HK/MO/US/GB/
  AU/CA/NZ/IE): keep 5,646,104 rows / 0.28 GB (10.1%), saving ~2.51 GB (89.9%)
  versus the full file. Chinese aliases are rare in the source either way.
- `title.basics.tsv` columns: tconst, titleType, primaryTitle, originalTitle,
  isAdult, startYear, endYear, runtimeMinutes, genres (comma-separated).
- Multi-value columns (`directors`, `writers`, `knownForTitles`, `genres`) are
  comma-separated strings; kept as raw TEXT to avoid lossy decomposition.
- `\N` is the dataset's null marker and maps to SQL NULL.
- Import result: all six tables loaded in ~4.5 minutes with zero dropped rows;
  `imdb.db` is 4.87 GB and passes `PRAGMA integrity_check`.
- Row counts: name_basics 15,608,805; title_basics 12,748,442; title_akas
  5,646,104; title_crew 12,749,320; title_episode 9,856,134; title_ratings
  1,710,605.
- Important data characteristic: `title_basics` stores English/romanized titles
  only (e.g. tt0106332 = "Farewell My Concubine" / "Ba wang bie ji"); fewer
  than a dozen rows start with Chinese characters. Chinese titles live in
  `title_akas`, so any Chinese-title search or display must join that table.
