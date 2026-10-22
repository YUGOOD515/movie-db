# Development

**English** | [简体中文](../zh-CN/development.md)

- [Run from source](#run-from-source)
- [Project layout](#project-layout)
- [Tests](#tests)
- [Offline test API](#offline-test-api)
- [Building installers](#building-installers)
- [CI and releases](#ci-and-releases)
- [Front-end conventions](#front-end-conventions)

## Run from source

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/). When running from source the data folder is the repository root (`library.db`, `.env` and so on); point `MOVIEDB_DATA_DIR` elsewhere if you prefer.

```sh
uv sync
uv run --with pystray --with pywebview python desktop.py   # desktop mode: native window + tray
uv run --with pystray python desktop.py --browser          # old style: system browser + tray / menu bar
uv run python desktop.py                  # without pywebview / pystray: browser + console, Ctrl+C to quit
uv run flask --app app run                # or the plain Flask development server
```

`desktop.py` accepts `--port`, `--browser` (use the system browser, same as `MOVIEDB_UI=browser`), `--no-browser` (server only), `--no-tray` and `--self-test` (open the window, check the page and bridge, then quit; used by CI). Without a `library.db` the first-run walkthrough opens; you can also write `.env` by hand following `.env.example`.

**macOS**: `brew install uv ffmpeg`, then a player (we recommend [IINA](https://iina.io): `brew install --cask iina`, or `brew install mpv`); everything else works as on Windows. Run the macOS build of AList yourself, or put its folder in `ALIST_DIR` and MovieDB will start it.

## Project layout

| Path | Contents |
|---|---|
| `app.py` | Flask routes |
| `desktop.py` | Desktop entry point: server, tray / menu bar, single instance, restart |
| `desktop_window.py` | Native window (pywebview): hide on close, remembered geometry, light / dark title bar, external links and file pickers, macOS menus |
| `queries/` | Read-only queries (pages only read `library.db`) |
| `models/` | Writes: `library.py` (library) and `discovery_store.py` (`discovery.db`) |
| `viewmodels/` | Page view models |
| `services/` | Import, linking, recognition, playback, deletion, settings, data folder… |
| `connectors/` | External services: TMDB, AList, MDBList, IMDb ratings, AI, ffprobe, players, throttling |
| `templates/`, `static/` | Jinja templates, styles and per-page scripts; third-party libraries in `static/vendor/` |
| `migrations/` | Database schema; `library_schema.sql` is the current one |
| `scripts/` | Command-line tools: thumbnails, ratings, snapshot publishing, `.env` check… |
| `packaging/` | Build scripts for the Windows / macOS installers and the slim ffprobe |
| `cloud/` | Cloudflare Workers for the read-only snapshot |
| `archive/` | Historical material no longer used at runtime |

Coding conventions are in [AGENTS.MD](../../AGENTS.MD) (Chinese).

## Tests

```sh
uv run --with pytest python -m pytest -q
```

`uv run python scripts/check_env.py` compares the variable names in `.env` and `.env.example` after an update.

## Offline test API

A local replay server serves Chinese TMDB data and MDBList ratings for *Breaking Bad* only (IMDb `tt0903747` / TMDB `1396`) and never calls remote APIs. Search accepts Simplified / Traditional Chinese, English and partial names; details, `append_to_response`, seasons and episodes, IMDb mappings and the images used by search and import are supported. Anything else returns empty results or 404.

The snapshot lives in `local-test-data/breaking-bad/` (ignored by Git), separate from the library and `static/images`, so deleting the title in the app doesn't touch it. It contains real API responses, the related library rows, stills / posters / backdrops / logos / cast photos, an image URL map and a SHA-256 manifest. Episode `find` responses are assembled from the saved season data and episode `external_ids`; older snapshots without translations replay as untranslated. AList videos are not copied.

Run the local API in one terminal:

```sh
uv run python scripts/local_test_api.py
```

Stop any running MovieDB, then start it with three addresses replaced (PowerShell example):

```powershell
$env:TMDB_API_ORIGIN = "http://127.0.0.1:5001/3"
$env:TMDB_IMAGE_ORIGIN = "http://127.0.0.1:5001/t/p"
$env:MDBLIST_API_URL = "http://127.0.0.1:5001/mdblist/api/"
uv run flask --app app run --host 127.0.0.1 --port 5000
```

- Existing tokens and keys can stay; the local server ignores credentials. Without any, set `TMDB_TOKEN` and `MDBLIST_APIKEY` to `local-test`.
- AList works as usual; the IMDb ratings file still comes from the official site.
- Set the variables in the process that actually starts MovieDB and restart it to clear caches — editing `.env` alone does not replace the TMDB addresses. Remove the three variables and restart to go back to the real services.

To capture a fresh snapshot, run `uv run python scripts/capture_breaking_bad.py --output local-test-data/breaking-bad-new` and start the replay server with `--data local-test-data/breaking-bad-new`. Capturing calls the remote APIs but never writes to the library; existing snapshot folders are never overwritten.

## Building installers

### Windows

```sh
uv run python packaging/build_windows.py
```

Downloads the embeddable Python, installs dependencies for Windows and copies the app files tracked by Git (no tests or development material), then removes unused parts with `packaging/slim.py` (Pillow's AVIF / FreeType extensions, pywebview backends for other platforms). The launcher `runtime\python\MovieDB.exe` is `pythonw.exe` with the MovieDB icon and name (via rcedit). `packaging/check_runtime.py` verifies imports, thumbnails and window components after packaging. With `makensis` (NSIS 3) available it builds the installer, otherwise a portable zip (unzip and run `MovieDB.cmd`). Works on Linux too.

### macOS

```sh
uv run python packaging/build_macos.py [--arch arm64|x86_64] [--ffprobe-dir <dir>]
```

Produces `dist/MovieDB-<version>-macOS-<arch>.dmg`. The script downloads Python from [python-build-standalone](https://github.com/astral-sh/python-build-standalone), installs dependencies for macOS (keeping only the target architecture of universal binaries), precompiles bytecode, builds the launcher from `packaging/macos/launcher.c` (Python lives in `Contents/MacOS/MovieDB-python` so the process belongs to MovieDB.app), signs ad hoc and packs an LZMA-compressed `.dmg`. Apple silicon Macs can build both architectures. The app never writes inside the `.app` at runtime (so the signature stays valid); all data goes to the data folder.

### ffprobe

Both scripts offer three options:

- `--ffprobe-dir <dir>` (used by CI): a **slim build** made with `packaging/build_ffprobe.sh`. It keeps only the file / http / https protocols, common containers (mkv/webm, mp4/mov, ts/m2ts, avi, flv, wmv, mpg, ogg, rmvb) and common audio and video codecs — a static single file of about 8 MB, still LGPL.
  - Windows: `bash packaging/build_ffprobe.sh <FFmpeg source> <output dir> win64` (Linux + mingw-w64); https uses the system Schannel.
  - macOS: targets `macos-arm64` / `macos-x86_64` (build on a Mac); https uses the system Secure Transport.
  - CI generates test clips with a full ffmpeg (H.264 / HEVC 10-bit HDR10 / AV1 / VP9 / MPEG-2 / MPEG-4 / WMV / RV; AAC / EAC3 / DTS / TrueHD / FLAC / Opus / PCM and more) and compares the slim and full builds field by field over local HTTPS with Range support (`packaging/check_ffprobe.py`).
- Nothing: downloads BtbN's full LGPL build (about 160 MB).
- `--no-ffprobe`: bundle none; point `FFPROBE_PATH` at your own ffprobe.

Rare formats the slim build doesn't know only lose their quality info; importing still works. Use `FFPROBE_PATH` to switch to a full build if you need it.

## CI and releases

`.github/workflows/windows-package.yml` (“安装包（Windows / macOS）”) runs on pushes to `main`, `release/**`, `feat/**` and `perf/**` that touch program files. It builds and verifies the slim ffprobe, builds the Windows installer and portable zip and the macOS `.dmg` for both architectures, and tests installation, the portable data location, migration of old data and the `.dmg` signature. Build artifacts of any branch can be downloaded at the bottom of the run page (GitHub sign-in required).

On `main`, if no release `v<version from pyproject.toml>` exists yet, one is created with the installers attached; its notes are the `## v<version>` section of [CHANGELOG.md](../../CHANGELOG.md). To release: bump the version in `pyproject.toml`, rename the CHANGELOG's “未发布” (Unreleased) section to that version, and merge into `main`.

## Front-end conventions

- The home page opens with a “Latest additions” carousel (Embla) with a slow Ken Burns background; the page tint follows the current title.
- Posters and backdrops show a BlurHash placeholder before fading in. The color table `static/thumbnails/tones.json` is generated on import and filled in for older images at startup (skip with `MOVIEDB_TONES_DISABLED=1`).
- On desktop, posters tilt on hover, lists fade in as they enter the viewport, and cards morph into detail pages with shared-element View Transitions (Chrome / Edge / Safari 18+). Everything is disabled when the system asks for reduced motion.
- Third-party libraries (Embla Carousel, Motion, vanilla-tilt, BlurHash — all MIT) are vendored in `static/vendor/`; no CDN.
- Media management uses one bottom status bar (`static/status-bar.js`), centered at the bottom of the viewport; the page reserves space for it and drop-downs open above it. The upper layer shows background tasks (AList scan, rating refresh, link results), the lower layer the current view's actions. Presets: `selection` (N selected + bulk actions), `check` (validation + submit), `progress` (determinate / indeterminate), `result` (success / warning / error, auto-dismiss, paused on hover), `notice` (suggested action) and `pending` (unsaved changes: discard / save). Update with `StatusBar.<preset>(key, …)` and dismiss with `StatusBar.clear(key)`. Outer radius = inner control radius + padding + border (2rem pill controls, 0.5rem padding), so the curves are concentric.
- Dialogs in media management (pick a title, scan folders, delete a title) are centered and fade in with a slight zoom. The title picker has a fixed height and searches the prefilled name when opened from the pending list. After trying another image on an overview card, the card shows “unsaved” and the status bar offers discard / save (several titles at once), with a warning before leaving the page.
- Corners use superellipses (`corner-shape: squircle`). A child that inherits its parent's radius must also set `corner-shape: inherit` and subtract the parent's border width from the radius, or the hover background shows gaps in the corners.
