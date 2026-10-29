import os
import re
from types import SimpleNamespace
from unittest.mock import patch

import pytest

with patch.dict(os.environ, {"ALIST_AUTOSTART": "0", "MOVIEDB_AUTOSCAN_DISABLED": "1"}):
    import app as movie_app
from connectors.drives.base import Entry, Link
from services import media_source


class FakeResponse:
    def __init__(self, status=200, content=b"", headers=None):
        self.status_code = status
        self.content = content
        self.headers = headers or {}
        self.closed = False

    def iter_content(self, size):
        for offset in range(0, len(self.content), size):
            yield self.content[offset:offset + size]

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses=None, handler=None):
        self.responses = list(responses or [])
        self.handler = handler
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.handler:
            return self.handler(url, kwargs)
        return self.responses.pop(0)


@pytest.fixture
def client(monkeypatch):
    movie_app.app.config.update(TESTING=True, READ_ONLY=False)
    monkeypatch.setattr(media_source, "parse_stream_token", lambda token: (_ for _ in ()).throw(ValueError())
                        if token == "bad" else "/quark/a.mkv")
    return movie_app.app.test_client()


def install(monkeypatch, link, session, entry=None, fresh_link=None):
    entry = entry or Entry("a.mkv", False, "a", size=25 * 1024 * 1024)
    drive = SimpleNamespace(session=session)
    calls = []

    def open_link(path, fresh=False):
        calls.append(fresh)
        return (fresh_link if fresh and fresh_link else link), entry

    monkeypatch.setattr(media_source, "open_link", open_link)
    monkeypatch.setattr(media_source, "resolve", lambda path: (drive, entry))
    return calls


def test_stream_rejects_bad_signature_and_serves_local_ranges(client, monkeypatch, tmp_path):
    assert client.get("/stream/bad/a.mkv").status_code == 403
    video = tmp_path / "a.mkv"
    video.write_bytes(b"0123456789")
    install(monkeypatch, Link(local_path=str(video)), FakeSession(),
            Entry("a.mkv", False, "a", size=10))
    response = client.get("/stream/good/a.mkv", headers={"Range": "bytes=2-5"})
    assert response.status_code == 206 and response.data == b"2345"
    assert response.headers["Content-Range"] == "bytes 2-5/10"


def test_stream_redirects_non_proxy(client, monkeypatch):
    install(monkeypatch, Link("https://offline.invalid/video", proxy=False), FakeSession())
    response = client.get("/stream/good/a.mkv")
    assert response.status_code == 302 and response.headers["Location"] == "https://offline.invalid/video"


def test_single_proxy_forwards_range_and_refreshes_auth(client, monkeypatch):
    first = FakeResponse(403)
    second = FakeResponse(206, b"2345", {"Content-Range": "bytes 2-5/10", "Content-Length": "4",
                                         "Content-Type": "video/x-matroska"})
    session = FakeSession([first, second])
    initial = Link("https://offline.invalid/old", {"X-Key": "old"}, proxy=True)
    fresh = Link("https://offline.invalid/new", {"X-Key": "new"}, proxy=True)
    calls = install(monkeypatch, initial, session, fresh_link=fresh)
    response = client.get("/stream/good/a.mkv", headers={"Range": "bytes=2-5", "If-Range": "etag"})
    assert response.status_code == 206 and response.data == b"2345"
    assert response.headers["Content-Range"] == "bytes 2-5/10"
    assert calls == [False, True]
    assert session.calls[0][1]["headers"]["Range"] == "bytes=2-5"
    assert session.calls[1][1]["headers"] == {"X-Key": "new", "Range": "bytes=2-5", "If-Range": "etag"}
    assert first.closed and second.closed


def test_segmented_proxy_requests_parts_in_order(client, monkeypatch):
    mib = 1024 * 1024
    data = b"a" * (10 * mib) + b"b" * (10 * mib) + b"c" * (5 * mib)

    def upstream(url, kwargs):
        value = kwargs["headers"]["Range"]
        start, end = map(int, re.fullmatch(r"bytes=(\d+)-(\d+)", value).groups())
        return FakeResponse(206, data[start:end + 1], {"Content-Range": f"bytes {start}-{end}/{len(data)}"})

    session = FakeSession(handler=upstream)
    install(monkeypatch, Link("https://offline.invalid/video", proxy=True, concurrency=3, part_size=10 * mib), session)
    response = client.get("/stream/good/a.mkv")
    assert response.status_code == 200 and response.data == data
    ranges = [call[1]["headers"]["Range"] for call in session.calls]
    assert ranges == [f"bytes=0-{10 * mib - 1}", f"bytes={10 * mib}-{20 * mib - 1}",
                      f"bytes={20 * mib}-{25 * mib - 1}"]
    assert response.headers["Content-Length"] == str(len(data))
    assert response.headers["Accept-Ranges"] == "bytes"


def test_segmented_proxy_honours_open_range_and_retries_part(client, monkeypatch):
    data = b"0123456789"
    attempts = []

    def upstream(url, kwargs):
        value = kwargs["headers"]["Range"]
        attempts.append(value)
        if len(attempts) == 1:
            raise ConnectionError("offline")
        start, end = map(int, re.fullmatch(r"bytes=(\d+)-(\d+)", value).groups())
        return FakeResponse(206, data[start:end + 1], {"Content-Range": f"bytes {start}-{end}/{len(data)}"})

    session = FakeSession(handler=upstream)
    calls = install(monkeypatch, Link("https://offline.invalid/video", proxy=True, concurrency=2, part_size=2),
                    session, Entry("a.mkv", False, "a", size=len(data)))
    response = client.get("/stream/good/a.mkv", headers={"Range": "bytes=5-"})
    assert response.status_code == 206 and response.data == data[5:]
    assert response.headers["Content-Range"] == "bytes 5-9/10"
    assert attempts[0] == "bytes=5-6" and attempts[1] == "bytes=5-6"
    assert calls == [False, True]


def test_head_uses_one_byte_get_and_returns_total_size(client, monkeypatch):
    session = FakeSession([FakeResponse(206, b"x", {"Content-Range": "bytes 0-0/12345", "Content-Length": "1",
                                                    "Content-Type": "video/x-matroska"})])
    install(monkeypatch, Link("https://offline.invalid/video", proxy=True), session)
    response = client.head("/stream/good/a.mkv")
    assert response.status_code == 200 and response.data == b""
    assert response.headers["Content-Length"] == "12345"
    assert session.calls[0][1]["headers"]["Range"] == "bytes=0-0"
