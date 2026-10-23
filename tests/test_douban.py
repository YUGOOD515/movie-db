"""豆瓣评分：ID 映射、取分、被拦截时暂停、全库刷新与旧库升级。网络请求全部打桩。"""

import sqlite3
from contextlib import closing
from unittest.mock import Mock, patch

import pytest
import requests

from connectors import douban, throttle
from models import library
from services import rating_sync, setup_wizard
from viewmodels.title import _external_links, format_ratings


def _json(status=200, payload=None, headers=None):
    response = Mock(status_code=status, headers=headers or {}, is_redirect=status in (301, 302))
    response.json.return_value = payload if payload is not None else {}
    return response


def _wikidata(pairs):
    return _json(200, {"results": {"bindings": [
        {"imdb": {"value": imdb}, "douban": {"value": did}} for imdb, did in pairs]}})


@pytest.fixture
def douban_on(monkeypatch):
    monkeypatch.setenv("DOUBAN_ENABLED", "1")


def test_lookup_maps_with_wikidata_and_signs_frodo_request():
    movie = {"id": "1292052", "rating": {"value": 9.7, "count": 3200000}}
    with patch.object(requests, "get", side_effect=[_wikidata([("tt0111161", "1292052")]), _json(200, movie)]) as get:
        fields = douban.lookup("tt0111161", "movie")
    assert fields == {"douban_id": "1292052", "douban_rating": 9.7, "douban_votes": 3200000}
    frodo = get.call_args_list[1]
    assert frodo.args[0] == "https://frodo.douban.com/api/v2/movie/1292052"
    assert frodo.kwargs["params"]["apiKey"] and frodo.kwargs["params"]["_sig"]
    assert "VALUES ?imdb" in get.call_args_list[0].kwargs["params"]["query"]


def test_lookup_falls_back_to_v2_and_tries_tv_first_for_series():
    with patch.object(requests, "get", side_effect=[_wikidata([]), _json(200, {"id": "26794435", "rating": {"value": 0, "count": 3}})]) as get, \
         patch.object(requests, "post", return_value=_json(200, {"id": "https://api.douban.com/movie/26794435"})) as post:
        fields = douban.lookup("tt5491994", "series")
    assert post.call_args.args[0].endswith("/movie/imdb/tt5491994")
    assert get.call_args.args[0].endswith("/tv/26794435")
    # 评价人数不足：只记豆瓣 ID，不写评分（保留旧值）
    assert fields == {"douban_id": "26794435"}
    with patch.object(requests, "post", return_value=_json(404, {"msg": "movie_not_found", "code": 5000})):
        assert douban.lookup("tt0000001", "movie", wikidata=False) == {}


def test_blocked_pauses_whole_service():
    with patch.object(requests, "get", return_value=_json(403, {"msg": "need_login", "code": 103})):
        with pytest.raises(douban.DoubanBlocked):
            douban.rating("1292052", "movie")
    assert throttle.limiter("douban").blocked_for() > 60
    with patch.object(requests, "get") as get:
        with pytest.raises(throttle.RateLimitError):
            douban.rating("1292052", "movie")
    get.assert_not_called()
    throttle.reset()
    redirect = _json(302, headers={"Location": "https://sec.douban.com/c?r=x"})
    redirect.json.side_effect = ValueError
    with patch.object(requests, "get", return_value=redirect), pytest.raises(douban.DoubanBlocked):
        douban.rating("1292052", "movie")


def test_fetch_douban_rating_respects_switch_and_swallows_errors(douban_on, monkeypatch):
    with patch.object(douban, "lookup", side_effect=requests.ConnectionError()):
        assert rating_sync.fetch_douban_rating("tt0111161", "movie") == {}
    monkeypatch.setenv("DOUBAN_ENABLED", "0")
    with patch.object(douban, "lookup") as lookup:
        assert rating_sync.fetch_douban_rating("tt0111161", "movie") == {}
    lookup.assert_not_called()


def _library(tmp_path):
    database = tmp_path / "library.db"
    setup_wizard.ensure_library(database)
    with closing(sqlite3.connect(database)) as db, db:
        db.executemany(
            "INSERT INTO title_basic (title_id, title_type, display_title, douban_id, douban_rating) VALUES (?, ?, ?, ?, ?)",
            [("tt0000001", "movie", "甲", None, None), ("tt0000002", "series", "乙", "222", 7.0),
             ("tt0000003", "movie", "丙", None, 6.5)])
    return database


def test_refresh_douban_ratings_batches_mapping_and_stops_when_blocked(tmp_path):
    database = _library(tmp_path)
    calls = []

    def lookup(title_id, title_type, douban_id, *, wikidata):
        calls.append((title_id, title_type, douban_id, wikidata))
        if title_id == "tt0000002":
            raise douban.DoubanBlocked("HTTP 403")
        return {"douban_id": douban_id, "douban_rating": 8.1, "douban_votes": 100}

    with patch.object(douban, "wikidata_ids", return_value={"tt0000001": "111"}) as batch, \
         patch.object(douban, "lookup", side_effect=lookup):
        result = rating_sync.refresh_douban_ratings(database)
    assert batch.call_args.args[0] == ["tt0000001", "tt0000003"]
    assert calls[:2] == [("tt0000001", "movie", "111", False), ("tt0000002", "series", "222", False)]
    assert len(calls) == 2 and result["douban_succeeded"] == 1 and result["douban_message"]
    with closing(sqlite3.connect(database)) as db:
        rows = dict((row[0], row[1:]) for row in db.execute("SELECT title_id, douban_id, douban_rating FROM title_basic"))
    # 被拦截之后的作品保留原值
    assert rows == {"tt0000001": ("111", 8.1), "tt0000002": ("222", 7.0), "tt0000003": (None, 6.5)}


def test_upgrade_library_adds_douban_columns(tmp_path):
    database = _library(tmp_path)
    with closing(sqlite3.connect(database)) as db, db:
        for column in ("douban_id", "douban_rating", "douban_votes"):
            db.execute(f"ALTER TABLE title_basic DROP COLUMN {column}")
    assert library.upgrade_library(database) == ["douban_id", "douban_rating", "douban_votes"]
    assert library.upgrade_library(database) == []
    assert library.upgrade_library(tmp_path / "missing.db") == []
    with closing(sqlite3.connect(database)) as db:
        assert db.execute("SELECT count(*) FROM title_basic WHERE douban_id IS NULL").fetchone()[0] == 3


def test_douban_badge_links_to_subject():
    title = {"title_id": "tt0111161", "average_rating": 9.3, "douban_rating": 9.7, "douban_votes": 3200000,
             "douban_id": "1292052"}
    ratings = format_ratings(title)
    item = next(rating for rating in ratings if rating["key"] == "douban")
    assert item["score"] == "9.7" and item["url"] == "https://movie.douban.com/subject/1292052/"
    assert "3,200,000 人评价" in item["title"]
    assert {"label": "豆瓣", "url": item["url"]} in _external_links(title, False)
    assert not any(rating["key"] == "douban" for rating in format_ratings({"douban_id": "1"}))


def test_import_writes_douban_fields_even_without_mdblist():
    from services import media_import
    skipped = {"status": "skipped", "ratings": {"mdblist_score": None, "douban_rating": 8.0, "douban_id": "1"}}
    assert media_import._ratings_to_write(skipped) == {"douban_rating": 8.0, "douban_id": "1"}
    assert media_import._ratings_to_write({"status": "unavailable"}) == {}
    success = {"status": "success", "ratings": {"mdblist_score": 70}}
    assert media_import._ratings_to_write(success) == {"mdblist_score": 70}
