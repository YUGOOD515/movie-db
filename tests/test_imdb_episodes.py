"""IMDb 本地库（单集 + 评分）：真实 SQLite、条件下载、原子更新和元数据回退。"""

import gzip
import sqlite3
from unittest.mock import MagicMock, Mock

import pytest
import requests

from connectors import imdb_episodes as remote, tmdb
from models import imdb_episodes as store
from services import imdb_identity as identity

HEADER = "tconst\tparentTconst\tseasonNumber\tepisodeNumber\n"
RATINGS = "tconst\taverageRating\tnumVotes\ntt0000001\t8.5\t1200\ntt1000099\t7.1\t33\n"


def source(path, rows="tt1000001\ttt0000001\t1\t1\n"):
    with gzip.open(path, "wt", encoding="utf-8") as file:
        file.write(HEADER + rows)
    return path


def existing(tmp_path, rows=None):
    path = identity.index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    store.build(source(tmp_path / "old.gz", rows or "tt1000001\ttt0000001\t1\t1\n"), path,
                {"revision": "old", "etag": '"v1"', "checked_at": 100, "updated_at": 90})
    return path


def test_index_preserves_ambiguous_and_unknown_rows_without_guessing(tmp_path):
    path = existing(tmp_path, "tt1000001\ttt0000001\t1\t1\n"
                    "tt1000002\ttt0000001\t1\t2\n"
                    "tt1000003\ttt0000001\t1\t2\n"
                    "tt1000004\ttt0000001\t\\N\t\\N\n"
                    "tt1000005\ttt0000001\t0\t1\n")
    assert store.info(path)["rows"] == 5
    assert store.lookup(path, "tt0000001") == {(1, 1): "tt1000001", (0, 1): "tt1000005"}
    assert store.lookup(path, "tt9999999") == {}
    assert store.lookup(path, "1 OR 1=1") == {}
    with sqlite3.connect(path) as db:
        assert "WITHOUT ROWID" in db.execute("SELECT sql FROM sqlite_master WHERE name='episode'").fetchone()[0]


def test_ratings_share_the_index_and_ignore_invalid_ids(tmp_path):
    ratings = tmp_path / "ratings.gz"
    with gzip.open(ratings, "wt", encoding="utf-8") as file:
        file.write(RATINGS)
    path = tmp_path / "index.sqlite3"
    store.build(source(tmp_path / "episodes.gz"), path, {"revision": "r"}, ratings_source=ratings)
    assert store.info(path)["rating_rows"] == 2
    assert store.ratings(path, ["tt0000001", "tt404", "x OR 1"]) == {"tt0000001": (8.5, 1200)}
    assert store.ratings(tmp_path / "missing.sqlite3", ["tt0000001"]) == {}
    bad = tmp_path / "bad.gz"
    bad.write_bytes(gzip.compress(HEADER.encode()))
    with pytest.raises(ValueError, match="评分文件格式"):
        store.build(source(tmp_path / "episodes2.gz"), tmp_path / "bad.sqlite3", {"revision": "b"}, ratings_source=bad)


@pytest.mark.parametrize("payload", [b"not gzip", gzip.compress(HEADER.encode()),
    gzip.compress((HEADER + "invalid\ttt1\t1\t1\n").encode())])
def test_bad_data_is_never_published(tmp_path, payload):
    path = existing(tmp_path)
    bad = tmp_path / "bad.gz"
    bad.write_bytes(payload)
    with pytest.raises((ValueError, OSError, EOFError)):
        store.build(bad, tmp_path / "new.sqlite3", {"revision": "bad"})
    assert store.info(path)["revision"] == "old"
    assert store.lookup(path, "tt0000001") == {(1, 1): "tt1000001"}


def test_publish_invalidates_lookup_cache_and_check_keeps_revision(tmp_path):
    path = existing(tmp_path)
    assert store.lookup(path, "tt0000001")[(1, 1)] == "tt1000001"
    new = tmp_path / "new.sqlite3"
    store.build(source(tmp_path / "new.gz", "tt1000099\ttt0000001\t1\t1\n"), new, {"revision": "new"})
    store.publish(new, path)
    store.checked(path, 200)
    assert store.lookup(path, "tt0000001")[(1, 1)] == "tt1000099"
    assert store.info(path)["revision"] == "new"
    assert store.info(path)["checked_at"] == 200


def test_conditional_download_and_stream_progress(tmp_path, monkeypatch):
    response = MagicMock(status_code=200, headers={"Content-Length": "6", "ETag": '"v2"'})
    response.__enter__.return_value = response
    response.iter_content.return_value = [b"abc", b"def"]
    get = Mock(return_value=response)
    monkeypatch.setattr(remote.requests, "get", get)
    progress = Mock()
    target = tmp_path / "download.gz"
    assert remote.download(target, etag='"v1"', progress=progress)["etag"] == '"v2"'
    assert target.read_bytes() == b"abcdef"
    assert get.call_args.kwargs["headers"]["If-None-Match"] == '"v1"'
    assert get.call_args.kwargs["stream"] is True
    progress.assert_called_with(6, 6)
    response.__exit__.assert_called()
    response.status_code = 304
    response.iter_content.reset_mock()
    assert remote.download(target, etag='"v2"') == {"unchanged": True}
    response.iter_content.assert_not_called()


def test_incomplete_download_is_rejected(tmp_path, monkeypatch):
    response = MagicMock(status_code=200, headers={"Content-Length": "6"})
    response.__enter__.return_value = response
    response.iter_content.return_value = [b"abc"]
    monkeypatch.setattr(remote.requests, "get", Mock(return_value=response))
    with pytest.raises(ValueError, match="未下载完整"):
        remote.download(tmp_path / "download.gz")


def run_refresh(monkeypatch):
    # 保留真实文件发布；只阻止测试额外启动后台扫描。
    from services import media_discovery
    scan = Mock()
    monkeypatch.setattr(media_discovery, "start_scan", scan)
    identity._jobs[str(identity.index_path())] = {"running": True, "started_at": 1}
    identity._refresh(identity.index_path(), "library.db")
    return scan


def test_update_304_does_not_rebuild_or_rescan(tmp_path, monkeypatch):
    path = existing(tmp_path)
    stale = path.parent / ".episodes-update-killed"  # 上次更新中途退出留下的临时目录
    stale.mkdir()
    (stale / "episodes.tsv.gz").write_bytes(b"partial")
    monkeypatch.setattr(remote, "download", Mock(return_value={"unchanged": True}))
    build = Mock(side_effect=AssertionError("must not rebuild"))
    monkeypatch.setattr(store, "build", build)
    scan = run_refresh(monkeypatch)
    assert identity.status()["revision"] == "old"
    assert identity.status()["checked_at"] > 100
    assert identity.status()["message"] == "已是最新版本"
    assert not identity.status()["running"]
    scan.assert_not_called()
    assert not stale.exists()


@pytest.mark.parametrize("failure", [requests.Timeout("offline"), ValueError("文件损坏")])
def test_update_failure_preserves_old_index(tmp_path, monkeypatch, failure):
    path = existing(tmp_path)
    original = path.read_bytes()
    monkeypatch.setattr(remote, "download", Mock(side_effect=failure))
    scan = run_refresh(monkeypatch)
    assert path.read_bytes() == original
    assert identity.status()["error"]
    assert not identity.status()["running"]
    assert not list(path.parent.glob(".imdb-update-*"))
    scan.assert_not_called()


def fake_download(episode_rows="tt1000099\ttt0000001\t1\t1\n", calls=None):
    def download(target, *, url, etag=None, progress=None):
        if calls is not None:
            calls.append((url, etag))
        if url == remote.RATINGS_URL:
            with gzip.open(target, "wt", encoding="utf-8") as file:
                file.write(RATINGS)
            return {"unchanged": False, "etag": '"r2"', "download_bytes": 10}
        source(target, episode_rows)
        return {"unchanged": False, "etag": '"v2"', "download_bytes": 20}
    return download


def test_successful_update_publishes_ratings_and_episodes_together(tmp_path, monkeypatch):
    path = existing(tmp_path)
    legacy = path.parent / "title.ratings.tsv.gz"
    legacy.write_bytes(b"old")
    calls = []
    monkeypatch.setattr(remote, "download", fake_download(calls=calls))
    from services import rating_sync
    refresh = Mock()
    monkeypatch.setattr(rating_sync, "refresh_imdb_ratings", refresh)
    scan = run_refresh(monkeypatch)
    assert calls == [(remote.URL, '"v1"'), (remote.RATINGS_URL, None)]
    assert identity.revision() != "old"
    assert identity.episode_ids("tt0000001") == {(1, 1): "tt1000099"}
    assert identity.ratings(["tt1000099"]) == {"tt1000099": (7.1, 33)}
    assert identity.status()["ratings_etag"] == '"r2"' and not identity.status()["running"]
    assert not legacy.exists()
    refresh.assert_called_once_with("library.db")
    scan.assert_called_once_with("library.db")


def test_one_changed_file_downloads_both_again(tmp_path, monkeypatch):
    existing(tmp_path)
    calls, real = [], fake_download()
    def download(target, *, url, etag=None, progress=None):
        calls.append((url, etag))
        return {"unchanged": True} if etag else real(target, url=url)
    monkeypatch.setattr(remote, "download", download)
    from services import rating_sync
    monkeypatch.setattr(rating_sync, "refresh_imdb_ratings", Mock())
    run_refresh(monkeypatch)
    assert calls == [(remote.URL, '"v1"'), (remote.RATINGS_URL, None), (remote.URL, None), (remote.RATINGS_URL, None)]
    assert identity.ratings(["tt0000001"]) == {"tt0000001": (8.5, 1200)}


def test_abnormal_row_drop_keeps_old_database(tmp_path, monkeypatch):
    path = existing(tmp_path, "".join(f"tt{1000000+i}\ttt0000001\t1\t{i}\n" for i in range(1, 1002)))
    monkeypatch.setattr(remote, "download", fake_download("tt1000001\ttt0000001\t1\t1\n"))
    run_refresh(monkeypatch)
    assert store.info(path)["rows"] == 1001
    assert "数据量异常" in identity.status()["error"]


def test_schedule_modes_and_failed_update_backoff(tmp_path, monkeypatch):
    existing(tmp_path)
    monkeypatch.delenv("IMDB_EPISODES_UPDATE", raising=False)
    assert not identity.due(101)
    assert identity.due(100 + 7 * 86400)
    monkeypatch.setenv("IMDB_EPISODES_UPDATE", "daily")
    assert identity.due(100 + 86400)
    identity._jobs[str(identity.index_path())] = {"error": "offline", "finished_at": 100 + 86400}
    assert not identity.due(100 + 86401)
    assert identity.due(100 + 86400 + 3601)
    monkeypatch.setenv("IMDB_EPISODES_UPDATE", "manual")
    assert not identity.due(10**10)


def test_first_update_and_single_flight(monkeypatch):
    monkeypatch.delenv("IMDB_EPISODES_UPDATE", raising=False)
    assert identity.due()
    thread = Mock()
    monkeypatch.setattr(identity, "Thread", thread)
    assert identity.start_update()
    assert not identity.start_update()
    assert not identity.due()
    thread.return_value.start.assert_called_once()


def test_tmdb_fallback_priority_conflicts_and_missing_positions(monkeypatch):
    episodes = [{"season_number": 1, "episode_number": n, "name": f"E{n}"} for n in range(1, 6)]
    fetch = Mock(return_value={(1, 2): "tt2000002"})
    monkeypatch.setattr(tmdb, "fetch_episode_ids", fetch)
    monkeypatch.setattr(tmdb, "build_episode", lambda ep, series, title_id: (ep["episode_number"], title_id))
    result = tmdb._series_episodes(None, 1, {}, [{"episodes": episodes}], {(1, 1): "tt1000001"},
        {(1, 1): "tt9999999", (1, 2): "tt9999998", (1, 3): "tt3000003", (1, 4): "tt1000001",
         (1, 5): "invalid", (1, 6): "tt3000006"})
    assert result == [(1, "tt1000001"), (2, "tt2000002"), (3, "tt3000003")]
    assert (1, 1) not in fetch.call_args.args[2]


def test_tmdb_cache_changes_with_local_index_mappings(monkeypatch):
    tmdb._cache.clear()
    fetch = Mock(side_effect=lambda session, title_id, known, fallback: {"fallback": fallback})
    monkeypatch.setattr(tmdb, "_build_media_metadata", fetch)
    assert tmdb.build_media_metadata("tt1", session=Mock())["fallback"] == {}
    assert tmdb.build_media_metadata("tt1", session=Mock(), fallback_episode_ids={(1, 1): "tt2"})["fallback"]
    assert fetch.call_count == 2
    tmdb._cache.clear()
