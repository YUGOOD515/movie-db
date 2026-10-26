import sys
sys.path = [p for p in sys.path if "AppData" not in p] + [p for p in sys.path if "AppData" in p]

import io
import json
import os
import re
import requests
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import unquote

with patch.dict(os.environ, {"ALIST_AUTOSTART": "0"}):
    import app as movie_app
from connectors import alist, imdb_ratings, media_probe, player, tmdb
from models import database as database_module
from models import library as models
from queries.collection import get_collection_items
from queries.title import query_title
from services import media_import as media_import_service
from services import rating_sync
from services.playback import is_effectively_completed
from connectors import tmdb as tmdb_connector
from viewmodels.title import build_title_viewmodel


REAL_IDENTIFY_TITLE = tmdb.identify_title
REAL_BUILD_MEDIA_METADATA = tmdb.build_media_metadata


def _fake_title(title_id, title_type, name, **values):
    return {
        "title_id": title_id, "tmdb_id": None, "title_type": title_type,
        "primary_title": name, "original_title": name, "original_language": "en",
        "display_title": values.pop("display_title"), "is_adult": 0, "start_year": 2026,
        "end_year": None, "runtime_minutes": values.pop("runtime", 50), "genres": "Drama",
        "overview": None, "tagline": None, "average_rating": None, "num_votes": None, **values,
    }


FAKE_EPISODES = {
    "tt9000003": (1, 1, "First Episode", "第一集"),
    "tt9000004": (1, 2, "Second Episode", "第二集"),
}
FAKE_RATINGS = {
    "tt9000001": (8.1, 1000), "tt9000002": (8.2, 2000),
    "tt9000003": (8.3, 300), "tt9000004": (8.4, 400),
}


def fake_identify_title(title_id, session=None):
    if title_id == "tt9000001":
        return {"title_type": "movie", "root_id": title_id}
    if title_id == "tt9000002":
        return {"title_type": "series", "root_id": title_id}
    if title_id in FAKE_EPISODES:
        season, episode, *_ = FAKE_EPISODES[title_id]
        return {"title_type": "episode", "root_id": "tt9000002",
                "season_number": season, "episode_number": episode}
    raise ValueError(f"TMDB 中未找到 {title_id} 对应的电影、剧集或单集。")


def fake_media_metadata(title_id, session=None, known_episode_ids=None, artwork=()):
    """Offline stand-in for TMDB: one movie and one two-episode series."""
    artwork = [dict(image) for image in artwork]
    if title_id == "tt9000001":
        return {"title_basic": _fake_title(title_id, "movie", "Import Movie", display_title="导入电影", runtime=120),
                "title_artwork": artwork, "title_cast": [], "episodes": []}
    if title_id == "tt9000002":
        return {
            "title_basic": _fake_title(title_id, "series", "Import Series", display_title="导入剧集"),
            "title_artwork": artwork, "title_cast": [],
            "episodes": [
                {"season_number": season, "episode_number": episode, "title_artwork": [],
                 "title_basic": _fake_title(episode_id, "episode", name, display_title=display)}
                for episode_id, (season, episode, name, display) in FAKE_EPISODES.items()
            ],
        }
    raise ValueError(f"TMDB 中未找到 {title_id} 对应的电影、剧集或单集。")


def fake_metadata_with(artwork):
    return lambda title_id, session=None, known_episode_ids=None: fake_media_metadata(
        title_id, session, known_episode_ids, artwork
    )


class LibraryIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "library.db"
        schema = (movie_app.BASE_DIR / "migrations" / "library_schema.sql").read_text(
            encoding="utf-8"
        )
        fixture = (
            movie_app.BASE_DIR / "tests" / "fixtures" / "library_fixture.sql"
        ).read_text(encoding="utf-8")
        with closing(sqlite3.connect(self.database_path)) as database:
            database.executescript(schema)
            database.executescript(fixture)

        tmdb._cache.clear()
        for name, fake in (("identify_title", fake_identify_title), ("build_media_metadata", fake_media_metadata)):
            patcher = patch.object(tmdb, name, side_effect=fake)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(imdb_ratings, "lookup_ratings", side_effect=lambda ids, path=None: {
            title_id: FAKE_RATINGS[title_id] for title_id in ids if title_id in FAKE_RATINGS
        })
        patcher.start()
        self.addCleanup(patcher.stop)

        self.previous_database = movie_app.app.config["DATABASE"]
        self.previous_image_root = movie_app.app.config["IMAGE_ROOT"]
        movie_app.app.config.update(
            TESTING=True,
            DATABASE=self.database_path,
            IMAGE_ROOT=Path(self.temporary_directory.name) / "images",
        )
        self.client = movie_app.app.test_client()

    def tearDown(self):
        movie_app.app.config["DATABASE"] = self.previous_database
        movie_app.app.config["IMAGE_ROOT"] = self.previous_image_root
        self.temporary_directory.cleanup()

    def connect(self):
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def test_title_search_and_selection_reuse_identity_without_writes(self):
        before = self.database_path.read_bytes()
        with patch.object(tmdb, "_session"), patch.object(tmdb, "_get") as fetch:
            tmdb._cache.clear()
            self.addCleanup(tmdb._cache.clear)
            fetch.side_effect = [
                {"page": 1, "total_pages": 2, "results": [{
                    "id": 9001, "title": "导入电影", "original_title": "Import Movie",
                    "release_date": "2026-01-01", "poster_path": "/poster.jpg",
                    "overview": "用于区分同名电影的简介",
                }]},
                {"id": 9001, "title": "导入电影", "release_date": "2026-01-01",
                 "external_ids": {"imdb_id": "tt9000001"}},
            ]
            url = "/manager/titles/search?q=导入电影&type=movie"
            data = self.client.get(url).get_json()
            self.assertEqual(data["items"][0]["year"], "2026")
            self.assertTrue(data["has_more"])
            self.assertEqual(self.client.get(url).get_json(), data)
            selected = self.client.get("/manager/titles/select?type=movie&tmdb_id=9001")
            self.assertEqual(selected.status_code, 200)
            self.assertEqual(selected.json, {"title_id": "tt9000001", "name": "导入电影", "year": 2026, "media_type": "movie", "tmdb_id": 9001})
            self.assertEqual(tmdb.find_media(Mock(), "tt9000001"), ("movie", {"id": 9001}))
            self.assertEqual(fetch.call_count, 2)
        self.assertEqual(before, self.database_path.read_bytes())

    def test_title_selection_rejects_missing_identity(self):
        for title_id in ("", "nm123"):
            summary = {"title_id": title_id, "title_type": "movie", "display_title": "电影", "start_year": 2026}
            with self.subTest(title_id=title_id), patch.object(tmdb, "fetch_title_summary", return_value=summary), patch.object(tmdb, "remember_media_identity") as remember:
                response = self.client.get("/manager/titles/select?type=movie&tmdb_id=9001")
                self.assertEqual(response.status_code, 400)
                self.assertIn("没有 IMDb 映射", response.json["error"])
                remember.assert_not_called()

    def test_selected_series_returns_identity_for_manual_episode_entry(self):
        summary = {"title_id": "tt9000002", "title_type": "series", "display_title": "导入剧集", "start_year": 2026}
        with patch.object(tmdb, "fetch_title_summary", return_value=summary), \
             patch.object(tmdb, "remember_media_identity"):
            response = self.client.get("/manager/titles/select?type=tv&tmdb_id=9002")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["media_type"], "tv")
        self.assertEqual(response.json["title_id"], "tt9000002")

    def test_selected_movie_is_written_by_existing_batch_import(self):
        tmdb._cache.clear()
        self.addCleanup(tmdb._cache.clear)
        # Exercise the real TMDB connector here instead of the offline fake.
        with patch.object(tmdb, "_session"), patch.object(tmdb, "_get") as get, \
             patch.object(tmdb, "identify_title", new=REAL_IDENTIFY_TITLE), \
             patch.object(tmdb, "build_media_metadata", new=REAL_BUILD_MEDIA_METADATA), \
             patch.object(alist, "validate_alist_video_url"), \
             patch.object(alist, "get_video_link", return_value="https://media.test/movie.mkv"), \
             patch.object(media_probe, "probe_video", return_value={"duration_ms": 7200000}), \
             patch.object(media_import_service, "fetch_title_rating", return_value={"status": "unavailable"}):
            get.side_effect = [
                {"id": 9001, "title": "导入电影", "release_date": "2026-01-01", "external_ids": {"imdb_id": "tt9000001"}},
                {"id": 9001, "title": "导入电影", "original_title": "Import Movie", "release_date": "2026-01-01",
                 "overview": "搜索后导入", "genres": [{"id": 18}, {"id": 878}, {"id": 10770}]},
            ]
            selected = self.client.get("/manager/titles/select?type=movie&tmdb_id=9001").json
            response = self.client.post("/manager/import/batch", data={
                "title_id": selected["title_id"], "season_number": "", "episode_number": "",
                "alist_url": "http://127.0.0.1:5244/d/Movies/Search.mkv?sign=test",
            }, follow_redirects=True)
            self.assertIn("批量录入完成：成功 1 条", response.get_data(as_text=True))
            self.assertEqual([call.args[1] for call in get.call_args_list], ["/movie/9001", "/movie/9001"])
            self.assertEqual(get.call_args_list[1].args[2]["append_to_response"], "images,credits,translations")
        with closing(self.connect()) as db:
            title = db.execute("SELECT tmdb_id, overview, genres, average_rating FROM title_basic WHERE title_id = 'tt9000001'").fetchone()
            media = db.execute("SELECT title_id FROM media_file WHERE alist_path = '/Movies/Search.mkv'").fetchone()
        self.assertEqual(tuple(title), (9001, "搜索后导入", "Drama,Sci-Fi", 8.1))
        self.assertEqual(media["title_id"], selected["title_id"])

    def test_title_search_validates_inputs_and_handles_upstream_errors(self):
        with patch.object(tmdb, "search_media") as search:
            for query in ["q=&type=movie", "q=test&type=person", "q=test&page=0", "q=test&page=abc", "q=test&page=501"]:
                self.assertEqual(self.client.get("/manager/titles/search?" + query).status_code, 400)
            search.assert_not_called()
            search.side_effect = requests.Timeout("private upstream details")
            response = self.client.get("/manager/titles/search?q=test")
            self.assertEqual(response.status_code, 502)
            self.assertNotIn("private", response.json["error"])
        with patch.object(tmdb, "fetch_title_summary") as resolve:
            for value in ["0", "-1", "../movie", "abc"]:
                self.assertEqual(self.client.get("/manager/titles/select", query_string={"tmdb_id": value}).status_code, 400)
            resolve.assert_not_called()

    def build_title(
        self,
        title_id: str,
        *,
        select_resume: bool = False,
        **parameters,
    ):
        with movie_app.app.test_request_context("/"):
            raw = query_title(database_module.get_db(), title_id, **parameters)
            return build_title_viewmodel(raw, select_resume=select_resume)

    def test_episode_fragment_uses_selected_episode_and_escapes_content(self):
        with closing(self.connect()) as database, database:
            database.execute("DELETE FROM media_file WHERE title_id = ?", ("tt11599999",))
            database.execute(
                "UPDATE title_basic SET overview = ? WHERE title_id = ?",
                ('<script>alert("episode")</script>', "tt11555014"),
            )
        response = self.client.get("/title/tt11555014/episode-panel")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        for attribute in ("data-episode-hero", "data-episode-hero-control", "data-episode-facts"):
            self.assertEqual(html.count(f'{attribute}="tt11555014"'), 1)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn('<script>', html)
        self.assertIn('/TV/Young Sheldon/S03E12.mkv', html)
        self.assertNotIn('/TV/Young Sheldon/S03E13.mkv', html)
        self.assertNotIn('<html', html)
        unavailable = self.client.get("/title/tt11599999/episode-panel").get_data(as_text=True)
        self.assertIn("暂无可播放资源", unavailable)
        for title_id in ("tt0816692", "tt6226232", "tt0000000"):
            self.assertEqual(self.client.get(f"/title/{title_id}/episode-panel").status_code, 404)

    def test_schema_data_and_integrity(self):
        with closing(self.connect()) as database:
            tables = {
                row[0]
                for row in database.execute(
                    """
                    SELECT name
                      FROM sqlite_master
                     WHERE type = 'table'
                       AND name NOT LIKE 'sqlite_%'
                    """
                )
            }
            self.assertEqual(
                tables,
                {
                    "title_basic",
                    "title_relation",
                    "title_artwork",
                    "title_cast",
                    "media_file",
                    "playback_progress",
                    "collection",
                    "collection_item",
                },
            )
            self.assertEqual(
                database.execute("SELECT COUNT(*) FROM title_basic").fetchone()[0],
                8,
            )
            self.assertEqual(
                database.execute("SELECT COUNT(*) FROM title_cast").fetchone()[0],
                3,
            )
            self.assertEqual(database.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(database.execute("PRAGMA foreign_key_check").fetchall(), [])

        with closing(database_module.connect_database(self.database_path)) as database:
            with self.assertRaises(sqlite3.OperationalError):
                database.execute("DELETE FROM collection_item")

    def test_core_routes_and_legacy_redirects(self):
        for path in (
            "/",
            "/recent",
            "/unwatched",
            "/top",
            "/catalog",
            "/catalog?type=series&genre=Drama&rating=8&language=en&sort=rating",
            "/favorites",
            "/collection/1",
            "/title/tt0816692",
            "/title/tt6226232",
            "/title/tt6226232?resume=1",
            "/title/tt6226232?season=3&episode=12",
            "/title/tt11555014",
            "/media",
            "/media?q=星际&type=movie",
            "/media?tab=associate",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

        self.assertEqual(self.client.get("/movies/157336").status_code, 404)
        self.assertEqual(self.client.get("/series/71728").status_code, 404)

    def test_title_cast_query_and_render(self):
        movie = self.build_title("tt0816692")
        self.assertEqual(
            [person["name"] for person in movie["cast"]],
            ["马修·麦康纳", "安妮·海瑟薇"],
        )
        self.assertIsNone(movie["cast"][1]["image"])

        movie_html = self.client.get("/title/tt0816692").get_data(as_text=True)
        episode_html = self.client.get("/title/tt11555014").get_data(as_text=True)
        self.assertIn("演员阵容", movie_html)
        self.assertIn("马修·麦康纳", movie_html)
        self.assertIn("安妮·海瑟薇", movie_html)
        self.assertIn("伊恩·阿米蒂奇", episode_html)

    def test_cast_replacement_is_atomic(self):
        replacement = [
            {
                "title_id": "tt0816692",
                "tmdb_person_id": 999,
                "name": "新演员",
                "character_name": "新角色",
                "cast_order": 0,
                "remote_path": None,
                "local_path": None,
            },
            {
                "title_id": "tt0816692",
                "tmdb_person_id": 1000,
                "name": "无效演员",
                "character_name": None,
                "cast_order": -1,
                "remote_path": None,
                "local_path": None,
            },
        ]

        with self.assertRaises(sqlite3.IntegrityError):
            models.replace_library_cast(self.database_path, "tt0816692", replacement)

        with closing(self.connect()) as database:
            rows = database.execute(
                "SELECT name FROM title_cast WHERE title_id = ? ORDER BY cast_order",
                ("tt0816692",),
            ).fetchall()
        self.assertEqual(
            [row["name"] for row in rows],
            ["马修·麦康纳", "安妮·海瑟薇"],
        )

    def test_home_catalog_and_title_viewmodels_render_expected_data(self):
        home = self.client.get("/").get_data(as_text=True)
        self.assertIn("data-home-hero", home)
        self.assertIn("1 / 3：星际穿越", home)
        self.assertIn('action="/play/3"', home)
        self.assertIn("vendor/embla-carousel.umd.js", home)
        self.assertIn("library-section--after-hero", home)
        self.assertIn("S3 E13 · 合同，规则和一点猪脑", home)
        self.assertIn("/title/tt6226232?resume=1", home)
        self.assertIn('action="/title/tt6226232/resume"', home)
        self.assertIn("电影类别", home)
        self.assertIn("剧集类别", home)
        self.assertIn("观看进度", home)
        self.assertEqual(home.count("media-rail--span-6"), 3)
        self.assertEqual(home.count("media-rail--span-3"), 1)
        self.assertEqual(home.count("media-rail--span-2"), 3)
        self.assertRegex(
            home,
            r'continue-frame[\s\S]+?</div>\s*<div class="continue-meta pt-3">',
        )

        catalog = self.client.get(
            "/catalog?type=movie&genre=Sci-Fi&rating=8&language=en&sort=rating"
        ).get_data(as_text=True)
        self.assertIn("星际穿越", catalog)
        self.assertNotIn("后室海报", catalog)
        self.assertIn("segmented-control", catalog)
        self.assertIn("genre-pills", catalog)
        # 有图时占位图标默认隐藏，仅在图片加载失败后由前端显示。
        self.assertRegex(
            catalog,
            r'data-media-image>\s*<span class="image-fallback[^"]*\bd-none"',
        )
        self.assertNotIn("onclick=", catalog)
        self.assertIn('data-filter-set="sort"', catalog)

        default_series = self.client.get("/title/tt6226232").get_data(as_text=True)
        selected_series = self.client.get(
            "/title/tt6226232?season=3&episode=12"
        ).get_data(as_text=True)
        direct_episode = self.client.get("/title/tt11555014").get_data(as_text=True)
        # 默认进入（未指定单集）按历史记录规则定位到续播单集 S3·E13
        self.assertIn("S3 · E13", default_series)
        self.assertIn('data-episode-hero="tt11591954"', default_series)
        self.assertIn("继续播放", default_series)
        self.assertIn("S3 · E12", selected_series)
        self.assertIn("身体闪光器和商场安全装备", selected_series)
        self.assertIn("S3 · E12", direct_episode)
        self.assertIn("detail-hero-grid", selected_series)
        self.assertIn("episode-rail-track", selected_series)
        information_position = selected_series.index('id="information-heading"')
        file_position = selected_series.index('id="file-information-heading"')
        links_position = selected_series.index('aria-label="外部链接"')
        self.assertLess(information_position, file_position)
        self.assertLess(file_position, links_position)
        self.assertNotIn('class="col-12 col-lg-7"', selected_series)
        self.assertNotIn('class="col-12 col-lg-5"', selected_series)

        # 懒渲染：服务端只输出精选单集的面板，其余单集按需获取共享模板片段
        self.assertEqual(selected_series.count('data-episode-hero="'), 1)
        self.assertEqual(selected_series.count('data-episode-hero-control="'), 1)
        self.assertEqual(selected_series.count('data-episode-facts="'), 1)

        # 全剧/本集信息切换：按钮 + 全剧面板（默认隐藏）
        self.assertIn('data-series-toggle', selected_series)
        self.assertIn('data-series-hero-panel', selected_series)
        self.assertIn('episode-hero-panels hero-pane-hidden', selected_series)
        self.assertIn("全剧 · 共", selected_series)
        self.assertNotIn("data-series-toggle", self.client.get("/title/tt0816692").get_data(as_text=True))
        self.assertIn('data-panel-url="/title/tt11555014/episode-panel"', selected_series)
        self.assertGreater(
            selected_series.count('data-episode-target="'),
            selected_series.count('data-episode-hero="'),
        )

        manager = self.client.get("/media?q=星际&type=movie").get_data(
            as_text=True
        )
        self.assertIn("overview-artwork-row", manager)
        self.assertIn("tt0816692", manager)
        self.assertIn("data-rating-refresh", manager)
        self.assertIn("/manager/ratings/refresh", manager)

        favorites = self.client.get("/favorites").get_data(as_text=True)
        self.assertIn("收藏夹筛选", favorites)
        self.assertIn("genre-pills", favorites)

        top = self.client.get("/top").get_data(as_text=True)
        ranked_cards = re.findall(
            r'<article class="ranked-feature">([\s\S]*?)</article>',
            top,
        )
        self.assertEqual(len(ranked_cards), 2)
        for card in ranked_cards:
            self.assertIn("ranked-feature-logo", card)
            self.assertIn("ranked-feature-content", card)
            self.assertIn("ranked-feature-meta", card)
            self.assertNotIn('class="fw-semibold text-truncate"', card)

        association = self.client.get("/media?tab=associate").get_data(
            as_text=True
        )
        self.assertIn('action="/manager/import/batch"', association)
        self.assertIn("data-batch-add", association)
        self.assertIn('name="json_file"', association)
        self.assertIn("media_import_template.json", association)
        self.assertNotIn("<fieldset disabled>", association)

    def test_missing_resources_return_404(self):
        self.assertEqual(self.client.get("/title/tt0000000").status_code, 404)
        self.assertEqual(self.client.get("/collection/9999").status_code, 404)
        self.assertEqual(self.client.post("/play/9999999").status_code, 404)

    def test_collection_playback_and_artwork_mutations(self):
        self.assertTrue(
            models.toggle_library_collection_item(
                self.database_path, 1, "tt0816692"
            )
        )
        self.assertFalse(
            models.toggle_library_collection_item(
                self.database_path, 1, "tt0816692"
            )
        )
        with self.assertRaises(ValueError):
            models.toggle_library_collection_item(
                self.database_path, 2, "tt11555014"
            )
        self.assertTrue(
            models.toggle_library_collection_item(
                self.database_path, 2, "tt6226232"
            )
        )

        with closing(self.connect()) as database:
            before = database.execute(
                """
                SELECT local_path
                  FROM title_artwork
                 WHERE title_id = 'tt0816692'
                   AND artwork_type = 'poster'
                   AND position = 2
                """
            ).fetchone()[0]
        models.set_library_primary_artwork(
            self.database_path, "tt0816692", "poster", 2
        )
        with closing(self.connect()) as database:
            primary = database.execute(
                """
                SELECT local_path
                  FROM title_artwork
                 WHERE title_id = 'tt0816692'
                   AND artwork_type = 'poster'
                   AND position = 1
                """
            ).fetchone()[0]
            self.assertEqual(primary, before)
            self.assertEqual(database.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(database.execute("PRAGMA foreign_key_check").fetchall(), [])

        response = self.client.post(
            "/manager/tt0816692/artwork",
            data={
                "poster_position": "2",
                "backdrop_position": "2",
                "logo_position": "2",
                "return_to": "/media",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith("/media"))
        with closing(self.connect()) as database:
            primary_types = database.execute(
                """
                SELECT COUNT(DISTINCT artwork_type)
                  FROM title_artwork
                 WHERE title_id = 'tt0816692'
                   AND artwork_type IN ('poster', 'landscape_poster', 'logo')
                   AND position = 1
                """
            ).fetchone()[0]
            self.assertEqual(primary_types, 3)
            self.assertEqual(database.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(database.execute("PRAGMA foreign_key_check").fetchall(), [])

        playback_id = models.save_library_playback_progress(
            self.database_path, 3, 1_000
        )
        models.save_library_playback_progress(
            self.database_path, 3, 2_000, playback_id=playback_id
        )
        with closing(self.connect()) as database:
            row = database.execute(
                "SELECT position_ms FROM playback_progress WHERE playback_id = ?",
                (playback_id,),
            ).fetchone()
            self.assertEqual(row["position_ms"], 2_000)

    def test_play_route_uses_one_history_row_per_session(self):
        with closing(self.connect()) as database:
            before = database.execute(
                "SELECT COUNT(*) FROM playback_progress WHERE file_id = 3"
            ).fetchone()[0]

        with patch.object(alist, "get_video_link", return_value="https://media.test/a.mkv"), patch.object(
            player, "play_video"
        ) as play_video:
            response = self.client.post("/play/3")
            self.assertEqual(response.status_code, 204)
            callback = play_video.call_args.kwargs["progress_callback"]
            callback(1_000, 100_000)
            callback(2_000, 100_000)

        with closing(self.connect()) as database:
            rows = database.execute(
                """
                SELECT position_ms
                  FROM playback_progress
                 WHERE file_id = 3
                 ORDER BY playback_id
                """
            ).fetchall()
            self.assertEqual(len(rows), before + 1)
            self.assertEqual(rows[-1]["position_ms"], 2_000)

    def test_series_resume_thresholds_and_collection_identity(self):
        page = self.build_title("tt6226232")
        self.assertEqual(page["selected_episode"]["episode_id"], "tt11591954")
        self.assertEqual(page["resume_episode"]["episode_id"], "tt11591954")
        self.assertTrue(page["resume_episode"]["play_label"].startswith("继续播放"))
        self.assertEqual(page["favorite"]["title_id"], "tt6226232")
        self.assertTrue(page["favorite"]["action_url"].endswith("/tt6226232/toggle"))

        with closing(self.connect()) as database, database:
            database.execute(
                "UPDATE media_file SET duration_ms = 600000 WHERE file_id = 14"
            )
            database.execute(
                "UPDATE playback_progress SET position_ms = 480000 WHERE file_id = 14"
            )
        page = self.build_title("tt6226232")
        self.assertEqual(page["resume_episode"]["episode_id"], "tt11591954")

        with closing(self.connect()) as database, database:
            database.execute(
                "UPDATE playback_progress SET position_ms = 480001 WHERE file_id = 14"
            )
        page = self.build_title("tt6226232")
        self.assertEqual(page["resume_episode"]["episode_id"], "tt11599999")
        self.assertTrue(page["resume_episode"]["play_label"].startswith("播放"))
        resume_page = self.build_title("tt6226232", select_resume=True)
        self.assertEqual(
            resume_page["selected_episode"]["episode_id"],
            "tt11599999",
        )
        self.assertEqual(
            resume_page["playback_episode"]["episode_id"],
            "tt11599999",
        )
        rendered = self.client.get("/title/tt6226232?resume=1").get_data(
            as_text=True
        )
        self.assertIn('data-episode-hero="tt11599999"', rendered)
        response = self.client.post("/title/tt6226232/resume")
        self.assertEqual(response.status_code, 307)
        self.assertTrue(response.headers["Location"].endswith("/play/15"))

        with closing(self.connect()) as database, database:
            database.execute(
                "UPDATE playback_progress SET position_ms = 540000 WHERE file_id = 14"
            )
        ninety_percent = self.build_title("tt6226232", select_resume=True)
        self.assertEqual(
            ninety_percent["selected_episode"]["episode_id"],
            "tt11599999",
        )

        self.assertFalse(is_effectively_completed(480000, 600000))
        self.assertTrue(is_effectively_completed(480001, 600000))
        self.assertTrue(is_effectively_completed(540000, 600000))

        models.toggle_library_collection_item(
            self.database_path, 2, "tt6226232"
        )
        with closing(self.connect()) as database:
            items = get_collection_items(database, 2)
        self.assertEqual([item["title_id"] for item in items], ["tt6226232"])

    def test_completed_movie_restarts_and_uses_non_continue_label(self):
        with closing(self.connect()) as database, database:
            database.execute(
                """
                INSERT INTO playback_progress (file_id, position_ms, watched_at)
                VALUES (3, 9126000, '2026-08-30 10:00:00')
                """
            )
        page = self.build_title("tt0816692")
        self.assertFalse(page["play_label"].startswith("继续播放"))

        with patch.object(
            alist, "get_video_link", return_value="https://media.test/movie.mkv"
        ), patch.object(player, "play_video") as play_video:
            self.assertEqual(self.client.post("/play/3").status_code, 204)
        self.assertEqual(play_video.call_args.kwargs["start_position_ms"], 0)

    def test_artwork_batch_rolls_back_as_one_transaction(self):
        with closing(self.connect()) as database:
            before = database.execute(
                """
                SELECT local_path
                  FROM title_artwork
                 WHERE title_id = 'tt0816692'
                   AND artwork_type = 'poster'
                   AND position = 1
                """
            ).fetchone()[0]
        response = self.client.post(
            "/manager/tt0816692/artwork",
            data={
                "poster_position": "2",
                "backdrop_position": "999",
                "return_to": "/media",
            },
        )
        self.assertEqual(response.status_code, 404)
        with closing(self.connect()) as database:
            after = database.execute(
                """
                SELECT local_path
                  FROM title_artwork
                 WHERE title_id = 'tt0816692'
                   AND artwork_type = 'poster'
                   AND position = 1
                """
            ).fetchone()[0]
        self.assertEqual(after, before)

    def test_import_writer_rejects_cross_title_alist_remap_atomically(self):
        with closing(self.connect()) as database:
            existing_path = database.execute(
                "SELECT alist_path FROM media_file ORDER BY file_id LIMIT 1"
            ).fetchone()[0]

        title = {
            "title_id": "tt9000001",
            "tmdb_id": 9001,
            "title_type": "movie",
            "primary_title": "Import Movie",
            "original_title": "Import Movie",
            "original_language": "en",
            "display_title": "导入电影",
            "is_adult": 0,
            "start_year": 2026,
            "end_year": None,
            "runtime_minutes": 120,
            "genres": "Drama",
            "overview": "电影简介",
            "tagline": None,
            "average_rating": 8.1,
            "num_votes": 1000,
        }
        with self.assertRaisesRegex(ValueError, "已关联其他影视条目"):
            models.import_media_bundle(
                self.database_path,
                titles=[title],
                relations=[],
                artwork=[],
                target_title_id=title["title_id"],
                alist_path=existing_path,
                probe={},
            )

        with closing(self.connect()) as database:
            inserted = database.execute(
                "SELECT 1 FROM title_basic WHERE title_id = 'tt9000001'"
            ).fetchone()
        self.assertIsNone(inserted)

    def test_manager_filtering_is_server_owned(self):
        html = self.client.get("/media?q=星际&type=movie").get_data(as_text=True)
        self.assertIn('id="overview-search-form" role="search" data-auto-submit', html)
        self.assertNotIn("function applyFilter", html)
        self.assertNotIn("request.full_path", html)

    def test_batch_import_reports_full_rollback(self):
        result = {
            "successes": [],
            "failures": [
                {
                    "index": 2,
                    "title_id": "tt9000002",
                    "message": "模拟失败",
                }
            ],
        }
        with patch.object(
            movie_app,
            "import_media_batch_service",
            return_value=result,
        ) as import_batch:
            response = self.client.post(
                "/manager/import/batch",
                data={
                    "title_id": ["tt9000001", "tt9000002"],
                    "season_number": ["", "1"],
                    "episode_number": ["", "1"],
                    "alist_url": [
                        "http://127.0.0.1:5244/d/Movies/Import.mkv?sign=one",
                        "http://127.0.0.1:5244/d/Series/S01E01.mkv?sign=two",
                    ],
                },
                follow_redirects=True,
            )

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("批量录入失败，全部内容均未写入", html)
        self.assertIn("第 2 条（tt9000002）：模拟失败", html)
        self.assertIn('data-category="danger"', html)
        self.assertNotIn('data-category="warning"', html)
        items = import_batch.call_args.kwargs["items"]
        self.assertEqual(items[0]["title_id"], "tt9000001")
        self.assertEqual(items[1]["season_number"], "1")

    def test_batch_import_accepts_json_upload_and_rejects_invalid_json(self):
        payload = {
            "items": [
                {
                    "title_id": "tt9000002",
                    "season_number": 1,
                    "episode_number": 2,
                    "alist_url": "http://127.0.0.1:5244/d/Series/S01E02.mkv?sign=three",
                }
            ]
        }
        with patch.object(
            movie_app,
            "import_media_batch_service",
            return_value={"successes": [{"index": 1}], "failures": []},
        ) as import_batch:
            response = self.client.post(
                "/manager/import/batch",
                data={
                    "json_file": (
                        io.BytesIO(json.dumps(payload).encode("utf-8")),
                        "media.json",
                    )
                },
                follow_redirects=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("批量录入完成：成功 1 条", response.get_data(as_text=True))
        self.assertIn("data-flash-toast", response.get_data(as_text=True))
        self.assertIn('data-category="success"', response.get_data(as_text=True))
        self.assertNotIn("alert-success", response.get_data(as_text=True))
        item = import_batch.call_args.kwargs["items"][0]
        self.assertEqual(item["season_number"], "1")
        self.assertEqual(item["episode_number"], "2")

        with patch.object(movie_app, "import_media_batch_service") as invalid_batch:
            invalid = self.client.post(
                "/manager/import/batch",
                data={"json_file": (io.BytesIO(b"{bad"), "invalid.json")},
                follow_redirects=True,
            )
        self.assertIn(
            "JSON 文件不是有效的 UTF-8 JSON 数据",
            invalid.get_data(as_text=True),
        )
        invalid_batch.assert_not_called()

    def test_json_import_template_is_valid_and_complete(self):
        response = self.client.get(
            "/static/templates/media_import_template.json"
        )
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.get_data(as_text=True))
        response.close()
        self.assertGreaterEqual(len(payload["items"]), 2)
        self.assertEqual(
            set(payload["items"][0]),
            {"title_id", "season_number", "episode_number", "alist_url"},
        )

    def test_title_id_import_accepts_movie_series_and_episode(self):
        movie_metadata = {
            "title_basic": {
                "tmdb_id": 9001,
                "overview": "电影简介",
                "tagline": "电影标语",
                "original_language": "en",
            },
            "title_artwork": [],
            "title_cast": [
                {
                    "title_id": "tt9000001",
                    "tmdb_person_id": 90001,
                    "name": "导入演员",
                    "character_name": "主角",
                    "cast_order": 0,
                    "remote_path": None,
                    "local_path": None,
                }
            ],
            "episodes": [],
        }
        series_metadata = {
            "title_basic": {
                "tmdb_id": 9002,
                "overview": "剧集简介",
                "tagline": "剧集标语",
                "original_language": "en",
            },
            "title_artwork": [],
            "title_cast": [],
            "episodes": [
                {
                    "season_number": 1,
                    "episode_number": 1,
                    "title_basic": {
                        "tmdb_id": 9003,
                        "overview": "第一集简介",
                        "tagline": None,
                        "original_language": "en",
                    },
                    "title_artwork": [],
                },
                {
                    "season_number": 1,
                    "episode_number": 2,
                    "title_basic": {
                        "tmdb_id": 9004,
                        "overview": "第二集简介",
                        "tagline": None,
                        "original_language": "en",
                    },
                    "title_artwork": [],
                },
            ],
        }

        def metadata(title_id, session=None, known_episode_ids=None):
            base = fake_media_metadata(title_id)
            extra = movie_metadata if title_id == "tt9000001" else series_metadata
            base["title_basic"].update(extra["title_basic"])
            base["title_cast"] = extra["title_cast"]
            for item, more in zip(base["episodes"], extra["episodes"]):
                item["title_basic"].update(more["title_basic"])
            return base

        probe = {
            "duration_ms": 7_200_000,
            "file_size_bytes": 1024,
            "bit_rate": 8_000_000,
            "container_format": "matroska,webm",
            "video_codec": "hevc",
            "video_width": 3840,
            "video_height": 2160,
            "video_frame_rate": 23.976,
            "video_pixel_format": "yuv420p10le",
            "video_dynamic_range": "HDR10 / PQ",
            "audio_codec": "eac3",
            "audio_channels": 6,
            "audio_channel_layout": "5.1",
        }
        with patch.object(
            media_import_service.alist, "validate_alist_video_url"
        ), patch.object(
            media_import_service.alist,
            "get_video_link",
            return_value="https://media.test/file.mkv",
        ), patch.object(
            media_import_service.media_probe, "probe_video", return_value=probe
        ), patch.object(
            media_import_service.tmdb,
            "build_media_metadata",
            side_effect=metadata,
        ), patch.object(
            media_import_service,
            "fetch_title_rating",
            side_effect=lambda title_id: {
                "title_id": title_id,
                "status": "success",
                "ratings": {
                    "mdblist_score": 88,
                    "rt_critics_score": 94,
                },
                "message": None,
            },
        ):
            movie = self.client.post(
                "/manager/import/batch",
                data={
                    "title_id": "tt9000001",
                    "season_number": "",
                    "episode_number": "",
                    "alist_url": "http://127.0.0.1:5244/d/Movies/Import.mkv?sign=one",
                },
            )
            series_episode = self.client.post(
                "/manager/import/batch",
                data={
                    "title_id": "tt9000002",
                    "season_number": "1",
                    "episode_number": "1",
                    "alist_url": "http://127.0.0.1:5244/d/Series/S01E01.mkv?sign=two",
                },
            )
            direct_episode = self.client.post(
                "/manager/import/batch",
                data={
                    "title_id": "tt9000004",
                    "season_number": "",
                    "episode_number": "",
                    "alist_url": "http://127.0.0.1:5244/d/Series/S01E02.mkv?sign=three",
                },
            )

        self.assertEqual(movie.status_code, 302)
        self.assertEqual(series_episode.status_code, 302)
        self.assertEqual(direct_episode.status_code, 302)
        with closing(self.connect()) as database:
            imported_titles = database.execute(
                """
                SELECT title_id,
                       title_type,
                       display_title,
                       average_rating,
                       mdblist_score,
                       rt_critics_score
                  FROM title_basic
                 WHERE title_id LIKE 'tt900%'
                 ORDER BY title_id
                """
            ).fetchall()
            imported_files = database.execute(
                """
                SELECT title_id, alist_path, duration_ms
                  FROM media_file
                 WHERE title_id LIKE 'tt900%'
                 ORDER BY alist_path
                """
            ).fetchall()
            relations = database.execute(
                """
                SELECT episode_id, series_id, season_number, episode_number
                  FROM title_relation
                 WHERE series_id = 'tt9000002'
                 ORDER BY episode_number
                """
            ).fetchall()
            imported_cast = database.execute(
                "SELECT name, character_name FROM title_cast WHERE title_id = ?",
                ("tt9000001",),
            ).fetchone()

        self.assertEqual(len(imported_titles), 4)
        self.assertEqual(imported_titles[0]["display_title"], "导入电影")
        self.assertEqual(imported_titles[0]["average_rating"], 8.1)
        self.assertEqual(imported_titles[0]["mdblist_score"], 88)
        self.assertEqual(imported_titles[0]["rt_critics_score"], 94)
        self.assertEqual(imported_titles[1]["mdblist_score"], 88)
        self.assertEqual(
            [row["title_id"] for row in imported_files],
            ["tt9000001", "tt9000003", "tt9000004"],
        )
        self.assertTrue(all(row["duration_ms"] == 7_200_000 for row in imported_files))
        self.assertEqual(len(relations), 2)
        self.assertEqual(tuple(imported_cast), ("导入演员", "主角"))

    def test_series_title_id_requires_position_before_external_calls(self):
        with patch.object(
            media_import_service.tmdb, "build_media_metadata"
        ) as fetch_metadata:
            response = self.client.post(
                "/manager/import/batch",
                data={
                    "title_id": "tt9000002",
                    "season_number": "",
                    "episode_number": "",
                    "alist_url": "http://127.0.0.1:5244/d/Series/Test.mkv?sign=abc",
                },
                follow_redirects=True,
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn("必须填写有效的季号和集号", response.get_data(as_text=True))
        fetch_metadata.assert_not_called()

    def test_rating_refresh_routes_delegate_to_service(self):
        status = {
            "running": True,
            "total": 2,
            "completed": 0,
            "succeeded": 0,
            "failed": 0,
            "current_title_id": None,
        }
        with patch.object(
            movie_app,
            "get_rating_title_ids",
            return_value=["tt0816692", "tt6226232"],
        ), patch.object(
            movie_app,
            "start_rating_refresh",
            return_value={**status, "started": True},
        ) as start_refresh:
            response = self.client.post("/manager/ratings/refresh")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["started"])
        start_refresh.assert_called_once_with(
            self.database_path,
            ["tt0816692", "tt6226232"],
        )

        with patch.object(
            movie_app,
            "get_rating_refresh_status",
            return_value=status,
        ):
            response = self.client.get("/manager/ratings/refresh")
        self.assertEqual(response.get_json(), status)

    def test_rendered_local_static_references_exist(self):
        references = set()
        for path in (
            "/",
            "/top",
            "/title/tt0816692",
            "/title/tt6226232?season=3&episode=12",
            "/media?q=星际&type=movie",
        ):
            html = self.client.get(path).get_data(as_text=True)
            references.update(
                unquote(match)
                for match in re.findall(r'(?:src|href)="/static/([^"?#]+)', html)
            )
        missing = [
            reference
            for reference in references
            if not (movie_app.BASE_DIR / "static" / reference).is_file()
        ]
        self.assertGreater(len(references), 4)
        self.assertEqual(missing, [])

    def test_batch_import_failure_rolls_back_entire_transaction(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_db = Path(temp_dir) / "test_rollback.db"
            temp_images = Path(temp_dir) / "images"
            temp_images.mkdir()
            shutil.copyfile(self.database_path, temp_db)

            # Get initial counts
            with closing(sqlite3.connect(temp_db)) as db:
                before_titles = db.execute("SELECT COUNT(*) FROM title_basic").fetchone()[0]
                before_files = db.execute("SELECT COUNT(*) FROM media_file").fetchone()[0]

            items = [
                {"title_id": "tt9000002", "season_number": "1", "episode_number": "1", "alist_url": "http://127.0.0.1:5244/d/Newsroom/S01E01.mkv?sign=abc"},
                {"title_id": "tt9000002", "season_number": "1", "episode_number": "2", "alist_url": "http://127.0.0.1:5244/d/Newsroom/S01E02.mkv?sign=abc"},
            ]

            # Intentionally fail on item 2
            def probe_side_effect(url):
                if "S01E02" in url:
                    raise ValueError("Simulated probe error on E02")
                return {
                    "duration_ms": 3600000,
                    "file_size_bytes": 100000,
                    "bit_rate": 1000,
                    "container_format": "matroska",
                    "video_codec": "hevc",
                    "video_width": 1920,
                    "video_height": 1080,
                    "video_frame_rate": 24.0,
                    "video_pixel_format": "yuv420p",
                    "video_dynamic_range": None,
                    "audio_codec": "aac",
                    "audio_channels": 2,
                    "audio_channel_layout": "stereo",
                }

            artwork_path = "images/tt9000002/backdrops/1.jpg"
            metadata = {
                "title_basic": {},
                "title_artwork": [
                    {
                        "title_id": "tt9000002",
                        "artwork_type": "landscape_poster",
                        "position": 1,
                        "remote_path": "/backdrop.jpg",
                        "local_path": artwork_path,
                    }
                ],
                "episodes": [],
            }

            def stage_images(payload, image_root, **options):
                for image in payload["title_artwork"]:
                    relative = Path(*Path(image["local_path"]).parts[1:])
                    target = Path(image_root) / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(b"staged image")
                return {"downloaded": 1, "skipped": 0, "failed": 0}

            with patch.object(alist, "validate_alist_video_url", return_value=None), \
                 patch.object(alist, "get_video_link", side_effect=lambda p: f"http://127.0.0.1:5244/d{p}"), \
                 patch.object(media_probe, "probe_video", side_effect=probe_side_effect), \
                 patch.object(media_import_service, "fetch_title_rating", return_value={"status": "skipped", "ratings": {}}), \
                 patch.object(tmdb, "build_media_metadata", side_effect=fake_metadata_with(metadata["title_artwork"])), \
                 patch.object(tmdb, "download_metadata_images", side_effect=stage_images):

                result = media_import_service.import_media_batch(
                    library_database=temp_db,
                    image_root=temp_images,
                    items=items,
                )

            self.assertEqual(len(result["successes"]), 0)
            self.assertEqual(len(result["failures"]), 1)
            self.assertEqual(result["failures"][0]["index"], 2)

            # Check that database has 0 partial leftovers
            with closing(sqlite3.connect(temp_db)) as db:
                after_titles = db.execute("SELECT COUNT(*) FROM title_basic").fetchone()[0]
                after_files = db.execute("SELECT COUNT(*) FROM media_file").fetchone()[0]

            self.assertEqual(before_titles, after_titles)
            self.assertEqual(before_files, after_files)
            self.assertFalse((Path(temp_dir) / artwork_path).exists())
            self.assertFalse((Path(temp_dir) / "thumbnails" / artwork_path).exists())

    def test_batch_write_failure_removes_published_artwork(self):
        image_root = Path(self.temporary_directory.name) / "images"
        artwork_path = "images/tt9000002/backdrops/1.jpg"
        metadata = {
            "title_basic": {},
            "title_artwork": [
                {
                    "title_id": "tt9000002",
                    "artwork_type": "landscape_poster",
                    "position": 1,
                    "remote_path": "/backdrop.jpg",
                    "local_path": artwork_path,
                }
            ],
            "episodes": [],
        }

        def stage_images(payload, staging_root, **options):
            for image in payload["title_artwork"]:
                target = Path(staging_root) / Path(*Path(image["local_path"]).parts[1:])
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"staged image")
            return {"downloaded": 1, "skipped": 0, "failed": 0}

        original_import = media_import_service.import_media_bundle
        write_count = 0

        def fail_second_write(**kwargs):
            nonlocal write_count
            write_count += 1
            if write_count == 2:
                raise RuntimeError("simulated write failure")
            return original_import(**kwargs)

        items = [
            {"title_id": "tt9000003", "season_number": "", "episode_number": "", "alist_url": "http://127.0.0.1:5244/d/Series/E01.mkv?sign=one"},
            {"title_id": "tt9000004", "season_number": "", "episode_number": "", "alist_url": "http://127.0.0.1:5244/d/Series/E02.mkv?sign=two"},
        ]
        with closing(self.connect()) as database:
            before_files = database.execute("SELECT COUNT(*) FROM media_file").fetchone()[0]

        with patch.object(alist, "validate_alist_video_url", return_value=None), \
             patch.object(alist, "get_video_link", return_value="https://media.test/episode"), \
             patch.object(media_probe, "probe_video", return_value={"duration_ms": 1000}), \
             patch.object(media_import_service, "fetch_title_rating", return_value={"status": "skipped", "ratings": {}}), \
             patch.object(tmdb, "build_media_metadata", side_effect=fake_metadata_with(metadata["title_artwork"])), \
             patch.object(tmdb, "download_metadata_images", side_effect=stage_images), \
             patch.object(media_import_service, "import_media_bundle", side_effect=fail_second_write):
            result = media_import_service.import_media_batch(
                library_database=self.database_path,
                image_root=image_root,
                items=items,
            )

        with closing(self.connect()) as database:
            after_files = database.execute("SELECT COUNT(*) FROM media_file").fetchone()[0]
        self.assertEqual(result["successes"], [])
        self.assertEqual(result["failures"][0]["index"], 2)
        self.assertEqual(before_files, after_files)
        self.assertFalse((Path(self.temporary_directory.name) / artwork_path).exists())

    def test_batch_prepares_every_item_before_acquiring_write_lock(self):
        lock_state = []
        calls = 0

        def probe_video(_url):
            nonlocal calls
            calls += 1
            if calls == 2:
                with closing(sqlite3.connect(self.database_path, timeout=0.05)) as db:
                    try:
                        db.execute("BEGIN IMMEDIATE")
                        db.rollback()
                        lock_state.append("available")
                    except sqlite3.OperationalError as error:
                        lock_state.append(str(error))
            return {"duration_ms": 3_600_000}

        items = [
            {"title_id": "tt9000003", "season_number": "", "episode_number": "", "alist_url": "http://127.0.0.1:5244/d/Series/E01.mkv?sign=one"},
            {"title_id": "tt9000004", "season_number": "", "episode_number": "", "alist_url": "http://127.0.0.1:5244/d/Series/E02.mkv?sign=two"},
        ]
        with patch.object(alist, "validate_alist_video_url", return_value=None), \
             patch.object(alist, "get_video_link", side_effect=lambda path: f"https://media.test{path}"), \
             patch.object(media_probe, "probe_video", side_effect=probe_video), \
             patch.object(media_import_service, "fetch_title_rating", return_value={"status": "skipped", "ratings": {}}), \
             patch.object(media_import_service, "_build_context", wraps=media_import_service._build_context) as build_context:
            result = media_import_service.import_media_batch(
                library_database=self.database_path,
                image_root=Path(self.temporary_directory.name) / "images",
                items=items,
            )

        self.assertEqual(lock_state, ["available"])
        self.assertEqual(build_context.call_count, 1)
        self.assertEqual(len(result["successes"]), 2)
        self.assertEqual(result["failures"], [])

    def test_batch_normalizes_context_errors(self):
        item = {
            "title_id": "tt9000002",
            "season_number": "1",
            "episode_number": "1",
            "alist_url": "http://127.0.0.1:5244/d/Series/E01.mkv?sign=one",
        }
        with patch.object(
            media_import_service,
            "fetch_title_rating",
            return_value={"status": "skipped", "ratings": {}},
        ), patch.object(
            tmdb,
            "build_media_metadata",
            side_effect=requests.Timeout("tmdb timeout"),
        ):
            result = media_import_service.import_media_batch(
                library_database=self.database_path,
                image_root=Path(self.temporary_directory.name) / "images",
                items=[item],
            )

        self.assertEqual(result["successes"], [])
        self.assertEqual(result["failures"][0]["index"], 1)
        self.assertIn("tmdb timeout", result["failures"][0]["message"])

    def test_thumbnail_failure_does_not_change_committed_import(self):
        metadata = {
            "title_basic": {},
            "title_artwork": [
                {
                    "title_id": "tt9000001",
                    "artwork_type": "landscape_poster",
                    "position": 1,
                    "remote_path": "/backdrop.jpg",
                    "local_path": "images/tt9000001/backdrops/1.jpg",
                }
            ],
            "episodes": [],
        }

        def stage_images(payload, image_root, **options):
            image = payload["title_artwork"][0]
            target = Path(image_root) / Path(*Path(image["local_path"]).parts[1:])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"image")
            return {"downloaded": 1, "skipped": 0, "failed": 0}

        item = {
            "title_id": "tt9000001",
            "season_number": "",
            "episode_number": "",
            "alist_url": "http://127.0.0.1:5244/d/Movies/Movie.mkv?sign=one",
        }
        with patch.object(alist, "validate_alist_video_url", return_value=None), \
             patch.object(alist, "get_video_link", return_value="https://media.test/movie"), \
             patch.object(media_probe, "probe_video", return_value={"duration_ms": 1000}), \
             patch.object(media_import_service, "fetch_title_rating", return_value={"status": "skipped", "ratings": {}}), \
             patch.object(tmdb, "build_media_metadata", side_effect=fake_metadata_with(metadata["title_artwork"])), \
             patch.object(tmdb, "download_metadata_images", side_effect=stage_images), \
             patch.object(media_import_service, "ensure_thumbnail", side_effect=RuntimeError("thumbnail failed")):
            result = media_import_service.import_media_batch(
                library_database=self.database_path,
                image_root=Path(self.temporary_directory.name) / "images",
                items=[item],
            )

        self.assertEqual(len(result["successes"]), 1)
        self.assertEqual(result["failures"], [])
        with closing(self.connect()) as database:
            stored = database.execute(
                "SELECT title_id FROM media_file WHERE alist_path = ?",
                ("/Movies/Movie.mkv",),
            ).fetchone()
        self.assertEqual(stored["title_id"], "tt9000001")

    def test_card_image_filter_memoizes_within_request(self):
        with patch.object(
            movie_app,
            "get_card_image_path",
            side_effect=lambda path, static_root=None: path,
        ) as resolve_path:
            response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        paths = [call.args[0] for call in resolve_path.call_args_list]
        self.assertEqual(len(paths), len(set(paths)))


class RatingSyncUnitTest(unittest.TestCase):
    def test_extract_rating_fields_maps_supported_platforms(self):
        fields = rating_sync.extract_rating_fields(
            {
                "score": 87,
                "score_average": 84,
                "platform_ratings": [
                    {
                        "platform_key": "tomatoes_critic",
                        "score_100": 94.0,
                        "score_10": 9.4,
                        "score_display": "94%",
                        "votes": 320,
                    },
                    {
                        "platform_key": "letterboxd",
                        "score_100": 82.0,
                        "score_10": 8.2,
                        "score_display": "4.1/5.0",
                        "votes": 5000,
                    },
                ],
            }
        )
        self.assertEqual(fields["mdblist_score"], 87)
        self.assertEqual(fields["rt_critics_score"], 94)
        self.assertEqual(fields["rt_critics_votes"], 320)
        self.assertEqual(fields["letterboxd_score"], 4.1)

    def test_sync_title_ratings_fetches_concurrently_and_writes_serially(self):
        title_ids = ["tt1", "tt2", "tt3"]

        def fetch(title_id):
            if title_id == "tt2":
                return {
                    "title_id": title_id,
                    "status": "error",
                    "ratings": {},
                    "message": "unavailable",
                }
            return {
                "title_id": title_id,
                "status": "success",
                "ratings": {"mdblist_score": 80},
                "message": None,
            }

        progress = []
        with patch.object(
            rating_sync,
            "fetch_title_rating",
            side_effect=fetch,
        ) as fetch_rating, patch.object(
            rating_sync,
            "update_title_ratings",
            return_value=True,
        ) as update_rating:
            result = rating_sync.sync_title_ratings(
                "library.db",
                title_ids,
                max_workers=2,
                progress_callback=progress.append,
            )

        self.assertEqual(result, {
            "total": 3,
            "completed": 3,
            "succeeded": 2,
            "failed": 1,
        })
        self.assertEqual(fetch_rating.call_count, 3)
        self.assertEqual(update_rating.call_count, 2)
        self.assertEqual(progress[-1]["completed"], 3)


class ConnectorUnitTest(unittest.TestCase):
    def test_connector_credentials_come_from_environment(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            tmdb_connector.paths, "ENV_PATH", Path(directory) / ".env"
        ), patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "TMDB_TOKEN"):
                tmdb_connector._token()
            with self.assertRaisesRegex(RuntimeError, "ALIST_USERNAME"):
                alist.get_video_link("/Movies/Test.mkv")

        with patch.dict(os.environ, {"TMDB_TOKEN": "test-token"}, clear=True):
            self.assertEqual(tmdb_connector._token(), "test-token")

    def test_alist_url_is_restricted_to_signed_local_direct_links(self):
        valid = "http://127.0.0.1:5244/d/Movies/Test.mkv?sign=abc"
        self.assertEqual(alist.parse_alist_url(valid), "/Movies/Test.mkv")

        for invalid in (
            "https://example.com/d/Movies/Test.mkv?sign=abc",
            "http://127.0.0.1:5244/d/Movies/Test.mkv",
            "http://127.0.0.1:5244/d/../secret.mkv?sign=abc",
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                alist.parse_alist_url(invalid)

    def test_ffprobe_output_is_normalized(self):
        payload = {
            "format": {
                "duration": "12.345",
                "size": "1024",
                "bit_rate": "8000000",
                "format_name": "matroska,webm",
            },
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "hevc",
                    "width": 3840,
                    "height": 2160,
                    "avg_frame_rate": "24000/1001",
                    "pix_fmt": "yuv420p10le",
                    "color_transfer": "smpte2084",
                },
                {
                    "codec_type": "audio",
                    "codec_name": "eac3",
                    "channels": 6,
                    "channel_layout": "5.1",
                },
            ],
        }
        completed = Mock(returncode=0, stdout=json.dumps(payload))
        with patch.object(media_probe.subprocess, "run", return_value=completed):
            result = media_probe.probe_video("https://media.test/movie.mkv")

        self.assertEqual(result["duration_ms"], 12_345)
        self.assertEqual(result["video_width"], 3840)
        self.assertEqual(result["video_frame_rate"], 23.976)
        self.assertEqual(result["video_dynamic_range"], "HDR10 / PQ")
        self.assertEqual(result["audio_channels"], 6)

    def test_image_download_is_capped_at_five_per_category(self):
        images = {
            "posters": [
                {"file_path": f"/poster-{i}.jpg", "iso_639_1": "zh"}
                for i in range(8)
            ],
            "logos": [
                {"file_path": f"/logo-{i}.png", "iso_639_1": "zh"}
                for i in range(7)
            ],
            "backdrops": [
                {"file_path": f"/backdrop-{i}.jpg", "iso_639_1": None}
                for i in range(6)
            ],
        }
        records = tmdb_connector.build_title_artwork("tt1234567", images)

        self.assertEqual(len(records), 15)
        self.assertEqual(
            {record["position"] for record in records},
            {1, 2, 3, 4, 5},
        )

    def test_cast_is_ordered_deduplicated_and_capped(self):
        cast = [
            {
                "id": index,
                "name": f"演员 {index}",
                "character": f"角色 {index}",
                "order": index,
                "profile_path": f"/{index}.jpg",
            }
            for index in range(1, 30)
        ]
        cast.insert(1, {**cast[0], "character": "重复角色"})

        records = tmdb_connector.build_title_cast("tt1234567", cast)

        self.assertEqual(len(records), 24)
        self.assertEqual(records[0]["tmdb_person_id"], 1)
        self.assertEqual(records[-1]["tmdb_person_id"], 24)
        self.assertEqual(records[0]["local_path"], "images/people/1.jpg")

    def test_tv_cast_uses_aggregate_credits_and_roles(self):
        payload = {
            "aggregate_credits": {
                "cast": [
                    {
                        "id": 1,
                        "name": "演员",
                        "order": 0,
                        "profile_path": None,
                        "roles": [
                            {"character": "角色 A"},
                            {"character": "角色 B"},
                        ],
                    }
                ]
            }
        }
        with patch.object(
            tmdb_connector,
            "fetch_detail",
            return_value=payload,
        ) as fetch_detail:
            records = tmdb_connector.fetch_title_cast(
                "tt1234567",
                "tv",
                42,
                session=Mock(),
            )

        fetch_detail.assert_called_once_with(
            unittest.mock.ANY,
            "tv",
            42,
            append="aggregate_credits",
        )
        self.assertEqual(records[0]["character_name"], "角色 A / 角色 B")

    def test_obsolete_omdb_connector_is_absent(self):
        for name in (
            "fetch_omdb_ratings",
            "fetch_omdb_season_episodes",
            "fetch_media_ratings",
            "parse_omdb_ratings",
            "RatingFetchError",
        ):
            self.assertFalse(hasattr(tmdb_connector, name), name)


if __name__ == "__main__":
    unittest.main()
