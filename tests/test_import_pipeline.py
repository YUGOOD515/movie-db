"""Exercise scheduling, resource budgets, atomicity and restart boundaries offline."""

import json
import sqlite3
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import test_library_app as fixtures
from connectors import alist, llm, media_probe, throttle, tmdb, mdblist
from models import api_usage
from services import import_jobs, import_runtime, media_discovery, media_import, recognition


def eventually(check, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        threading.Event().wait(.01)
    raise AssertionError("Timed out waiting for background work")


class ImportJobsTest(unittest.TestCase):
    connect = fixtures.LibraryIntegrationTest.connect

    def setUp(self):
        fixtures.LibraryIntegrationTest.setUp(self)
        for patcher in (
            patch.object(alist, "configured_token", return_value="offline"),
            patch.object(alist, "get_file", side_effect=lambda path, token: {
                "signed_url": alist.get_origin() + "/d" + path + "?sign=private",
                "raw_url": "https://offline.invalid" + path + "?private=raw"}),
            patch.object(media_probe, "probe_video", return_value={"duration_ms": 1000}),
            patch.object(media_import, "fetch_title_rating", return_value={"status": "no_key"}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        import_jobs.stop(self.database_path)
        import_runtime.reset()
        media_discovery._scans.pop(str(self.database_path), None)
        fixtures.LibraryIntegrationTest.tearDown(self)

    def seed(self, paths):
        state = media_discovery._empty_state("running")
        state["records"] = [{"path": "/share", "name": "Offline", "kind": "folder", "sort_time": 0,
                             "files": [{"path": path, "name": Path(path).name, "size": 100, "modified": "v1"} for path in paths]}]
        media_discovery._scans[str(self.database_path)] = state

    def selection(self, paths, title="tt9000001"):
        return {"title_id": title, "items": [{"path": path, **({"season_number": 1, "episode_number": index} if title == "tt9000002" else {})} for index, path in enumerate(paths, 1)]}

    def submit(self, selections):
        response = self.client.post("/manager/association/jobs", json={"selections": selections})
        self.assertEqual(response.status_code, 202, response.json)
        return response.json["jobs"]

    def finished(self, ids):
        def get():
            jobs = import_jobs.status(self.database_path, ids)["jobs"]
            return jobs if all(job["state"] not in ("queued", "running") for job in jobs) else None
        return eventually(get)

    def test_idempotent_submit_survives_browser_disconnect_and_hides_urls(self):
        path = "/share/movie.mkv"
        self.seed([path])
        started, release = threading.Event(), threading.Event()

        def probe(url):
            started.set()
            assert release.wait(5)
            return {"duration_ms": 1000}

        with patch.object(media_probe, "probe_video", side_effect=probe) as call:
            first = self.submit([self.selection([path])])
            self.assertTrue(started.wait(5))
            second = self.submit([self.selection([path])])
            self.assertEqual(first[0]["id"], second[0]["id"])
            # No browser stream is being consumed while the work completes.
            release.set()
            result = self.finished([first[0]["id"]])[0]
            self.assertEqual(result["state"], "complete")
            self.assertEqual(call.call_count, 1)
        public = self.client.get("/manager/association/jobs").get_data(as_text=True)
        self.assertNotIn("private", public)
        with closing(import_jobs._connect(self.database_path)) as db:
            raw = dict(db.execute("SELECT payload, probes FROM import_job").fetchone())
        self.assertNotIn("private", json.dumps(raw))
        with closing(self.connect()) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM media_file WHERE alist_path = ?", (path,)).fetchone()[0], 1)

    def test_core_commits_while_images_and_ratings_are_still_running(self):
        path = "/share/movie.mkv"
        self.seed([path])
        release = threading.Event()

        def blocked(*args):
            assert release.wait(5)
            return {"status": "no_key"}

        try:
            with patch.object(media_import, "_stage_context_images", side_effect=blocked), patch.object(media_import, "fetch_title_rating", side_effect=blocked):
                job = self.submit([self.selection([path])])[0]
                eventually(lambda: import_jobs.status(self.database_path, [job["id"]])["jobs"][0]["progress"].get("core") == 1)
                with closing(self.connect()) as db:
                    self.assertIsNotNone(db.execute("SELECT file_id FROM media_file WHERE alist_path = ?", (path,)).fetchone())
                self.assertEqual(import_jobs.status(self.database_path, [job["id"]])["jobs"][0]["state"], "running")
                release.set()
                self.assertEqual(self.finished([job["id"]])[0]["state"], "complete")
        finally:
            release.set()

    def test_multiple_titles_overlap_but_share_one_probe_limit(self):
        movies = [f"/share/movie{i}.mkv" for i in range(3)]
        episodes = [f"/share/S01E0{i}.mkv" for i in (1, 2)]
        self.seed(movies + episodes)
        barrier = threading.Barrier(2, timeout=5)
        metadata = fixtures.fake_media_metadata

        def build(title_id, **kwargs):
            barrier.wait()
            return metadata(title_id, **kwargs)

        lock, release, three = threading.Lock(), threading.Event(), threading.Event()
        active = peak = 0

        def probe(url):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                if active == 3:
                    three.set()
            try:
                assert release.wait(5)
                return {"duration_ms": 1000}
            finally:
                with lock:
                    active -= 1

        try:
            with patch.object(throttle, "ffprobe_workers", return_value=3), patch.object(tmdb, "build_media_metadata", side_effect=build), patch.object(media_probe, "probe_video", side_effect=probe):
                jobs = self.submit([self.selection(movies), self.selection(episodes, "tt9000002")])
                self.assertTrue(three.wait(5))
                release.set()
                self.assertTrue(all(job["state"] == "complete" for job in self.finished([job["id"] for job in jobs])))
                self.assertEqual(peak, 3)
        finally:
            release.set()

    def test_link_and_probe_do_not_have_a_batch_barrier(self):
        paths = ["/share/a.mkv", "/share/b.mkv"]
        self.seed(paths)
        first_probe = threading.Event()

        def link(path, token):
            if path == paths[1]:
                assert first_probe.wait(5), "second link blocked the first probe"
            return {"signed_url": alist.get_origin() + "/d" + path + "?sign=s", "raw_url": "https://offline.invalid" + path}

        def probe(url):
            first_probe.set()
            return {"duration_ms": 1000}

        with patch.object(alist, "get_file", side_effect=link), patch.object(media_probe, "probe_video", side_effect=probe):
            jobs = self.submit([self.selection(paths)])
            self.assertEqual(self.finished([job["id"] for job in jobs])[0]["state"], "complete")

    def test_failed_probe_keeps_the_chunk_atomic(self):
        paths = ["/share/a.mkv", "/share/b.mkv"]
        self.seed(paths)

        def probe(url):
            if "/b.mkv" in url:
                raise ValueError("Invalid media")
            return {"duration_ms": 1000}

        with patch.object(media_probe, "probe_video", side_effect=probe):
            jobs = self.submit([self.selection(paths)])
            self.assertEqual(self.finished([job["id"] for job in jobs])[0]["state"], "failed")
        with closing(self.connect()) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM media_file WHERE alist_path LIKE '/share/%'").fetchone()[0], 0)

    def test_expired_lease_restores_probe_checkpoint_and_fences_old_worker(self):
        path = "/share/a.mkv"
        self.seed([path])
        with patch.object(import_jobs, "start"):
            job = self.submit([self.selection([path])])[0]
        old = import_jobs._claim(self.database_path)
        import_jobs._checkpoint(self.database_path, job["id"], old["owner"], path, {"duration_ms": 42})
        with closing(import_jobs._connect(self.database_path)) as db, db:
            db.execute("UPDATE import_job SET lease_until = 0")
        resumed = import_jobs._claim(self.database_path)
        self.assertNotEqual(old["owner"], resumed["owner"])
        with self.assertRaises(ValueError):
            import_jobs._guard(self.database_path, job["id"], old["owner"])
        import_jobs._execute(self.database_path, old)
        with closing(import_jobs._connect(self.database_path)) as db:
            self.assertIsNotNone(db.execute("SELECT 1 FROM import_reservation WHERE job_id = ?", (job["id"],)).fetchone())
        with patch.object(media_probe, "probe_video") as probe:
            import_jobs._execute(self.database_path, resumed)
            probe.assert_not_called()
        self.assertEqual(import_jobs.status(self.database_path, [job["id"]])["jobs"][0]["state"], "complete")

    def test_restart_after_core_commit_finishes_without_reprobing(self):
        from services.media_association import complete_association
        path = "/share/a.mkv"
        self.seed([path])
        with patch.object(import_jobs, "start"):
            job = self.submit([self.selection([path])])[0]
        old = import_jobs._claim(self.database_path)

        def progress(phase, done, total):
            if phase == "enrich":
                raise SystemExit("Simulated process exit after core commit")

        with self.assertRaises(SystemExit):
            complete_association(library_database=self.database_path, image_root=self.client.application.config["IMAGE_ROOT"],
                payload=self.selection([path]), defer_enrichment=True, on_progress=progress,
                on_probe=lambda name, probe: import_jobs._checkpoint(self.database_path, job["id"], old["owner"], name, probe))
        with closing(import_jobs._connect(self.database_path)) as db, db:
            db.execute("UPDATE import_job SET lease_until = 0")
        resumed = import_jobs._claim(self.database_path)
        with patch.object(media_probe, "probe_video") as probe:
            import_jobs._execute(self.database_path, resumed)
            probe.assert_not_called()
        self.assertEqual(import_jobs.status(self.database_path, [job["id"]])["jobs"][0]["state"], "complete")

    def test_cancelled_work_cannot_recreate_a_title(self):
        path = "/share/a.mkv"
        self.seed([path])
        entered, release = threading.Event(), threading.Event()

        def probe(url):
            entered.set()
            assert release.wait(5)
            return {"duration_ms": 1000}

        try:
            with patch.object(media_probe, "probe_video", side_effect=probe):
                job = self.submit([self.selection([path])])[0]
                self.assertTrue(entered.wait(5))
                import_jobs.cancel_title(self.database_path, "tt9000001")
                release.set()
                import_jobs.stop(self.database_path)
                self.assertEqual(import_jobs.status(self.database_path, [job["id"]])["jobs"][0]["state"], "cancelled")
            with closing(self.connect()) as db:
                self.assertIsNone(db.execute("SELECT 1 FROM title_basic WHERE title_id = 'tt9000001'").fetchone())
        finally:
            release.set()

    def test_only_one_chunk_of_a_title_can_be_claimed_at_once(self):
        paths = ["/share/v1.mkv", "/share/v2.mkv", "/share/movie.mkv"]
        self.seed(paths)
        series = {"title_id": "tt9000002", "items": [{"path": path, "season_number": 1, "episode_number": 1} for path in paths[:2]]}
        with patch.object(import_jobs, "start"):
            self.submit([series, self.selection(paths[2:])])
        first, second = import_jobs._claim(self.database_path), import_jobs._claim(self.database_path)
        self.assertNotEqual(first["title_id"], second["title_id"])
        self.assertIsNone(import_jobs._claim(self.database_path))
        import_jobs.cancel_title(self.database_path, first["title_id"])
        import_jobs.cancel_title(self.database_path, second["title_id"])

    def test_source_change_while_queued_prevents_import(self):
        path = "/share/a.mkv"
        self.seed([path])
        with patch.object(import_jobs, "start"):
            job = self.submit([self.selection([path])])[0]
        media_discovery._scans[str(self.database_path)]["records"][0]["files"][0]["modified"] = "v2"
        with patch.object(media_probe, "probe_video") as probe:
            import_jobs._execute(self.database_path, import_jobs._claim(self.database_path))
            probe.assert_not_called()
        self.assertEqual(import_jobs.status(self.database_path, [job["id"]])["jobs"][0]["state"], "failed")

    def test_persisted_snapshot_restores_a_job_accepted_mid_scan(self):
        path = "/share/a.mkv"
        self.seed([path])
        with patch.object(import_jobs, "start"):
            job = self.submit([self.selection([path])])[0]
        media_discovery._scans.pop(str(self.database_path))
        import_jobs._execute(self.database_path, import_jobs._claim(self.database_path))
        self.assertEqual(import_jobs.status(self.database_path, [job["id"]])["jobs"][0]["state"], "complete")

    def test_expired_video_link_is_refreshed_once(self):
        path = "/share/a.mkv"
        self.seed([path])
        with patch.object(media_probe, "probe_video", side_effect=[media_probe.ProbeError("expired", code="expired"), {"duration_ms": 12}]) as probe, patch.object(alist, "get_file", return_value={"signed_url": alist.get_origin() + "/d" + path + "?sign=s", "raw_url": "https://offline.invalid/file"}) as link:
            jobs = self.submit([self.selection([path])])
            self.assertEqual(self.finished([job["id"] for job in jobs])[0]["state"], "complete")
            self.assertEqual(probe.call_count, 2)
            self.assertEqual(link.call_count, 2)

    def test_partial_import_keeps_recognition_of_remaining_files(self):
        from models import discovery_store
        paths = ["/share/a.mkv", "/share/b.mkv"]
        self.seed(paths)
        record = media_discovery._scans[str(self.database_path)]["records"][0]
        discovery_store.save_recognition(self.database_path, record["path"], recognition.signature(record),
            {"status": "ready", "method": "rule", "title": {"title_id": "tt9000001", "name": "Movie", "media_type": "movie"}})
        job = self.submit([self.selection(paths[:1])])[0]
        self.assertEqual(self.finished([job["id"]])[0]["state"], "complete")
        remaining = media_discovery.scan_status(self.database_path)["records"][0]
        self.assertEqual(remaining["recognition"]["status"], "ready")
        self.assertEqual([item["path"] for item in remaining["files"]], paths[1:])

    def test_slow_enrichment_releases_slots_for_other_titles(self):
        paths = [f"/share/movie{i}.mkv" for i in range(5)]
        self.seed(paths)
        release = threading.Event()
        def metadata(title_id, **kwargs):
            value = fixtures.fake_media_metadata("tt9000001")
            value["title_basic"]["title_id"] = title_id
            return value
        def artwork(*args):
            assert release.wait(8)
        try:
            with patch.object(tmdb, "identify_title", side_effect=lambda title_id: {"title_type": "movie", "root_id": title_id}), patch.object(tmdb, "build_media_metadata", side_effect=metadata), patch.object(media_import, "_stage_context_images", side_effect=artwork):
                jobs = self.submit([self.selection([path], f"tt910000{index}") for index, path in enumerate(paths)])
                def all_committed():
                    with closing(self.connect()) as db:
                        return db.execute("SELECT COUNT(*) FROM media_file WHERE alist_path LIKE '/share/%'").fetchone()[0] == 5
                eventually(all_committed)
                self.assertFalse(release.is_set())
                release.set()
                self.assertTrue(all(job["state"] == "complete" for job in self.finished([job["id"] for job in jobs])))
        finally:
            release.set()


def test_fair_pool_round_robins_titles():
    pool = import_runtime.FairPool(1, "test-fair", capacity=8)
    entered, release, order = threading.Event(), threading.Event(), []
    def first():
        entered.set()
        assert release.wait(5)
        order.append("A0")
    try:
        first_future = pool.submit("A", first)
        assert entered.wait(5)
        futures = [pool.submit("A", order.append, "A1"), pool.submit("A", order.append, "A2"), pool.submit("B", order.append, "B")]
        release.set()
        for future in [first_future, *futures]:
            future.result(timeout=5)
        assert order == ["A0", "A1", "B", "A2"]
    finally:
        release.set()
        pool.shutdown()


def test_queued_request_observes_a_new_long_circuit():
    gate = throttle.Limiter("test", throttle.Policy(rate=1e6, burst=1, concurrency=1, max_wait=.2))
    waiting, outcome = threading.Event(), []
    original_wait = gate._free.wait
    def wait(*args):
        waiting.set()
        return original_wait(*args)
    def call():
        try:
            with gate.slot():
                outcome.append("sent")
        except throttle.RateLimitError:
            outcome.append("blocked")
    with gate.slot(), patch.object(gate._free, "wait", side_effect=wait):
        thread = threading.Thread(target=call)
        thread.start()
        assert waiting.wait(2)
        gate.pause(60)
    thread.join(2)
    assert not thread.is_alive() and outcome == ["blocked"]


def test_streaming_slots_cover_response_bodies(monkeypatch, tmp_path):
    gate = throttle.Limiter("tmdb-image", throttle.Policy(rate=1e6, burst=1e6, concurrency=1, max_wait=5))
    monkeypatch.setattr(throttle, "limiter", lambda _: gate)
    lock, active, peak = threading.Lock(), 0, 0
    class Response:
        status_code, headers = 200, {"Content-Type": "image/jpeg"}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def raise_for_status(self): pass
        def iter_content(self, **kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(.02)
            yield b"image"
            with lock:
                active -= 1
    session = Mock()
    session.get.side_effect = lambda *args, **kwargs: Response()
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(tmdb._download_image, session, f"https://offline.invalid/{i}", tmp_path / f"{i}.jpg") for i in range(8)]
        for future in futures:
            future.result(timeout=5)
    assert peak == 1


def test_shared_actor_download_is_coalesced_across_staging_directories(monkeypatch, tmp_path):
    barrier = threading.Barrier(2, timeout=5)
    def fetch(session, url, path):
        time.sleep(.02)
        path.write_bytes(b"actor")
    with patch.object(tmdb, "_fetch_image", side_effect=fetch) as download:
        def run(number):
            barrier.wait()
            tmdb._download_image(None, "https://offline.invalid/actor", tmp_path / str(number) / "actor.jpg")
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(run, range(2)))
        assert download.call_count == 1
    assert (tmp_path / "0/actor.jpg").read_bytes() == (tmp_path / "1/actor.jpg").read_bytes() == b"actor"


def test_mdblist_rolling_budget_and_cached_requests(monkeypatch):
    api_usage.reserve("account", 2, now=100000)
    api_usage.reserve("account", 2, now=100001)
    with pytest.raises(throttle.RateLimitError):
        api_usage.reserve("account", 2, now=100002)
    api_usage.reserve("account", 2, now=186402)
    response = Mock(status_code=200)
    response.json.return_value = {"title": "Offline", "year": 2026, "type": "movie", "ratings": [{"source": "imdb", "value": 8}]}
    with patch.object(mdblist.requests, "get", return_value=response) as send:
        config = mdblist.Config(mdblist_apikey="offline-test")
        assert mdblist.fetch_title_ratings("tt1", config).status == "success"
        assert mdblist.fetch_title_ratings("tt1", config).status == "success"
        assert send.call_count == 1


def test_ai_outage_has_a_fixed_retry_budget(monkeypatch):
    monkeypatch.setattr(recognition, "prepare", lambda db, record, **kwargs: {"record": record, "needs_ai": True, "result": {}})
    monkeypatch.setattr(recognition, "finish", lambda db, context, answer: context["result"])
    monkeypatch.setattr(recognition, "ai_item", lambda _: {})
    monkeypatch.setattr(llm, "batch_size", lambda: 8)
    monkeypatch.setattr(llm, "concurrency", lambda: 1)
    with patch.object(recognition, "ask_batch", side_effect=llm.LlmError("offline", status=500, retry=True)) as ask:
        stats = recognition.recognize_many(None, [{"path": str(i)} for i in range(8)])
    assert ask.call_count == 3 and stats["failed"] == 8


def test_recognition_emits_results_before_stream_finishes(monkeypatch):
    finished = threading.Event()
    def records():
        yield {"path": "first"}
        assert finished.wait(5), "recognition waited for the entire scan"
        yield {"path": "second"}
    monkeypatch.setattr(recognition, "prepare", lambda db, record, **kwargs: {"record": record, "needs_ai": False})
    monkeypatch.setattr(recognition, "finish", lambda *args: {"method": "rule"})
    results, progress = [], []
    def result(record, value):
        results.append(record["path"])
        finished.set()
    recognition.recognize_many(None, records(), on_result=result, on_progress=progress.append)
    assert results == ["first", "second"] and progress[-1]["total"] == progress[-1]["done"] == 2


def test_source_budget_does_not_occupy_workers_waiting_for_another_source():
    pool = import_runtime.FairPool(3, "source-test")
    entered, release, other_source = threading.Event(), threading.Event(), threading.Event()
    def blocking():
        entered.set()
        assert release.wait(5)
    try:
        futures = [pool.submit("A", blocking, _resource=("drive-a", 1))]
        assert entered.wait(5)
        futures.append(pool.submit("A", blocking, _resource=("drive-a", 1)))
        futures.append(pool.submit("B", other_source.set, _resource=("drive-b", 1)))
        assert other_source.wait(5)
        release.set()
        for future in futures:
            future.result(timeout=5)
    finally:
        release.set()
        pool.shutdown()


def test_mdblist_retries_each_consume_the_request_budget():
    limited = Mock(status_code=429, headers={"Retry-After": "0"})
    ok = Mock(status_code=200)
    ok.json.return_value = {"ratings": [{"source": "imdb", "value": 8}]}
    with patch.object(mdblist.requests, "get", side_effect=[limited, ok]):
        assert mdblist.fetch_title_ratings("tt2", mdblist.Config(mdblist_apikey="offline")).status == "success"
    with closing(api_usage._connect()) as db:
        assert db.execute("SELECT COUNT(*) FROM request_usage").fetchone()[0] == 2


def test_live_rate_configuration_preserves_active_requests_and_circuit(monkeypatch):
    gate = throttle.limiter("tmdb")
    with gate.slot():
        gate.pause(60)
        monkeypatch.setattr(throttle, "_policies", lambda: {"tmdb": throttle.Policy(10, 1, 2, 30)})
        throttle.configure()
        assert throttle.limiter("tmdb") is gate
        assert gate._active == 1 and gate.blocked_for() > 59
        assert gate.policy.rate == 10 and gate.concurrency == 2
