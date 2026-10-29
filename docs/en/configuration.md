# Configuration

**English** | [简体中文](../zh-CN/configuration.md)

- [Settings page](#settings-page)
- [Wiping data](#wiping-data)
- [Data folder](#data-folder)
- [Environment variables](#environment-variables)
- [Players](#players)
- [Rate limits](#rate-limits)

## Settings page

![Settings page](../images/settings.webp)

Once the library exists, **Manage → Settings** (管理 → 设置, `/settings`) has four tabs:

| Tab | Contents |
|---|---|
| Connection (连接) | Drives, pending-file scanning, player, ffprobe, cloud read-only snapshot, local port |
| Metadata (元数据) | TMDB, MDBList, IMDb ratings |
| AI | Provider, model, endpoint, concurrency and more |
| Data (数据) | Wipe local data |

Each group shows its status and has its own **Check** (检测) button; less common options are under **Advanced** (高级). Save or discard changes in the bar at the bottom.

- Saved passwords and keys are never shown again — leave a field empty to keep it. Optional keys can be cleared individually. Turning AI recognition off also clears its endpoint, model and API key.
- Settings are written to `.env` in the data folder (existing comments and other variables are preserved) and take effect immediately, except the port (`MOVIEDB_PORT`) and the interface mode (`MOVIEDB_UI`). After changing either, the desktop app can restart itself from the settings page or the tray menu (**Restart MovieDB**); other ways of running need a manual restart.
- Settings can only be changed on the computer running MovieDB: they are read-only over a Cloudflare Tunnel and not available in the cloud snapshot.
- Before a library exists (no `library.db`), `/setup` shows the first-run walkthrough; afterwards it redirects to the settings page.

## Wiping data

**Settings → Data** deletes local data and returns to the first-run walkthrough. You can choose:

- the library (`library.db` — links, watch progress and collections)
- the recognition cache and scan records (`local.db`)
- posters and thumbnails (`static/images`, `static/thumbnails`)
- connections and keys (`.env`)

Everything is selected by default. Type the confirmation word (清除) to proceed; **no backup is made**. With the cloud snapshot enabled, wiping the library first publishes an empty snapshot so the cloud copy is cleared as well. Wiping is blocked while a scan is running; on Windows, files in use ask you to restart and try again.

## Data folder

The library, settings, artwork cache and logs (`library.db`, `local.db`, `.env`, `static/images`, `static/thumbnails`, `imdb/`, `logs/`) all live in the data folder:

| How you run MovieDB | Data folder |
|---|---|
| Windows installer | `%LOCALAPPDATA%\MovieDB` |
| macOS | `~/Library/Application Support/MovieDB` |
| Windows portable zip | `app\` inside the unzipped folder (marked by a `portable` file) |
| From source | the repository root |

- Set `MOVIEDB_DATA_DIR` to use another location (for example to share one library between a source checkout and the installed app).
- Program and data are separate, so upgrades only replace program files. Version 0.2 and earlier kept data in the install folder's `app\`; newer versions move it to `%LOCALAPPDATA%\MovieDB` on first launch (if the old version is still running and files are locked, the old location is used once more and the move happens next time).
- Uninstalling removes program files only, unless you choose to delete the data too.

## Environment variables

The settings page covers almost everything. You can also edit `.env` in the data folder (see [`.env.example`](../../.env.example) for names) or set variables in the process that starts MovieDB. When running from source, `.env` is loaded automatically by python-dotenv.

**General**

| Variable | Description |
|---|---|
| `MOVIEDB_PORT` | Local port, default `5000`; requires a restart. If it is not set and 5000 is taken by another program (on macOS usually the AirPlay Receiver), the desktop app picks the first free port in 5050–5059 and saves it here |
| `MOVIEDB_UI` | How the desktop app shows its interface: empty for its own window, `browser` for the system browser; requires a restart |
| `MOVIEDB_DATA_DIR` | Data folder; defaults are listed [above](#data-folder) |
| `MOVIEDB_LOCAL_DATABASE` | Location of `local.db`; defaults to the folder of `library.db` |
| `FLASK_SECRET_KEY` | Session secret; generated during first-run setup |
| `FLASK_PROFILING` | Set to `1` to enable profiling |

**Drives**

Mounts and credentials are managed under **Manage → Drives** and stored in `local.db`; `ALIST_*` variables are no longer used. Legacy AList values are only read once to create the passthrough mount, without changing `.env`.

| Variable | Description |
|---|---|
| `DRIVE_RATE`, `DRIVE_CONCURRENCY` | Drive API requests per second (default 20) and in-flight requests (default 4); lower them if your drive throttles. Aliyun and 123 have lower service-specific limits |
| `MEDIA_IMPORT_ROOT` | Only allow imports below this folder; also the default scan folder when none is chosen. When empty, MovieDB looks for a “来自：分享” (shared) folder. Legacy `ALIST_IMPORT_ROOT` is still read |

**Metadata**

| Variable | Description |
|---|---|
| `TMDB_TOKEN` | Your own TMDB key — an API Read Access Token or a 32-character API key. Leave empty to use the key built into the installers; running from source has no built-in key, so set one |
| `TMDB_PROXY` | HTTP proxy used for TMDB only, e.g. `http://127.0.0.1:7890` |
| `TMDB_API_ORIGIN`, `TMDB_IMAGE_ORIGIN` | Reverse proxy or mirror, defaults `https://api.themoviedb.org/3` and `https://image.tmdb.org/t/p`. With no API origin set, MovieDB switches to the official `api.tmdb.org` domain when the main one is unreachable; if posters don't load, set the image origin to the public relay `https://wsrv.nl/?url=https://image.tmdb.org/t/p` (third-party) |
| `MDBLIST_APIKEY` | MDBList API key for Rotten Tomatoes, Metacritic and other ratings |
| `DOUBAN_ENABLED` | Fetch Douban ratings on import and rating refresh (default on, `0` turns it off). Uses Douban's unofficial app API, which may stop working; when Douban refuses requests MovieDB pauses for 30 minutes |
| `IMDB_RATINGS_PATH` | Location of the official IMDb ratings file, default `imdb/` in the data folder |

**AI recognition** (any OpenAI-compatible API, see [Adding media → AI recognition](adding-media.md#ai-recognition))

| Variable | Description |
|---|---|
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | Endpoint, key and model; `https://api.deepseek.com` + `deepseek-flash` recommended |
| `LLM_API` | `responses` or `chat`; defaults to `responses` for DeepSeek and `chat` for others |
| `LLM_THINKING` | Thinking mode `none` (default) / `low` / `high` / `max`, DeepSeek only |
| `LLM_BATCH_SIZE` | Items per request (1–50); 8 for DeepSeek, 20 for others |
| `LLM_CONCURRENCY` | Requests in flight (1–500); 64 for DeepSeek, 2 for others |
| `LLM_RPM` | Requests per minute, default 10 (ignored for DeepSeek) |

**Scanning, media info and playback**

| Variable | Description |
|---|---|
| `MOVIEDB_AUTOSCAN` | Scan the chosen folders in the background at startup, default `1`; `0` turns it off |
| `FFPROBE_PATH` | Path to ffprobe; the installers bundle a slim build, which you can replace with a full one |
| `FFPROBE_TIMEOUT` | Per-file timeout in seconds, default 60 |
| `IMPORT_TITLE_CONCURRENCY` | Concurrent core preparations, default 4, range 1–16; enrichment uses separate resource pools |
| `FFPROBE_CONCURRENCY` | Total ffprobe processes shared by all imports, default 3, range 1–8 |
| `FFPROBE_PER_SOURCE` | Probe concurrency per first-level mount directory, defaults to the global cap |
| `IMAGE_CONCURRENCY` | Shared thumbnail/tone processing concurrency, default 2 |
| `PLAYER` | `mpc-hc` / `iina` / `mpv` / `vlc` / `system`; empty picks automatically |
| `PLAYER_PATH` | Program path for players other than MPC-HC; on macOS this can be an `.app` |
| `MPC_PATH`, `MPC_WEB_URL` | MPC-HC program path and web interface (default `http://127.0.0.1:13579`) |
| `MOVIEDB_TONES_DISABLED` | Set to `1` to skip computing artwork colors and BlurHash placeholders at startup |

**Cloud read-only snapshot** (see [Remote access](remote-access.md))

| Variable | Description |
|---|---|
| `MOVIEDB_SYNC_TOKEN` | Sync token; enables automatic sync |
| `MOVIEDB_SYNC_URL` | Address of the router Worker |
| `MOVIEDB_SYNC_DISABLED` | Set to `1` to pause automatic sync |

After updating the code, `uv run python scripts/check_env.py` compares `.env` with `.env.example`: it compares names only (never prints values) and lists missing, unknown, duplicated and unset optional variables.

> Never put TMDB or drive credentials in source code or commit them to Git. Rotate any credential that has ever entered the Git history. MovieDB no longer starts AList; keep the existing service running when using AList / OpenList passthrough.

## Players

**Play** opens MovieDB's `/stream` URL in a local player, and MovieDB redirects or proxies the drive content. Choose a player under **Settings → Connection → Player**; when empty, MovieDB picks the first one installed (Windows: MPC-HC → mpv → VLC; macOS: IINA → mpv → VLC).

| Player | Platform | Watch progress |
|---|---|---|
| MPC-HC | Windows (default) | Recorded and resumed; enable the web interface in MPC-HC *Options → Web Interface* |
| IINA | macOS (default) | Recorded and resumed (via mpv's JSON IPC) |
| mpv | All | Recorded and resumed (resume only on Windows) |
| VLC, system default app | All | Opens only, no progress |

If no player is found, the page points you to the settings instead of failing.

## Rate limits

Free API tiers are limited, so every external request goes through `connectors/throttle.py`: one token bucket per service (average rate) plus an adaptive concurrency cap (halved on 429/503, restored step by step after consecutive successes). On 429/503 the whole service pauses according to the server's `Retry-After`, `X-RateLimit-Reset` or Gemini's `retryDelay`, otherwise exponential backoff with jitter. If the required wait exceeds the cap (usually a daily quota), requests stop queueing: the service is paused until it recovers and requests degrade gracefully in the meantime.

| Service | Known limits | Default | Tune with |
| --- | --- | --- | --- |
| TMDB API | Service-dependent limits; retain headroom | 25/s by default, 16 concurrent, no accumulated bursts; image CDN separately, 6 concurrent | `TMDB_RATE` (maximum 30) |
| Drives and AList passthrough | Each drive has its own throttling (“too many requests”) | 20/s, 4 concurrent by default, with backoff on throttling errors. Aliyun and 123 use lower service-specific rates | `DRIVE_RATE`, `DRIVE_CONCURRENCY` |
| AI (DeepSeek) | No per-minute limit; per-account concurrency (flash 2500, v4-pro 500), 429 beyond that | 64 concurrent (halved on 429), up to 8 items per batch | `LLM_CONCURRENCY`, `LLM_BATCH_SIZE` |
| AI (other OpenAI-compatible) | Free tiers usually allow a dozen to a few dozen requests per minute, tens to thousands per day | 10 per minute, 2 concurrent, up to 20 items per batch | `LLM_RPM`, `LLM_CONCURRENCY`, `LLM_BATCH_SIZE` |
| MDBList | Free tier: 1000 requests per day, at most 1000 reads per 5 minutes | 2/s, 2 concurrent; rolling 24-hour budget of 900 including retries; successful scores cached for 6 hours | `MDBLIST_DAILY_BUDGET`, `MDBLIST_RATE` (maximum 2) |
| ffprobe | Each process reads file headers from the drive | 3 processes shared across titles, with an optional per-source cap | `FFPROBE_CONCURRENCY`, `FFPROBE_PER_SOURCE` |

MDBList usage and cache are persisted in `local.db` in the data directory (`MOVIEDB_LOCAL_DATABASE` overrides it). Accounts are distinguished by credential hashes; keys are not stored. Configure the budget for your plan and leave room for other clients using the same account. Queued requests recheck service cooldowns, streaming responses retain their slots until consumed, and live settings changes preserve in-flight requests and cooldowns.

When the AI quota runs out, recognition falls back to rule results and says so in the scan message. Those results are cached as “AI not used”, so a later scan fills them in once the quota is back; fallback results are never treated as final.
