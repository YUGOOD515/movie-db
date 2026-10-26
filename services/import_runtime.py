"""Process-wide, bounded work pools for imports (never one pool per HTTP request).

Each ready title gets a turn before another task from the same title is taken.
Pools have separate workers so waiting for an API cannot occupy a probe or image
processing worker. SQLite writes and publication/rollback share a short lock.
"""

from __future__ import annotations

import os
import threading
from collections import OrderedDict, deque
from concurrent.futures import Future, wait

from connectors import throttle

write_lock = threading.RLock()
_lock = threading.Lock()
_pools = {}


def setting(name, default, maximum):
    try:
        return max(1, min(maximum, int(os.environ.get(name, "") or default)))
    except ValueError:
        return default


class FairPool:
    def __init__(self, workers, name, capacity=128):
        self.condition = threading.Condition()
        self.queues = OrderedDict()
        self.count = 0
        self.capacity = capacity
        self.closed = False
        self.resources = {}
        self.active, self.limit, self.name = 0, workers, name
        self.threads = [threading.Thread(target=self._work, name=f"{name}-{i}", daemon=True)
                        for i in range(workers)]
        for thread in self.threads:
            thread.start()

    def submit(self, key, function, *args, _resource=None, **kwargs):
        future = Future()
        with self.condition:
            self.condition.wait_for(lambda: self.closed or self.count < self.capacity)
            if self.closed:
                raise RuntimeError("Import pool is closed")
            self.queues.setdefault(key, deque()).append((future, function, args, kwargs, _resource))
            self.count += 1
            self.condition.notify_all()
        return future

    def _work(self):
        while True:
            with self.condition:
                while True:
                    candidate = None
                    for key, tasks in self.queues.items():
                        if self.active >= self.limit:
                            break
                        for index, task in enumerate(tasks):
                            resource = task[4]
                            if resource is None or self.resources.get(resource[0], 0) < resource[1]:
                                candidate = (key, index)
                                break
                        if candidate:
                            break
                    if candidate:
                        break
                    if self.closed and not self.count:
                        return
                    self.condition.wait()
                key, index = candidate
                tasks = self.queues.pop(key)
                future, function, args, kwargs, resource = tasks[index]
                del tasks[index]
                if tasks:
                    self.queues[key] = tasks
                self.count -= 1
                self.active += 1
                if resource:
                    self.resources[resource[0]] = self.resources.get(resource[0], 0) + 1
                self.condition.notify_all()
            try:
                if future.set_running_or_notify_cancel():
                    try:
                        result = function(*args, **kwargs)
                    except BaseException as error:
                        future.set_exception(error)
                    else:
                        future.set_result(result)
            finally:
                with self.condition:
                    self.active -= 1
                    if resource:
                        self.resources[resource[0]] -= 1
                    self.condition.notify_all()

    def resize(self, workers):
        with self.condition:
            self.limit = workers
            while len(self.threads) < workers:
                thread = threading.Thread(target=self._work, name=f"{self.name}-{len(self.threads)}", daemon=True)
                self.threads.append(thread)
                thread.start()
            self.condition.notify_all()

    def shutdown(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()
        for thread in self.threads:
            thread.join()


def pool(name):
    with _lock:
        workers = {"probe": throttle.ffprobe_workers, "metadata": lambda: 4,
                   "artwork": lambda: 4, "image": lambda: 6,
                   "rating": lambda: 2, "imdb": lambda: 1,
                   "derive": lambda: setting("IMAGE_CONCURRENCY", 2, 8)}[name]()
        if name not in _pools:
            _pools[name] = FairPool(workers, f"import-{name}")
        else:
            _pools[name].resize(workers)
        return _pools[name]


class Scope:
    """Join every child before deleting staging files, including on early errors."""
    def __init__(self):
        self.lock = threading.Lock()
        self.futures = []
        self.cancelled = False

    def submit(self, name, key, function, *args, **kwargs):
        with self.lock:
            if self.cancelled:
                future = Future()
                future.cancel()
                return future
        future = pool(name).submit(key, function, *args, **kwargs)
        with self.lock:
            self.futures.append(future)
            if self.cancelled:
                future.cancel()
        return future

    def __enter__(self):
        return self

    def __exit__(self, kind, error, traceback):
        while True:
            with self.lock:
                self.cancelled = kind is not None
                futures = list(self.futures)
                if self.cancelled:
                    for future in futures:
                        future.cancel()
            wait(futures)
            with self.lock:
                if len(self.futures) == len(futures):
                    break


def reset():
    """For shutdown/tests; callers must finish their imports first."""
    with _lock:
        pools = list(_pools.values())
        _pools.clear()
    for item in pools:
        item.shutdown()
    from connectors import tmdb
    tmdb.clear_image_cache()
