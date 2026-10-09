import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

with patch.dict(os.environ, {"ALIST_AUTOSTART": "0"}):
    import app as movie_app

from services import title_deletion


class TitleDeletionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "library.db"
        self.images = self.root / "static" / "images"
        self.images.mkdir(parents=True)
        with closing(sqlite3.connect(self.db_path)) as db:
            db.executescript((movie_app.BASE_DIR / "migrations/library_schema.sql").read_text(encoding="utf-8"))
            db.executescript((movie_app.BASE_DIR / "tests/fixtures/library_fixture.sql").read_text(encoding="utf-8"))
            db.execute("INSERT INTO collection_item VALUES (2, 'tt6226232', CURRENT_TIMESTAMP)")
            for tid, person, path in [("tt6226232", 100, "images/people/shared.jpg"),
                                      ("tt3783958", 100, "images/people/shared.jpg"),
                                      ("tt6226232", 101, "images/people/exclusive.jpg")]:
                db.execute("INSERT INTO title_cast (title_id,tmdb_person_id,name,cast_order,local_path) VALUES (?,?,?,0,?)",
                           (tid, person, "Actor", path))
            db.execute("INSERT INTO title_artwork VALUES ('tt6719334','still',2,NULL,'images/tt6226232/tt6719334-still.jpg')")
            db.execute("INSERT INTO title_artwork VALUES ('tt0816692','poster',3,NULL,'images/tt0816692/posters/1.jpg')")
            db.commit()
        for path in ["images/people/shared.jpg", "images/people/exclusive.jpg", "images/tt6226232/tt6719334-still.jpg",
                     "images/tt6226232/unindexed.jpg", "images/tt0816692/posters/1.jpg",
                     "thumbnails/images/people/shared.jpg", "thumbnails/images/people/exclusive.jpg",
                     "thumbnails/images/tt6226232/tt6719334-still.jpg", "imdb.png"]:
            file = self.images.parent / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(b"image")
        self.original_config = {key: movie_app.app.config[key] for key in ("DATABASE", "IMAGE_ROOT", "TESTING")}
        movie_app.app.config.update(DATABASE=self.db_path, IMAGE_ROOT=self.images, TESTING=True)
        self.client = movie_app.app.test_client()

    def tearDown(self):
        movie_app.app.config.update(self.original_config)
        self.temp.cleanup()

    def preview(self, tid="tt6226232"):
        response = self.client.get(f"/title/{tid}/deletion")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        return response.json

    def execute(self, plan):
        return self.client.post(f"/title/{plan['title_id']}/deletion", json={"token": plan["token"]})

    def count(self, table):
        with closing(sqlite3.connect(self.db_path)) as db:
            return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_preview_is_readonly_and_detail_uses_whole_series(self):
        before = self.db_path.read_bytes()
        plan = self.preview("tt6719334")
        self.assertEqual(plan["title_id"], "tt6226232")
        self.assertEqual(plan["confirm_label"], "删除整部剧集")
        html = plan["html"]
        self.assertEqual(html.count("data-delete-episode"), 5)
        self.assertEqual(html.count("data-delete-watch "), 2)
        self.assertEqual(html.count('data-delete-shared-uses'), 1)
        self.assertIn('爱乐之城', html)
        self.assertNotIn('保留 · 共用', html)
        self.assertIn("少年谢尔顿与家人的成长故事。", html)
        self.assertIn("S3 E13", html)
        history_html = html.split('data-delete-history>', 1)[1].split('</section>', 1)[0]
        self.assertEqual(history_html.count('role="progressbar"'), 2)
        self.assertIn('aria-valuenow="25.0"', history_html)
        self.assertIn('aria-valuenow="50.0"', history_html)
        self.assertNotIn("badge", history_html)
        self.assertNotIn("10:00 / 20:00", history_html)
        self.assertIn("喜爱的剧集", html)
        self.assertIn("位于夸克网盘中的文件不会被删除。", html)
        for text in ("作品记录", "季集关系", "包括文件关联及媒体参数", "确认后无法撤销", "重新扫描"):
            self.assertNotIn(text, html)
        self.assertEqual(before, self.db_path.read_bytes())
        page = self.client.get("/title/tt6719334")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'data-delete-url="/title/tt6226232/deletion"', page.data)
        self.assertIn(b'title-deletion.js', page.data)

    def test_delete_entire_series_and_only_exclusive_assets(self):
        response = self.execute(self.preview())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["redirect_url"], "/media")
        for table in ("title_relation", "playback_progress"):
            self.assertEqual(self.count(table), 0)
        self.assertEqual(self.count("title_basic"), 2)
        self.assertEqual(self.count("media_file"), 1)
        self.assertEqual(self.count("collection"), 3)
        with closing(sqlite3.connect(self.db_path)) as db:
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertIsNone(db.execute("SELECT 1 FROM collection_item WHERE title_id='tt6226232'").fetchone())
        for path in ["images/people/shared.jpg", "thumbnails/images/people/shared.jpg", "imdb.png", "images/tt0816692/posters/1.jpg"]:
            self.assertTrue((self.images.parent / path).exists(), path)
        for path in ["people/exclusive.jpg", "tt6226232/tt6719334-still.jpg", "tt6226232/unindexed.jpg"]:
            self.assertFalse((self.images / path).exists(), path)
        self.assertFalse(list(self.images.parent.glob(".title-delete-*")))
        with self.client.session_transaction() as session:
            self.assertEqual(session["_flashes"][-1][0], "success")
        self.assertEqual(self.client.get("/title/tt6226232").status_code, 404)

    def test_movie_does_not_delete_series(self):
        preview = self.preview("tt0816692")
        self.assertEqual(preview["confirm_label"], "删除电影")
        self.assertNotIn("data-delete-history", preview["html"])
        self.assertNotIn("data-delete-seasons", preview["html"])
        self.assertEqual(self.execute(preview).status_code, 200)
        self.assertFalse((self.images / "tt0816692/posters/1.jpg").exists())
        self.assertEqual(self.count("title_relation"), 5)
        self.assertEqual(self.count("playback_progress"), 2)
        self.assertEqual(self.client.get("/title/tt6226232").status_code, 200)

    def test_token_required_and_bound_to_work(self):
        plan = self.preview()
        self.assertEqual(self.client.post("/title/tt6226232/deletion", json={}).status_code, 409)
        self.assertEqual(self.client.post("/title/tt0816692/deletion", json={"token": plan["token"]}).status_code, 409)
        self.assertEqual(self.client.post("/title/tt6226232/deletion", json={"token": plan["token"] + "invalid"}).status_code, 409)
        self.assertEqual(self.client.post("/title/tt6226232/deletion", json=[]).status_code, 409)
        self.assertEqual(self.client.post("/title/tt6226232/deletion", json=[1]).status_code, 400)
        self.assertEqual(self.count("title_basic"), 8)

    def test_changed_history_requires_new_confirmation(self):
        plan = self.preview()
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute("INSERT INTO playback_progress(file_id,position_ms) VALUES (11,100)")
            db.commit()
        self.assertEqual(self.execute(plan).status_code, 409)
        self.assertTrue((self.images / "people/exclusive.jpg").exists())
        self.assertEqual(self.execute(self.preview()).status_code, 200)

    def test_new_shared_reference_requires_new_confirmation(self):
        plan = self.preview()
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute("INSERT INTO title_cast (title_id,tmdb_person_id,name,cast_order,local_path) VALUES ('tt3783958',101,'Actor',1,'images/people/exclusive.jpg')")
            db.commit()
        self.assertEqual(self.execute(plan).status_code, 409)
        self.assertEqual(self.execute(self.preview()).status_code, 200)
        self.assertTrue((self.images / "people/exclusive.jpg").exists())
        self.assertTrue((self.images.parent / "thumbnails/images/people/exclusive.jpg").exists())

    def test_failure_restores_staged_files_and_database(self):
        plan = self.preview()
        with patch.object(title_deletion, "delete_library_titles", side_effect=sqlite3.OperationalError("locked")):
            self.assertEqual(self.execute(plan).status_code, 503)
        self.assertEqual(self.count("title_basic"), 8)
        self.assertTrue((self.images / "people/exclusive.jpg").exists())
        self.assertTrue((self.images / "tt6226232/unindexed.jpg").exists())
        self.assertFalse(list(self.images.parent.glob(".title-delete-*")))

    def test_expired_confirmation_and_changed_file(self):
        with patch("itsdangerous.timed.time.time", return_value=1000):
            expired = self.preview()
        self.assertEqual(self.execute(expired).status_code, 409)
        plan = self.preview()
        (self.images / "tt6226232/unindexed.jpg").write_bytes(b"changed image")
        self.assertEqual(self.execute(plan).status_code, 409)
        self.assertEqual(self.count("title_basic"), 8)

    def test_file_move_failure_restores_prior_moves(self):
        plan = self.preview()
        original_replace = Path.replace
        moves = 0

        def fail_second_move(path, target):
            nonlocal moves
            moves += 1
            if moves == 2:
                raise OSError("file busy")
            return original_replace(path, target)

        with patch.object(Path, "replace", fail_second_move):
            self.assertEqual(self.execute(plan).status_code, 503)
        self.assertEqual(self.count("title_basic"), 8)
        self.assertTrue((self.images / "people/exclusive.jpg").exists())
        self.assertTrue((self.images / "tt6226232/unindexed.jpg").exists())
        self.assertFalse(list(self.images.parent.glob(".title-delete-*")))

    def test_outside_paths_and_shared_image_inside_work_folder_are_preserved(self):
        outside = self.root / "outside.jpg"
        outside.write_bytes(b"preserve")
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute("INSERT INTO title_artwork VALUES ('tt6226232','still',1,NULL,'images/../../outside.jpg')")
            db.execute("INSERT INTO title_artwork VALUES ('tt3783958','still',1,NULL,'images/tt6226232/unindexed.jpg')")
            db.commit()
        self.assertEqual(self.execute(self.preview()).status_code, 200)
        self.assertEqual(outside.read_bytes(), b"preserve")
        self.assertTrue((self.images / "tt6226232/unindexed.jpg").exists())

    def test_actual_preview_content_is_escaped(self):
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute("UPDATE title_basic SET overview=? WHERE title_id='tt6226232'", ('<script>alert(1)</script>',))
            db.execute("UPDATE title_cast SET name=? WHERE title_id='tt6226232' AND tmdb_person_id=100", ('<img src=x onerror=alert(1)>',))
            db.execute("UPDATE title_basic SET display_title=? WHERE title_id='tt3783958'", ('<script>shared()</script>',))
            db.commit()
        html = self.preview()["html"]
        self.assertNotIn('<script>', html)
        self.assertNotIn('<img src=x', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('&lt;img src=x', html)
        self.assertIn('&lt;script&gt;shared()', html)

    def test_shared_photo_lists_unique_movies_and_parent_series(self):
        before = self.preview()
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute("INSERT INTO title_basic(title_id,title_type,display_title) VALUES ('tt9990001','series','共用剧集')")
            for number in (2, 3):
                tid = f'tt999000{number}'
                db.execute("INSERT INTO title_basic(title_id,title_type,display_title) VALUES (?,'episode','独立集名')", (tid,))
                db.execute("INSERT INTO title_relation VALUES (?,'tt9990001',1,?)", (tid, number))
                db.execute("INSERT INTO title_cast(title_id,tmdb_person_id,name,cast_order,local_path) "
                           "VALUES (?,100,'Actor',0,'images/people/shared.jpg')", (tid,))
            db.commit()
        html = self.preview()["html"]
        self.assertEqual(html.count('共用剧集'), 1)
        self.assertEqual(html.count('爱乐之城'), 1)
        self.assertNotIn('独立集名', html)
        self.assertNotIn('《小谢尔顿》', html)
        self.assertEqual(self.execute(before).status_code, 409)
        self.assertEqual(self.execute(self.preview()).status_code, 200)
        self.assertTrue((self.images / 'people/shared.jpg').exists())


if __name__ == "__main__":
    unittest.main()
