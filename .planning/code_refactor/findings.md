# Findings

- `app.py` is 538 lines and currently mixes Flask routing, AList URL parsing,
  signed-link validation, image downloading, SQLite connection management,
  normalized persistence, and presentation queries.
- `helpers.py` is 167 lines and already owns TMDB/AList requests, but repeats
  request headers and low-level request construction.
- The clean responsibility split is: `helpers.py` for external connections and
  reusable URL/file utilities, `models.py` for SQLite operations, and `app.py`
  for request validation, workflow orchestration, flashes, redirects, and render.
- Existing tests patch symbols through `app`; they must be redirected to the
  owning helper module while preserving full POST coverage.
- Final responsibility split: `app.py` has 92 lines and only two application
  functions; `helpers.py` owns network, URL, file download, and player helpers;
  `models.py` owns connections, transactions, persistence, and mapping queries.
- The production database contains two existing movies. Read-only verification
  returned `integrity_check = ok` and no foreign-key violations.
- The supplied signed AList URL still passes a one-byte range validation after
  the refactor and is normalized to an unsigned virtual path.
