# Remote access

**English** | [简体中文](../zh-CN/remote-access.md)

MovieDB listens on `127.0.0.1` only. To use it from other devices, a Cloudflare Tunnel can forward a domain to your computer securely; add the cloud read-only snapshot and the library stays browsable while your computer is offline. Both fit in Cloudflare's free tier.

- [Cloudflare Tunnel](#cloudflare-tunnel)
- [Cloud read-only snapshot](#cloud-read-only-snapshot)

## Cloudflare Tunnel

You need a domain on Cloudflare and [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/). Replace `library.example.com` with your own hostname.

1. Create a tunnel and point a hostname at it:

   ```sh
   cloudflared tunnel login
   cloudflared tunnel create moviedb
   cloudflared tunnel route dns moviedb library.example.com
   ```

2. Write a config file (for example `~/.cloudflared/config.yml`) that forwards the hostname to MovieDB's port:

   ```yaml
   tunnel: <tunnel ID>
   credentials-file: <path to the credentials JSON>
   ingress:
     - hostname: library.example.com
       service: http://127.0.0.1:5000
     - service: http_status:404
   ```

3. In Cloudflare Zero Trust, create an **Access application** for `library.example.com` that only allows your own email (for example with one-time email codes). **Don't skip this**, or anyone could open your library.

4. Start MovieDB, then run the tunnel; both must keep running:

   ```sh
   cloudflared tunnel --config ~/.cloudflared/config.yml run moviedb
   ```

Open `https://library.example.com` on any device and sign in with an allowed email. Over the tunnel the settings page is read-only; settings can only be changed on the computer itself.

Notes:

- Cloudflare only forwards traffic; the app, database, TMDB and AList connections all stay on your computer. If it sleeps, shuts down, goes offline or either process exits, the site is unavailable (see the snapshot below).
- **Play** always opens the player on the computer running MovieDB, so pressing it remotely works like a remote control.
- MovieDB uses a built-in development server, fine for personal use; for long-running unattended setups use a production WSGI server and start it at boot.
- Tunnel credentials live in your user folder — never copy them into the project or commit them.

## Cloud read-only snapshot

When your computer is offline (shut down, or MovieDB / the tunnel not running), the same hostname switches to a read-only snapshot in the cloud that looks just like the local site: editing buttons are disabled and write requests return 503. While the computer is online every request is passed through unchanged, including playback. The snapshot is still protected by Cloudflare Access.

### Components

| Part | Role |
|---|---|
| `cloud/router/` | Entry Worker (JavaScript) on the hostname's `/*` route. It tries the tunnel first; local responses carry an `X-MovieDB-Origin` header, so a 5xx without it or a connection failure counts as offline and the snapshot is served instead |
| `cloud/renderer/` | Read-only rendering Worker (Python Workers + Flask) that reuses `app.py`, `queries/`, `viewmodels/` and `templates/` to render page combinations not pre-generated in the snapshot |
| R2 bucket `moviedb-snapshot` | Pre-rendered pages, `static/` files and a read-only copy of `library.db` |
| `scripts/publish_snapshot.py` | Backs up `library.db`, crawls every page in read-only mode and uploads changed files incrementally |
| `services/cloud_sync.py` | With `MOVIEDB_SYNC_TOKEN` set, syncs once at startup and then whenever `library.db` changes and stays unchanged for a minute (at least 2 minutes between syncs) |

`discovery.db`, `.env` and similar files are never uploaded. Playback is unavailable while offline because it relies on your local AList.

### Deploying

1. Create an R2 bucket named `moviedb-snapshot`.
2. Edit `cloud/router/wrangler.jsonc`:
   - `routes`: your hostname (for example `library.example.com/*`), plus optionally a preview hostname that always shows the snapshot;
   - `vars.SNAPSHOT_HOSTS`: the preview hostname(s), comma-separated;
   - `vars.SYNC_TOKEN_SHA256`: the SHA-256 digest of the sync token. Generate a random token, put it in `.env` as `MOVIEDB_SYNC_TOKEN`, and put its digest here (the digest can be public; the token itself stays on your computer).
3. Deploy both Workers:

   ```sh
   cd cloud && npm install
   cd renderer && uv run python build.py && uv run pywrangler deploy
   cd ../router && npx wrangler deploy
   ```

4. Set `MOVIEDB_SYNC_TOKEN` and the router Worker's `workers.dev` address as `MOVIEDB_SYNC_URL` in `.env`, then restart MovieDB.

Pushing to `main` redeploys the rendering Worker through `.github/workflows/deploy-cloud.yml` (needed after changing `templates/`, `app.py` and similar code). It requires a `CLOUDFLARE_API_TOKEN` repository secret with *Workers Scripts: Edit*, *Workers Routes: Edit* and *Workers R2 Storage: Edit*.

### Syncing manually

```sh
uv run python scripts/publish_snapshot.py            # sync now
uv run python scripts/publish_snapshot.py --dry-run  # build only, don't upload
```

`MOVIEDB_SYNC_DISABLED=1` pauses automatic sync. The preview hostname lets you check the offline experience at any time.

### Turning it off

Delete the Worker route on your hostname in Cloudflare to go back to plain tunnel access.
