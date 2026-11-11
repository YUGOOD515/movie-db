import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from services import stream_buffer
from services.stream_buffer import ReadAhead


def test_prefetch_fills_bounded_window_without_waiting_for_consumer():
    calls = []
    full = threading.Event()

    def fetch(start, end, stopped):
        calls.append(start)
        if len(calls) == 8:
            full.set()
        return bytes(range(start, end + 1))

    buffer = ReadAhead(100, 4, 32, 3, fetch)
    try:
        assert buffer.read(0) == (0, b"\x00\x01\x02\x03")
        assert full.wait(2), "read-ahead stopped when the consumer stopped reading"
        assert sorted(calls) == list(range(0, 32, 4))
        assert len(buffer._parts) == 8
        assert buffer.read(5) == (4, b"\x04\x05\x06\x07")
        assert calls.count(4) == 1
    finally:
        buffer.close()


def test_concurrent_readers_share_inflight_part_and_seeks_discard_old_window():
    calls = []
    started, unblock = threading.Event(), threading.Event()

    def fetch(start, end, stopped):
        calls.append(start)
        if start == 0:
            started.set()
            assert unblock.wait(2)
        return bytes(range(start, end + 1))

    buffer = ReadAhead(200, 4, 32, 2, fetch)
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            first = workers.submit(buffer.read, 0)
            assert started.wait(2)
            second = workers.submit(buffer.read, 1)
            unblock.set()
            assert first.result() == second.result() == (0, b"\x00\x01\x02\x03")
        assert calls.count(0) == 1
        assert buffer.read(101) == (100, b"defg")
        assert all(92 <= start < 124 for start in buffer._parts)
        assert len(buffer._parts) <= 8
    finally:
        unblock.set()
        buffer.close()


def test_failure_stops_speculation_and_close_stops_new_reads():
    def fetch(start, end, stopped):
        raise OSError("offline")

    buffer = ReadAhead(200, 4, 32, 2, fetch)
    with pytest.raises(OSError, match="offline"):
        buffer.read(0)
    buffer.close()
    with pytest.raises(OSError, match="closed"):
        buffer.read(0)


def test_concurrent_seeks_do_not_cancel_another_readers_queued_part(monkeypatch):
    started, unblock = threading.Event(), threading.Event()
    queued = {100: threading.Event(), 160: threading.Event()}

    def fetch(start, end, stopped):
        if start == 0:
            started.set()
            assert unblock.wait(2)
        return bytes(range(start, end + 1))

    buffer = ReadAhead(200, 4, 16, 1, fetch)
    original = buffer._submit

    def submit(start):
        future = original(start)
        if start in queued:
            queued[start].set()
        return future

    monkeypatch.setattr(buffer, "_submit", submit)
    try:
        with ThreadPoolExecutor(max_workers=3) as workers:
            first = workers.submit(buffer.read, 0)
            assert started.wait(2)
            second = workers.submit(buffer.read, 100)
            assert queued[100].wait(2)
            third = workers.submit(buffer.read, 160)
            assert queued[160].wait(2)
            unblock.set()
            assert first.result() == (0, bytes(range(4)))
            assert second.result() == (100, bytes(range(100, 104)))
            assert third.result() == (160, bytes(range(160, 164)))
    finally:
        unblock.set()
        buffer.close()


def test_pool_reuses_buffers_caps_active_files_and_expires_idle(monkeypatch):
    monkeypatch.setattr(stream_buffer, "MAX_BUFFERS", 2)
    timers = []

    class Timer:
        def __init__(self, delay, callback):
            self.callback = callback
            self.cancelled = False
            timers.append(self)

        def start(self):
            pass

        def cancel(self):
            self.cancelled = True

    monkeypatch.setattr(stream_buffer.threading, "Timer", Timer)
    factory = lambda: ReadAhead(200, 4, 32, 2, lambda *args: b"test")
    first = stream_buffer.acquire(("/a/f",), factory)
    again = stream_buffer.acquire(("/a/f",), factory)
    assert first is again and first.users == 2
    second = stream_buffer.acquire(("/b/f",), factory)
    assert stream_buffer.acquire(("/c/f",), factory) is None
    stream_buffer.release(("/a/f",), first)
    stream_buffer.release(("/a/f",), first)
    old_timer = timers[-1]
    stream_buffer.acquire(("/a/f",), factory)
    stream_buffer.release(("/a/f",), first)
    old_timer.callback()
    assert not first.stopped.is_set(), "a cancelled timer expired a newly released buffer"
    timers[-1].callback()
    assert first.stopped.is_set()
    stream_buffer.clear("b")
    assert second.stopped.is_set()


def test_idle_buffer_is_evicted_before_active_buffer(monkeypatch):
    monkeypatch.setattr(stream_buffer, "MAX_BUFFERS", 2)
    factory = lambda: ReadAhead(200, 4, 32, 2, lambda *args: b"test")
    first = stream_buffer.acquire(("/a/f",), factory)
    stream_buffer.release(("/a/f",), first)
    active = stream_buffer.acquire(("/b/f",), factory)
    third = stream_buffer.acquire(("/c/f",), factory)
    assert first.stopped.is_set() and not active.stopped.is_set() and third is not None
