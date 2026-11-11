"""Compare direct playback with read-ahead against a local rate-shaped upstream.

Run: uv run python scripts/benchmark_stream_buffer.py
No library, drive credentials, media files or running MovieDB instance are used.
This measures a synthetic per-connection bottleneck, not real cloud throughput.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from flask import Flask, request
from werkzeug.serving import make_server

from connectors.drives.base import Entry, Link
from services import media_stream, stream_buffer

MIB = 1024 * 1024


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size-mib", type=int, default=24)
    parser.add_argument("--upstream-mib-s", type=float, default=4)
    parser.add_argument("--consumer-mib-s", type=float, default=8)
    args = parser.parse_args()
    if min(args.size_mib, args.upstream_mib_s, args.consumer_mib_s) <= 0:
        parser.error("sizes and rates must be positive")
    payload = bytes(range(256)) * (args.size_mib * MIB // 256)

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            match = re.fullmatch(r"bytes=(\d+)-(\d*)", self.headers.get("Range", ""))
            start, end = (int(match[1]), int(match[2]) if match[2] else len(payload) - 1) if match else (0, len(payload) - 1)
            self.send_response(206 if match else 200)
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Content-Type", "video/x-matroska")
            if match:
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(payload)}")
            self.end_headers()
            try:
                time.sleep(0.04)  # fixed request latency
                for offset in range(start, end + 1, 64 * 1024):
                    data = payload[offset:min(offset + 64 * 1024, end + 1)]
                    time.sleep(len(data) / (args.upstream_mib_s * MIB))
                    self.wfile.write(data)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    session = requests.Session()
    drive = SimpleNamespace(session=session)
    link = Link(f"http://127.0.0.1:{upstream.server_port}/a.mkv", proxy=True,
                concurrency=3, part_size=4 * MIB, prefetch_size=128 * MIB)
    entry = Entry("a.mkv", False, "a", size=len(payload))
    app = Flask(__name__)

    @app.get("/stream/a.mkv")
    def stream():
        if request.args.get("mode") == "direct":
            return media_stream._single("/bench/a.mkv", drive, link)
        return media_stream._buffered("/bench/a.mkv", drive, link, entry)

    server = make_server("127.0.0.1", 0, app, threaded=True)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    results = []
    try:
        for mode in ("direct", "buffered"):
            stream_buffer.clear()
            digest = hashlib.sha256()
            begin = last_read = time.perf_counter()
            waits = []
            first_byte = None
            received = 0
            with requests.get(f"http://127.0.0.1:{server.server_port}/stream/a.mkv?mode={mode}",
                              stream=True, timeout=(5, 20)) as response:
                response.raise_for_status()
                for data in response.iter_content(MIB):
                    now = time.perf_counter()
                    waits.append(now - last_read)
                    if first_byte is None:
                        first_byte = now - begin
                    digest.update(data)
                    received += len(data)
                    time.sleep(len(data) / (args.consumer_mib_s * MIB))
                    last_read = time.perf_counter()
            elapsed = time.perf_counter() - begin
            assert received == len(payload) and digest.digest() == hashlib.sha256(payload).digest()
            results.append({"mode": mode, "size_mib": args.size_mib, "seconds": round(elapsed, 3),
                            "effective_mib_s": round(received / MIB / elapsed, 3),
                            "first_mib_seconds": round(first_byte, 3),
                            "read_wait_seconds": round(sum(waits), 3),
                            "max_read_wait_after_start_seconds": round(max(waits[1:], default=0), 3),
                            "sha256_verified": True})
        print(json.dumps({"scenario": "local HTTP; 40 ms request latency; per-connection rate cap",
                          "upstream_mib_s_per_connection": args.upstream_mib_s,
                          "consumer_mib_s": args.consumer_mib_s, "results": results}, indent=2))
    finally:
        stream_buffer.clear()
        server.shutdown()
        server.server_close()
        upstream.shutdown()
        upstream.server_close()
        session.close()


if __name__ == "__main__":
    main()
