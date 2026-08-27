# Progress

- Read the full planning skill, project instructions, application, helpers, and tests.
- Counted current module responsibilities and selected the three-module split.
- Moved TMDB/AList access and reusable URL/image utilities into `helpers.py`.
- Added `models.py` for all SQLite connection, transaction, query, and write code.
- Reduced `app.py` from 538 to 92 lines and updated mocks to patch owning modules.
- Passed Python compilation, four workflow tests, GET `/` smoke testing, database
  integrity/foreign-key checks, `git diff --check`, and live AList range validation.
