from __future__ import annotations

import mimetypes
import re
from concurrent.futures import ThreadPoolExecutor

from flask import Response, abort, redirect, send_file, stream_with_context

from services import media_source

CHUNK_SIZE = 1024 * 1024
AUTH_STATUSES = {401, 403, 410}
CONTENT_HEADERS = ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "Last-Modified", "ETag")


def _request(drive, link, headers=None):
    request_headers = dict(link.headers)
    request_headers.update(headers or {})
    return drive.session.get(link.url, headers=request_headers, stream=True, timeout=(10, 60))


def _headers(response):
    return {name: response.headers[name] for name in CONTENT_HEADERS if name in response.headers}


def _single(path, drive, link):
    headers = {name: value for name, value in (
        ("Range", media_source_request_header("Range")),
        ("If-Range", media_source_request_header("If-Range")),
    ) if value}
    response = _request(drive, link, headers)
    if response.status_code in AUTH_STATUSES:
        response.close()
        link, _ = media_source.open_link(path, fresh=True)
        response = _request(drive, link, headers)

    def body():
        try:
            yield from response.iter_content(CHUNK_SIZE)
        finally:
            response.close()

    return Response(stream_with_context(body()), status=response.status_code, headers=_headers(response),
                    direct_passthrough=True)


def media_source_request_header(name):
    from flask import request
    return request.headers.get(name)


def _range(size):
    from flask import request
    value = request.headers.get("Range")
    if not value:
        return 0, size - 1, False
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", value.strip())
    if not match or size <= 0:
        return None
    first, last = match.groups()
    if not first:
        if not last or int(last) <= 0:
            return None
        length = min(int(last), size)
        return size - length, size - 1, True
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start >= size or end < start:
        return None
    return start, end, True


def _head(path, drive, link):
    response = _request(drive, link, {"Range": "bytes=0-0"})
    if response.status_code in AUTH_STATUSES:
        response.close()
        link, _ = media_source.open_link(path, fresh=True)
        response = _request(drive, link, {"Range": "bytes=0-0"})
    try:
        headers = _headers(response)
        match = re.fullmatch(r"bytes\s+\d+-\d+/(\d+)", response.headers.get("Content-Range", ""))
        if match:
            headers["Content-Length"] = match.group(1)
        headers.setdefault("Accept-Ranges", "bytes")
        result = Response(status=200, direct_passthrough=True)
        result.headers.update(headers)
        return result
    finally:
        response.close()


def _segmented(path, drive, link, entry):
    from flask import request
    if entry.size <= 0:
        return Response("文件大小未知", status=502)
    requested = _range(entry.size)
    if requested is None:
        return Response(status=416, headers={"Content-Range": f"bytes */{entry.size}"})
    start, end, ranged = requested
    concurrency = link.concurrency or 2
    part_size = link.part_size or 8 * 1024 * 1024
    parts = list(range(start, end + 1, part_size))
    state = {"link": link}

    def fetch(part_start):
        import requests
        part_end = min(part_start + part_size - 1, end)
        for attempt in range(2):
            try:
                response = _request(drive, state["link"], {"Range": f"bytes={part_start}-{part_end}"})
                try:
                    if response.status_code in (200, 206):
                        return response.content
                finally:
                    response.close()
            except (requests.RequestException, OSError, RuntimeError, ValueError):
                pass
            if attempt == 0:
                try:
                    state["link"] = media_source.open_link(path, fresh=True)[0]
                except (requests.RequestException, OSError, RuntimeError, ValueError):
                    return b""
        return b""

    @stream_with_context
    def body():
        executor = ThreadPoolExecutor(max_workers=concurrency)
        futures = {}
        try:
            for index in range(min(concurrency, len(parts))):
                futures[index] = executor.submit(fetch, parts[index])
            for index in range(len(parts)):
                data = futures.pop(index).result()
                if not data:
                    return
                yield data
                next_index = index + concurrency
                if next_index < len(parts):
                    futures[next_index] = executor.submit(fetch, parts[next_index])
        finally:
            for future in futures.values():
                future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)

    length = end - start + 1
    headers = {
        "Content-Length": str(length),
        "Accept-Ranges": "bytes",
        "Content-Type": mimetypes.guess_type(request.path)[0] or "application/octet-stream",
    }
    if ranged:
        headers["Content-Range"] = f"bytes {start}-{end}/{entry.size}"
    return Response(body(), status=206 if ranged else 200, headers=headers)


def response(token):
    try:
        path = media_source.parse_stream_token(token)
    except ValueError:
        abort(403)
    link, entry = media_source.open_link(path)
    if link.local_path:
        return send_file(link.local_path, conditional=True)
    drive, _ = media_source.resolve(path)
    from flask import request
    if request.method == "HEAD":
        return _head(path, drive, link)
    if not link.proxy:
        return redirect(link.url, 302)
    if link.concurrency == 0 and link.part_size == 0:
        return _single(path, drive, link)
    return _segmented(path, drive, link, entry)
