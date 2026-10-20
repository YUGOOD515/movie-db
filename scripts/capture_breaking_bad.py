"""Save current local records/files and real API responses for offline replay."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
from urllib.parse import urlsplit

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests

from connectors import mdblist, tmdb
from scripts.local_test_api import DEFAULT_DATA

TITLE_ID = "tt0903747"
TMDB_ID = 1396
PROJECT = tmdb.PROJECT_ROOT
REMOTE_API = "https://api.themoviedb.org/3"
REMOTE_IMAGES = "https://image.tmdb.org/t/p"


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_local_records():
    with closing(sqlite3.connect(f"file:{PROJECT / 'library.db'}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        relations = [dict(row) for row in db.execute(
            "SELECT * FROM title_relation WHERE series_id = ?", (TITLE_ID,))]
        ids = [TITLE_ID] + [row["episode_id"] for row in relations]
        marks = ",".join("?" for _ in ids)
        library = {"title_relation": relations}
        for table in ("title_basic", "title_artwork", "title_cast", "media_file", "collection_item"):
            library[table] = [dict(row) for row in db.execute(
                f"SELECT * FROM {table} WHERE title_id IN ({marks})", ids)]
        library["playback_progress"] = [dict(row) for row in db.execute(
            f"SELECT * FROM playback_progress WHERE file_id IN "
            f"(SELECT file_id FROM media_file WHERE title_id IN ({marks}))", ids)]
        library["collection"] = [dict(row) for row in db.execute(
            f"SELECT * FROM collection WHERE collection_id IN "
            f"(SELECT collection_id FROM collection_item WHERE title_id IN ({marks}))", ids)]
    return library


def capture(root):
    # Refuse to overwrite the original snapshot, including interrupted captures.
    root.mkdir(parents=True, exist_ok=False)
    library = read_local_records()
    write_json(root / "library-records.json", library)
    local_assets = {}
    for source in (PROJECT / "static/images" / TITLE_ID).rglob("*"):
        if source.is_file():
            relative = source.relative_to(PROJECT / "static")
            target = root / "saved-files" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    for row in library["title_artwork"] + library["title_cast"]:
        if not row.get("local_path"):
            continue
        source = PROJECT / "static" / row["local_path"]
        if source.is_file():
            target = root / "saved-files" / row["local_path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            if row.get("remote_path"):
                url = row["remote_path"]
                key = urlsplit(url).path.removeprefix("/t/p/") if url.startswith("http") else "original/" + url.lstrip("/")
                local_assets[key] = target.relative_to(root).as_posix()
    print(f"Saved existing records and {len(list((root / 'saved-files').rglob('*.*')))} files.", flush=True)

    with tmdb._session() as session:
        def get(endpoint, **params):
            response = session.get(REMOTE_API + endpoint, params={"language": "zh-CN", **params}, timeout=30)
            response.raise_for_status()
            return response.json()

        found = get(f"/find/{TITLE_ID}", external_source="imdb_id")
        detail = get(f"/tv/{TMDB_ID}", append_to_response="images,aggregate_credits,external_ids,translations",
                     include_image_language="zh,null,en")
        assert detail["id"] == TMDB_ID and detail["external_ids"]["imdb_id"] == TITLE_ID
        resources = {}
        for key in ("images", "aggregate_credits", "external_ids", "translations"):
            resources[f"tv/{TMDB_ID}/{key}"] = detail.pop(key)
        resources[f"tv/{TMDB_ID}"] = detail
        numbers = [s["season_number"] for s in detail["seasons"] if s.get("episode_count", 0) > 0]
        seasons_detail = get(f"/tv/{TMDB_ID}", append_to_response=",".join(f"season/{n}" for n in numbers))
        seasons = [seasons_detail[f"season/{n}"] for n in numbers]
        keys = [f"season/{e['season_number']}/episode/{e['episode_number']}/external_ids"
                for season in seasons for e in season.get("episodes", [])]
        mapping = {}
        for start in range(0, len(keys), tmdb.APPEND_LIMIT):
            chunk = keys[start:start + tmdb.APPEND_LIMIT]
            payload = get(f"/tv/{TMDB_ID}", append_to_response=",".join(chunk))
            for key in chunk:
                parts = key.split("/")
                mapping[(int(parts[1]), int(parts[3]))] = (payload.get(key) or {}).get("imdb_id")
        finds = {TITLE_ID: found}
        episode_assets = []
        for season in seasons:
            resources[f"tv/{TMDB_ID}/season/{season['season_number']}"] = season
            for episode in season.get("episodes", []):
                number = (episode["season_number"], episode["episode_number"])
                resources[f"tv/{TMDB_ID}/season/{number[0]}/episode/{number[1]}"] = episode
                if episode.get("still_path"):
                    episode_assets.append("original/" + episode["still_path"].lstrip("/"))
                title_id = mapping.get(number)
                if title_id:
                    finds[title_id] = {"movie_results": [], "tv_results": [], "tv_season_results": [],
                                       "person_results": [], "tv_episode_results": [{**episode, "show_id": TMDB_ID}]}
                    resources[f"tv/{TMDB_ID}/season/{number[0]}/episode/{number[1]}/external_ids"] = {"id": episode["id"], "imdb_id": title_id}
        search = get("/search/tv", query="Breaking Bad", page=1, include_adult="false")
        search["results"] = [row for row in search["results"] if row["id"] == TMDB_ID]
        if not search["results"]:
            raise ValueError("Breaking Bad is missing from the search response")
        search.update(total_results=1, total_pages=1)
        write_json(root / "tmdb.json", {"search": search, "resources": resources, "find": finds})

    cfg = mdblist.create_config()
    if not cfg.mdblist_apikey:
        raise ValueError("MDBLIST_APIKEY is required for capture")
    response = requests.get("https://mdblist.com/api/", params={"apikey": cfg.mdblist_apikey, "i": TITLE_ID}, timeout=30)
    # Never include the credential-bearing request URL in diagnostics or files.
    if response.status_code != 200:
        raise ValueError(f"MDBList capture failed: HTTP {response.status_code}")
    ratings = response.json()
    if mdblist.parse_mdblist_payload(TITLE_ID, ratings, 0).status != "success":
        raise ValueError("MDBList returned no usable ratings")
    write_json(root / "mdblist.json", ratings)

    artwork = tmdb.build_title_artwork(TITLE_ID, resources[f"tv/{TMDB_ID}/images"])
    cast = tmdb.build_title_cast(TITLE_ID, resources[f"tv/{TMDB_ID}/aggregate_credits"].get("cast", []))
    keys = set(episode_assets)
    for row in artwork + cast:
        if row.get("remote_path"):
            keys.add(urlsplit(row["remote_path"]).path.removeprefix("/t/p/"))
    posters = resources[f"tv/{TMDB_ID}/images"].get("posters", [])
    posters = sorted(posters, key=lambda image: (image.get("iso_639_1") != "zh", -(image.get("vote_average") or 0)))
    if posters:
        keys.add("w185/" + posters[0]["file_path"].lstrip("/"))
    if search["results"][0].get("poster_path"):
        keys.add("w185/" + search["results"][0]["poster_path"].lstrip("/"))

    def save_image(key):
        if key in local_assets:
            return key, local_assets[key]
        target = root / "api-images" / key
        target.parent.mkdir(parents=True, exist_ok=True)
        response = requests.get(f"{REMOTE_IMAGES}/{key}", timeout=30)
        response.raise_for_status()
        target.write_bytes(response.content)
        return key, target.relative_to(root).as_posix()

    with ThreadPoolExecutor(max_workers=4) as pool:
        local_assets.update(pool.map(save_image, sorted(keys)))
    hashes = {file.relative_to(root).as_posix(): hashlib.sha256(file.read_bytes()).hexdigest()
              for file in root.rglob("*") if file.is_file()}
    write_json(root / "manifest.json", {
        "imdb_id": TITLE_ID, "tmdb_id": TMDB_ID,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "sources": {"tmdb": REMOTE_API, "images": REMOTE_IMAGES, "mdblist": "https://mdblist.com/api/"},
        "notes": "Episode find replay combines saved TMDb season payloads with TMDb episode external_ids. Only images used by current search/import and pre-existing files are saved. AList videos are not copied.",
        "library_counts": {key: len(value) for key, value in library.items()},
        "episode_find_count": len(finds) - 1,
        "images": local_assets, "sha256": hashes,
    })
    print(f"Snapshot ready: {root}; {len(finds) - 1} episodes, {len(local_assets)} image URLs.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_DATA)
    args = parser.parse_args()
    capture(args.output.resolve())
