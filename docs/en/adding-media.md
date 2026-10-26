# Adding media

**English** | [简体中文](../zh-CN/adding-media.md)

There are three ways to add media: **smart discovery** scans your AList folders and proposes matches, the **link editor** lets you check every file against TMDB episodes, and **manual entry** takes an IMDb ID or a JSON file. All of them live under **Media management → Link** (媒体资源管理 → 关联) in the sidebar.

> The UI is in Chinese; labels are given in English with the original in parentheses where it helps.

- [Smart discovery](#smart-discovery)
- [AI recognition](#ai-recognition)
- [Link editor](#link-editor)
- [Manual entry](#manual-entry)
- [Where metadata comes from](#where-metadata-comes-from)
- [How importing works](#how-importing-works)
- [Deleting a title](#deleting-a-title)

## Smart discovery

![Discovered files ready to link](../images/discovery.webp)

The pending list (待关联) scans the AList folders you pick (click **+ Add folder**; if none is picked, MovieDB uses the shared folder it finds automatically). In the background it cleans up disguised file names, searches TMDB and works out seasons and episodes. Results are split into tabs with counts; when one tab is done the next one opens:

- **Ready** (可确认) — confidence ≥ 0.8, and for series every file maps to exactly one TMDB episode. Several sources of the same title are merged into one expandable row. Rows are selected by default; the bottom bar shows how many items and videos are selected, and **Link N items** imports them in one go. Nothing is imported without your click.
- **Review** (待核对) — an inline tag explains why (for example “6/12 episodes matched”, “duplicate of a ready item”, “same-name titles exist”, “low confidence”). **Review** opens the [link editor](#link-editor) prefilled with the match.
- **Unrecognized** (未识别) — neither rules nor AI found a reliable match. Things that look like courses or software are tagged “probably not a video” and can be ignored one by one or all at once.

Each row's **⋯** menu offers *Change title*, *Recognize again* and *Ignore*. Ignored items move to the **Ignored** tab and can be restored at any time.

Recognition order: existing links in the library → folder names you confirmed before → same-name titles in the library → TMDB rule-based search → AI. Confirmed folder names are remembered, so the same name is recognized instantly next time. Results are cached by a signature of the folder contents and are not recomputed unless the contents change.

Each scan also checks files that are already linked. A file is marked **missing** only when AList explicitly says it no longer exists (network errors don't count). The **Overview** tab can filter by missing files; nothing is deleted from your drive.

All of this is stored in `discovery.db` in the data folder (override with `MOVIEDB_DISCOVERY_DATABASE`) and is never included in the cloud snapshot. Once an AList account is configured, MovieDB scans once at startup; turn this off in Settings or with `MOVIEDB_AUTOSCAN=0`.

## AI recognition

After you configure `LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL` (or pick a provider under **Settings → AI**), folders whose rule-based score is below 0.75 are sent to the AI. Its candidates are still verified against TMDB, with a maximum confidence of 0.9.

### Pipeline

Each fully read directory enters recognition immediately while other directories continue scanning. Rules, AI and verification run **as a pipeline**: 16 threads run rule-based searches, uncertain folders are batched for AI, and another pool verifies answers and maps episodes. You can confirm recognized items while scanning continues. Incomplete directories remain available for review.

TMDB results are cached in `discovery.db` (searches 7 days, title summaries 30 days, titles without an IMDb ID 1 day), so re-recognizing after enabling AI, after a rule upgrade, or with *Recognize again* doesn't repeat the searches. When a folder's type is unclear, `/search/multi` finds movies and series in one request.

### Requests

- **Batching** — each request carries up to `LLM_BATCH_SIZE` items (8 for DeepSeek, 20 for others; a batch is sent when full or when its oldest item has waited 1 s). Input and output are JSON: `{"items":[{"id":"1","name":…,"samples":[…],"rules":[…]}]}` → `{"items":[{"id":"1","kind":"movie|tv|other","candidates":[…]}]}`. *Recognize again* on a single item uses the same format.
- **Cache-friendly prompts** — batches and single items share one fixed system prompt at the start of the request (`instructions` in the Responses API). DeepSeek's context cache matches on exact prefixes, and cached input costs about 1/50 of uncached input.
- **Retries** — truncated output and malformed JSON split the batch. Service and network failures retry the original batch up to three times, then stop further AI requests. Missing answers are batched for another request.
- **Circuit breaker and fallback** — an invalid key (401), insufficient balance (402) or bad parameters (400/422) stop AI requests for the current scan immediately. Rate limits (429/503) back off according to `Retry-After` and stop if they don't recover. Failed and skipped items fall back to rule results and are cached as “AI not used”, so the next scan retries them. The end-of-scan message lists the failure reason and count, plus request count, token usage and cache hit rate.
- **Protocol** — `LLM_API` selects `responses` (`/responses`) or `chat` (`/chat/completions`); both ask for JSON object output. On non-DeepSeek providers without JSON mode, MovieDB falls back to plain prompting.

**Check** in the settings page sends one tiny real JSON request, which catches problems such as an empty balance or a wrong model name.

### DeepSeek (recommended)

`LLM_BASE_URL=https://api.deepseek.com`, `LLM_MODEL=deepseek-flash`. Handled specially, following the official documentation:

- Uses the Responses API by default with thinking disabled (`reasoning.effort=none` for Responses, `thinking.type=disabled` for Chat; change with `LLM_THINKING`).
- DeepSeek has no per-minute limit, only a per-account concurrency limit (2500 for deepseek-flash, 500 for deepseek-v4-pro). MovieDB therefore keeps 64 requests in flight by default (`LLM_CONCURRENCY`, up to 500) and ignores `LLM_RPM`; on 429 it halves concurrency and ramps back up after consecutive successes.
- A trailing `/v1` from older configurations is removed automatically.

Rough cost: 1000 folders that need AI take about 50 requests, ~200k input tokens and ~80k output tokens — about US$0.10–0.20 at deepseek-flash prices (roughly half off-peak).

## Link editor

![Link editor with filename rules](../images/association.webp)

Open a pending item to get the link editor:

- **Title card** — poster, year, type and season/episode count. **TMDb↗** opens the TMDB page for a quick check; **Change** searches for another title.
- **File table** — for series each row shows the season/episode and the matching TMDB episode. Rows with problems (missing number, no such episode, needs checking) are highlighted in yellow; **Only problems** filters them, and the *Episode* drop-down lets you pick one directly. Several files for the same episode are linked as versions.
- **Filename rules** — collapsed by default, opened automatically when some files have no readable season/episode or look like continuous numbering.
  - The sample file name is split into segments; click a number to make it the episode or season and a rule is generated (you can switch samples; unreadable files come first).
  - The rule is shown as one line of segments like `…S〔season〕E〔episode〕…`, highlighting the matching part of the sample on hover. The line is the editor: click a segment to change its type (season, episode, literal text, number…), edit literal text in place, move or delete segments, add one with **+** at the end, or drag to reorder (Alt + arrow keys also work). A missing episode segment is flagged in place. Common formats can be applied with one click.
  - The season can come from the file name, the folder name (such as “第二季” or “Season 2”) or a fixed value; with several folders you can set the season and an episode offset per folder. If every season folder starts at 01 but TMDB has only one season, MovieDB suggests continuous numbering and can shift it in one click (e.g. 01–06 in the second folder become episodes 7–12 of season 1).
  - The footer summarizes the file count, whether every season/episode was read and whether TMDB has them all; click an issue to jump to it.

The bottom bar shows whether everything matches. **Link N** submits durable background jobs and returns to the list. Titles run concurrently and episodes share the probe pool. The status bar shows committed files, completed probes and active titles, and offers a stop action. Jobs continue after the page closes and resume after an application restart. Each server chunk of at most 100 files is atomic; successful chunks remain committed if a later chunk fails.

## Manual entry

The **⋯** menu at the top right of the pending list has *Manual entry*, *Upload JSON* and *JSON template*.

- **By IMDb ID** — add a movie or an episode. A series ID needs a season and episode number; an episode ID resolves its parent series automatically. Episodes without an IMDb ID on TMDB can't be added.
- **Search TMDB** — search by name → pick a movie or series → for a series, enter season, episode and AList video URL per row (add or remove rows); for a movie, enter one URL → add everything to the import list. Season and episode numbers are validated against TMDB on import. Adding only creates rows on the page and never overwrites existing data; the first addition replaces the single empty placeholder row. Finish with **Validate and import**.
- **Upload JSON** — bulk import following the template; if any entry fails, the whole batch is rolled back.

Searches and IMDb mappings are cached in memory for 10 minutes (up to 2048 entries) and identical concurrent requests are merged; the chosen match is reused on import. Cast and final artwork are fetched only on import, and candidate posters load in the browser on demand. If TMDB is unavailable you can still enter an IMDb ID; when an IMDb mapping or local data is missing, MovieDB tells you why.

## Where metadata comes from

- **TMDB** — title, year, genres, season/episode structure and episode IMDb IDs, overview, cast and images. Chinese titles fall back zh-CN → zh-TW → zh-HK; genres are converted to IMDb's English genres (e.g. Sci-Fi & Fantasy → Sci-Fi, Fantasy).
- **IMDb ratings** — from the official `title.ratings.tsv.gz` file (about 9 MB), downloaded to `imdb/` in the data folder on first import and refreshed when older than a day; override the location with `IMDB_RATINGS_PATH`. **Refresh ratings** on the Overview tab updates IMDb ratings for every title and episode from this file.
- **MDBList** (optional) — Rotten Tomatoes, Metacritic, Letterboxd, Trakt and more.

When you re-import an existing title, names, genres and season/episode relations already in the library are kept (only placeholder names like “Episode N” are replaced), and existing ratings are kept if new ones can't be fetched.

## How importing works

Confirmed jobs are scheduled by root IMDb title ID, with four titles preparing core associations by default. Committed titles continue enrichment in separate resource pools, freeing preparation slots. The total active window is four times the preparation concurrency, capped at 32 titles. Bounded process-wide pools prevent overlapping imports from multiplying concurrency. Each file obtains its AList signed and raw URLs immediately before probing; expired links refresh once. Probe checkpoints survive restarts, while signed URLs and credentials are never stored in jobs.

Core associations commit as soon as required metadata and probes are ready. Artwork, scores, thumbnails and tones run alongside them, retrying unavailable services at their recovery time for up to three rounds. Images are processed as they arrive, shared actor photos download once, thumbnails and tones share a decode, and only selected episodes' images are prepared. A local SQLite index of each IMDb ratings-file version avoids repeatedly decompressing it for separate titles. Manual JSON imports retain their batch atomicity.

Jobs and probe checkpoints live in `discovery.db`; request budgets and cached MDBList scores live in `api-usage.db` in the data folder. Neither is included in cloud snapshots. Duplicate submissions reuse jobs; deleting a title cancels unfinished jobs before they can write it back. Resource pools and short write transactions are shared within the desktop application's process; a multi-process deployment needs a single import dispatcher. See [Configuration → Rate limits](configuration.md#rate-limits).

## Deleting a title

**Delete title** at the bottom of a detail page first shows exactly what will be removed: overview, ratings, every season and episode, local images and watch history. Images and cast photos can be scrolled sideways and are tagged *delete* or *shared, kept*; watch history shows the time, position and file length. The original videos on AList and the collections themselves are kept. If the scope changes or more than five minutes pass, MovieDB checks again before deleting; afterwards you return to Media management with a confirmation.
