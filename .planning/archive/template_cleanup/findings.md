# Findings

- `templates/` currently contains 11 files.
- Direct Flask rendering currently names `index.html`, `library.html`, `top.html`, `genre_page.html`, `favorites_page.html`, `detail.html`, and one template below the initially truncated `app.py` output.
- All page templates extend `layout.html`; several import `_media_cards.html`.
- Confirmed unused legacy files: `library_page.html` and `top_rated_page.html`.
- `library.html` serves both recent-added and unwatched routes; `top.html` serves top-rated. No Python, Jinja, JavaScript, test, or loader API refers to the two legacy names.
- Both legacy files contain visibly corrupted old Chinese markup, further confirming they are superseded artifacts rather than active views.
