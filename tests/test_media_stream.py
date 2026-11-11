import os
import re
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

with patch.dict(os.environ, {"ALIST_AUTOSTART": "0", "MOVIEDB_AUTOSCAN_DISABLED": "1"}):
    import app as movie_app
from connectors.drives.base import Entry, Link
from services import media_source, media_stream, stream_buffer


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


def test_segmented_stops_if_later_part_loses_range_support(client, monkeypatch):
    data = b"0123456789"
    session = FakeSession(handler=lambda url, kwargs: FakeResponse(200, data) if len(session.calls) == 2
                          else FakeResponse(206, b"0123", {"Content-Range": "bytes 0-3/10"}))
    install(monkeypatch, Link("https://offline.invalid/video", proxy=True, concurrency=2, part_size=4),
            session, Entry("a.mkv", False, "a", size=len(data)))
    response = client.get("/stream/good/a.mkv")
    assert response.status_code == 200 and response.data == b"0123"
    assert len(session.calls) == 2


def buffered_source(monkeypatch, data, handler=None):
    def upstream(url, kwargs):
        start, end = map(int, re.fullmatch(r"bytes=(\d+)-(\d+)", kwargs["headers"]["Range"]).groups())
        assert kwargs["headers"]["Accept-Encoding"] == "identity"
        return FakeResponse(206, data[start:end + 1], {"Content-Range": f"bytes {start}-{end}/{len(data)}"})

    session = FakeSession(handler=handler or upstream)
    link = Link("https://offline.invalid/video", proxy=True, concurrency=3, part_size=4, prefetch_size=16)
    calls = install(monkeypatch, link, session, Entry("a.mkv", False, "a", size=len(data)))
    return session, calls


@pytest.mark.parametrize(("value", "status", "expected", "content_range"), [
    (None, 200, b"0123456789abcdef", None),
    ("bytes=5-10", 206, b"56789a", "bytes 5-10/16"),
    ("bytes=5-", 206, b"56789abcdef", "bytes 5-15/16"),
    ("bytes=-3", 206, b"def", "bytes 13-15/16"),
    ("bytes=16-", 416, b"", "bytes */16"),
    ("bytes=4-2", 416, b"", "bytes */16"),
    ("bytes=0-1,4-5", 416, b"", "bytes */16"),
])
def test_buffered_http_ranges(client, monkeypatch, value, status, expected, content_range):
    buffered_source(monkeypatch, b"0123456789abcdef")
    result = client.get("/stream/good/a.mkv", headers={"Range": value} if value else {})
    assert result.status_code == status and result.data == expected
    assert result.headers.get("Content-Range") == content_range
    result.close()


def test_buffered_prefetch_continues_after_short_request_and_is_reused(client, monkeypatch):
    full = threading.Event()
    data = b"0123456789abcdef"
    fetched = set()

    def upstream(url, kwargs):
        start, end = map(int, re.fullmatch(r"bytes=(\d+)-(\d+)", kwargs["headers"]["Range"]).groups())
        fetched.add(start)
        if len(fetched) == 4:
            full.set()
        return FakeResponse(206, data[start:end + 1], {"Content-Range": f"bytes {start}-{end}/{len(data)}"})

    session, _ = buffered_source(monkeypatch, data, upstream)
    first = client.get("/stream/good/a.mkv", headers={"Range": "bytes=0-1"})
    assert first.data == b"01"
    first.close()
    assert full.wait(2), "prefetch stopped at the end of a short player request"
    second = client.get("/stream/good/a.mkv", headers={"Range": "bytes=2-11"})
    assert second.data == data[2:12]
    second.close()
    assert len(session.calls) == 4
    assert all(buffer.users == 0 for buffer in stream_buffer._buffers.values())


def test_buffered_auth_refresh_is_shared_by_parallel_workers(client, monkeypatch):
    barrier = threading.Barrier(3)
    data = b"0123456789ab"

    def upstream(url, kwargs):
        if url.endswith("old"):
            barrier.wait(2)
            return FakeResponse(403)
        start, end = map(int, re.fullmatch(r"bytes=(\d+)-(\d+)", kwargs["headers"]["Range"]).groups())
        return FakeResponse(206, data[start:end + 1], {"Content-Range": f"bytes {start}-{end}/{len(data)}"})

    session = FakeSession(handler=upstream)
    initial = Link("https://offline.invalid/old", proxy=True, concurrency=3, part_size=4, prefetch_size=16)
    fresh = Link("https://offline.invalid/new", proxy=True, concurrency=3, part_size=4, prefetch_size=16)
    calls = install(monkeypatch, initial, session, Entry("a.mkv", False, "a", size=len(data)), fresh)
    result = client.get("/stream/good/a.mkv")
    assert result.status_code == 200 and result.data == data
    result.close()
    assert calls == [False, True]


def test_buffered_falls_back_when_upstream_ignores_ranges(client, monkeypatch):
    data = b"0123456789abcdef"
    session, _ = buffered_source(monkeypatch, data, lambda *args: FakeResponse(200, data))
    result = client.get("/stream/good/a.mkv")
    assert result.status_code == 200 and result.data == data
    result.close()
    assert not stream_buffer._buffers
    assert "Range" not in session.calls[-1][1]["headers"]


@pytest.mark.parametrize("headers", [{"Range": "bytes=2-5", "If-Range": '"etag"'},
                                  {"Range": "bytes=2-5", "User-Agent": "MovieDB-Probe"}])
def test_buffered_passes_conditionals_and_probes_through(client, monkeypatch, headers):
    session, _ = buffered_source(monkeypatch, b"0123456789abcdef",
                                 lambda *args: FakeResponse(206, b"2345"))
    result = client.get("/stream/good/a.mkv", headers=headers)
    assert result.data == b"2345" and len(session.calls) == 1
    assert session.calls[0][1]["headers"]["Range"] == "bytes=2-5"
    assert not stream_buffer._buffers


@pytest.mark.parametrize("mode", ["truncated", "wrong-range", "offline"])
def test_buffered_rejects_bad_first_part(client, monkeypatch, mode):
    def upstream(url, kwargs):
        if mode == "offline":
            raise ConnectionError("offline")
        value = "bytes 0-3/16" if mode == "truncated" else "bytes 1-4/16"
        return FakeResponse(206, b"bad" if mode == "truncated" else b"0123", {"Content-Range": value})

    buffered_source(monkeypatch, b"0123456789abcdef", upstream)
    result = client.get("/stream/good/a.mkv")
    assert result.status_code == 502 and not stream_buffer._buffers


def test_buffer_lease_released_when_response_closed_without_iteration(client, monkeypatch):
    buffered_source(monkeypatch, b"0123456789abcdef")
    with movie_app.app.test_request_context("/stream/good/a.mkv"):
        result = media_stream.response("good")
        assert next(iter(stream_buffer._buffers.values())).users == 1
        result.close()
        assert next(iter(stream_buffer._buffers.values())).users == 0
