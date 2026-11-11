from __future__ import annotations

import mimetypes
import re
import threading
from concurrent.futures import ThreadPoolExecutor

import requests
from flask import Response, abort, redirect, request, send_file, stream_with_context
from werkzeug.exceptions import NotFound

from connectors.drives.base import DriveError
from services import media_source, stream_buffer

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
        ("Range", request.headers.get("Range")),
        ("If-Range", request.headers.get("If-Range")),
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


def _range(size):
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
    unsupported = object()

    def fetch(part_start):
        part_end = min(part_start + part_size - 1, end)
        expected = f"bytes {part_start}-{part_end}/{entry.size}"
        for attempt in range(2):
            try:
                response = _request(drive, state["link"], {"Range": f"bytes={part_start}-{part_end}"})
                try:
                    if response.status_code == 200:
                        return unsupported
                    if response.status_code == 206 and response.headers.get("Content-Range") == expected:
                        data = response.content
                        if len(data) == part_end - part_start + 1:
                            return data
                finally:
                    response.close()
            except FileNotFoundError:
                raise
            except (requests.RequestException, OSError, RuntimeError, ValueError):
                pass
            if attempt == 0:
                try:
                    state["link"] = media_source.open_link(path, fresh=True)[0]
                except FileNotFoundError:
                    raise
                except (requests.RequestException, OSError, RuntimeError, ValueError):
                    return b""
        return b""

    first = fetch(parts[0])
    if first is unsupported:
        return _single(path, drive, state["link"])
    if not first:
        return Response(status=502)

    @stream_with_context
    def body():
        executor = ThreadPoolExecutor(max_workers=concurrency)
        futures = {}
        try:
            for index in range(1, min(concurrency + 1, len(parts))):
                futures[index] = executor.submit(fetch, parts[index])
            yield first
            for index in range(1, len(parts)):
                data = futures.pop(index).result()
                if data is unsupported or not data:
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


def _buffer_fetcher(path, drive, link, size):
    state = {"link": link}
    refresh_lock = threading.Lock()

    def fetch(start, end, stopped):
        for attempt in range(2):
            if stopped.is_set():
                raise OSError("Stream buffer closed")
            current = state["link"]
            try:
                response = _request(drive, current, {"Range": f"bytes={start}-{end}",
                                                    "Accept-Encoding": "identity"})
                try:
                    if response.status_code == 200:
                        raise stream_buffer.RangeUnsupported("Upstream ignored Range")
                    content_range = response.headers.get("Content-Range", "")
                    if (response.status_code != 206
                            or content_range != f"bytes {start}-{end}/{size}"
                            or response.headers.get("Content-Encoding", "identity") != "identity"):
                        raise OSError("Invalid upstream range response")
                    data = bytearray()
                    for chunk in response.iter_content(CHUNK_SIZE):
                        if stopped.is_set():
                            raise OSError("Stream buffer closed")
                        data.extend(chunk)
                        if len(data) > end - start + 1:
                            raise OSError("Oversized upstream range response")
                    if len(data) != end - start + 1:
                        raise OSError("Incomplete upstream range response")
                    return bytes(data)
                finally:
                    response.close()
            except (stream_buffer.RangeUnsupported, FileNotFoundError):
                raise
            except (DriveError, requests.RequestException, OSError, ValueError):
                if attempt == 0 and not stopped.is_set():
                    with refresh_lock:
                        if state["link"] is current:
                            state["link"] = media_source.open_link(path, fresh=True)[0]
        raise OSError("Unable to read upstream video range")

    return fetch


def _buffered(path, drive, link, entry):
    # Conditional ranges belong to the upstream validator. Probes need only file
    # headers, and unknown-size files cannot be split safely.
    if entry.size <= 0 or request.headers.get("If-Range") or request.user_agent.string == "MovieDB-Probe":
        return _single(path, drive, link)
    requested = _range(entry.size)
    if requested is None:
        return Response(status=416, headers={"Content-Range": f"bytes */{entry.size}"})
    start, end, ranged = requested
    key = (path, id(drive), entry.size, entry.modified, link.part_size, link.prefetch_size)
    buffer = stream_buffer.acquire(key, lambda: stream_buffer.ReadAhead(
        entry.size, link.part_size, link.prefetch_size, link.concurrency,
        _buffer_fetcher(path, drive, link, entry.size)))
    if buffer is None:
        return _single(path, drive, link)
    try:
        first_start, first = buffer.read(start)
    except stream_buffer.RangeUnsupported:
        stream_buffer.discard(key, buffer)
        stream_buffer.release(key, buffer)
        return _single(path, drive, link)
    except FileNotFoundError:
        stream_buffer.discard(key, buffer)
        stream_buffer.release(key, buffer)
        raise
    except (DriveError, requests.RequestException, OSError, ValueError):
        stream_buffer.discard(key, buffer)
        stream_buffer.release(key, buffer)
        return Response("网盘暂时无法读取视频，请稍后重试", status=502)

    released = False

    def release():
        nonlocal released
        if not released:
            released = True
            stream_buffer.release(key, buffer)

    def body():
        offset, block_start, data = start, first_start, first
        try:
            while offset <= end:
                stop = min(block_start + len(data), end + 1)
                # Small writes let the local player start consuming a downloaded
                # part promptly, even if its own receive buffer is small.
                while offset < stop:
                    next_offset = min(offset + CHUNK_SIZE, stop)
                    yield data[offset - block_start:next_offset - block_start]
                    offset = next_offset
                if offset <= end:
                    block_start, data = buffer.read(offset)
        except (DriveError, requests.RequestException, OSError, ValueError):
            stream_buffer.discard(key, buffer)
            # Once headers are sent, raising closes the connection as incomplete;
            # do not disguise a failed part as a clean end of the video.
            raise
        finally:
            release()

    headers = {
        "Content-Length": str(end - start + 1),
        "Accept-Ranges": "bytes",
        "Content-Type": mimetypes.guess_type(request.path)[0] or "application/octet-stream",
    }
    if ranged:
        headers["Content-Range"] = f"bytes {start}-{end}/{entry.size}"
    result = Response(body(), status=206 if ranged else 200, headers=headers)
    result.call_on_close(release)
    return result


def _response(token):
    try:
        path = media_source.parse_stream_token(token)
    except ValueError:
        abort(403)
    link, entry = media_source.open_link(path)
    if link.local_path:
        return send_file(link.local_path, conditional=True)
    drive, _ = media_source.resolve(path)
    if request.method == "HEAD":
        return _head(path, drive, link)
    if not link.proxy:
        return redirect(link.url, 302)
    if link.prefetch_size:
        return _buffered(path, drive, link, entry)
    if link.concurrency == 0 and link.part_size == 0:
        return _single(path, drive, link)
    return _segmented(path, drive, link, entry)


def response(token):
    try:
        return _response(token)
    except (FileNotFoundError, NotFound):
        return Response("找不到挂载或文件", status=404)
    except (DriveError, requests.RequestException):
        return Response("网盘暂时无法访问，请稍后重试", status=502)
