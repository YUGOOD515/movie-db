"""Bounded, memory-only read-ahead shared by successive player Range requests.

Completed downloads refill the window independently of the HTTP consumer. A seek
discards the old window; at most one part per worker may still be finishing.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from concurrent.futures import CancelledError, ThreadPoolExecutor

MAX_BUFFERS = 2
IDLE_SECONDS = 60


class RangeUnsupported(OSError):
    pass


class ReadAhead:
    def __init__(self, size, part_size, capacity, concurrency, fetch):
        self.size = size
        self.part_size = part_size
        self.part_count = max(concurrency, capacity // part_size)
        self.concurrency = concurrency
        self.fetch = fetch
        self.stopped = threading.Event()
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="stream-prefetch")
        self._parts = {}
        self._running = set()
        self._waiting = {}
        self._cursor = 0
        self._failed = False
        self.users = 0
        self.timer = None

    def _submit(self, start):
        future = self._executor.submit(self.fetch, start, min(start + self.part_size, self.size) - 1,
                                       self.stopped)
        self._parts[start] = future
        self._running.add(future)
        future.add_done_callback(self._completed)
        return future

    def _completed(self, future):
        with self._lock:
            self._running.discard(future)
            if future in self._parts.values() and not future.cancelled() and future.exception() is not None:
                self._failed = True
            self._fill()

    def _fill(self):
        if self.stopped.is_set() or self._failed:
            return
        # Retain two blocks behind the cursor for short backward seeks.
        behind = min(2, self.part_count - 1, self._cursor // self.part_size)
        limit = min(self.size, self._cursor + (self.part_count - behind) * self.part_size)
        for start in range(self._cursor, limit, self.part_size):
            if len(self._running) >= self.concurrency:
                break
            if start not in self._parts:
                self._submit(start)

    def read(self, offset):
        start = offset // self.part_size * self.part_size
        with self._lock:
            if self.stopped.is_set():
                raise OSError("Stream buffer closed")
            self._waiting[start] = self._waiting.get(start, 0) + 1
            self._cursor = start
            behind = min(2, self.part_count - 1, start // self.part_size)
            low = max(0, start - behind * self.part_size)
            high = start + (self.part_count - behind) * self.part_size
            for old in list(self._parts):
                if not low <= old < high and old not in self._waiting:
                    self._parts.pop(old).cancel()
            future = self._parts.get(start)
            if future is None:
                # A seek takes priority over further speculative work. The executor
                # still limits active connections if old downloads are finishing.
                future = self._submit(start)
            self._fill()
        try:
            return start, future.result()
        except CancelledError:
            raise OSError("Stream buffer closed") from None
        finally:
            with self._lock:
                self._waiting[start] -= 1
                if not self._waiting[start]:
                    self._waiting.pop(start)

    def close(self):
        with self._lock:
            self.stopped.set()
            if self.timer:
                self.timer.cancel()
            for future in list(self._parts.values()):
                future.cancel()
            self._parts.clear()
            self._executor.shutdown(wait=False, cancel_futures=True)


_lock = threading.RLock()
_buffers = OrderedDict()


def acquire(key, factory):
    with _lock:
        buffer = _buffers.get(key)
        if buffer is None:
            if len(_buffers) >= MAX_BUFFERS:
                idle = next((item for item, value in _buffers.items() if value.users == 0), None)
                if idle is None:
                    return None
                _buffers.pop(idle).close()
            buffer = _buffers[key] = factory()
        _buffers.move_to_end(key)
        buffer.users += 1
        if buffer.timer:
            buffer.timer.cancel()
            buffer.timer = None
        return buffer


def discard(key, buffer):
    with _lock:
        if _buffers.get(key) is buffer:
            _buffers.pop(key)
        buffer.close()


def release(key, buffer):
    with _lock:
        buffer.users -= 1
        if _buffers.get(key) is buffer and buffer.users == 0:
            def expire():
                with _lock:
                    if (_buffers.get(key) is buffer and buffer.users == 0
                            and buffer.timer is timer):
                        discard(key, buffer)

            timer = buffer.timer = threading.Timer(IDLE_SECONDS, expire)
            timer.daemon = True
            timer.start()


def clear(mount=None):
    with _lock:
        for key, buffer in list(_buffers.items()):
            if mount in (None, "*") or key[0].split("/")[1] == mount:
                _buffers.pop(key)
                buffer.close()
