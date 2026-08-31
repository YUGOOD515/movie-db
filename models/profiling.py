import os
import sqlite3
import time
from collections import Counter
from flask import Flask, current_app, g, has_request_context, request

MAX_SQL_DISPLAY_LENGTH = 120
SLOWEST_SQL_LIMIT = 3


def is_profiling_enabled(app: Flask | None = None) -> bool:
    env_override = os.environ.get("FLASK_PROFILING")
    if env_override is not None:
        return env_override.strip() in ("1", "true", "True", "TRUE")
    if app is not None:
        return bool(app.debug or app.testing)
    return False


def _record_query(sql: str, duration_seconds: float) -> None:
    if not has_request_context():
        return
    queries = getattr(g, "_profile_queries", None)
    if queries is None:
        return
    queries.append((sql, duration_seconds * 1000.0))


class ProfiledCursor(sqlite3.Cursor):
    def execute(self, sql, parameters=()):
        start = time.perf_counter()
        try:
            return super().execute(sql, parameters)
        finally:
            _record_query(sql, time.perf_counter() - start)

    def executemany(self, sql, seq_of_parameters):
        start = time.perf_counter()
        try:
            return super().executemany(sql, seq_of_parameters)
        finally:
            _record_query(sql, time.perf_counter() - start)

    def executescript(self, sql_script):
        start = time.perf_counter()
        try:
            return super().executescript(sql_script)
        finally:
            _record_query(sql_script, time.perf_counter() - start)


class ProfiledConnection(sqlite3.Connection):
    def cursor(self, factory=ProfiledCursor):
        return super().cursor(factory=factory)

    def execute(self, sql, parameters=()):
        start = time.perf_counter()
        try:
            return super().execute(sql, parameters)
        finally:
            _record_query(sql, time.perf_counter() - start)

    def executemany(self, sql, seq_of_parameters):
        start = time.perf_counter()
        try:
            return super().executemany(sql, seq_of_parameters)
        finally:
            _record_query(sql, time.perf_counter() - start)

    def executescript(self, sql_script):
        start = time.perf_counter()
        try:
            return super().executescript(sql_script)
        finally:
            _record_query(sql_script, time.perf_counter() - start)


def _normalize_sql(sql: str) -> str:
    return " ".join(sql.split())


def _truncate(text: str, limit: int = MAX_SQL_DISPLAY_LENGTH) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def start_profiling_request() -> None:
    if not is_profiling_enabled(current_app):
        return
    if request.path.startswith("/static/"):
        return
    g._profile_start_time = time.perf_counter()
    g._profile_queries = []


def finish_profiling_request(response):
    if not hasattr(g, "_profile_start_time"):
        return response

    total_time_ms = (time.perf_counter() - g._profile_start_time) * 1000.0
    queries: list[tuple[str, float]] = getattr(g, "_profile_queries", [])
    sql_count = len(queries)
    sql_total_time_ms = sum(d for _, d in queries)
    slowest_sql_time_ms = max((d for _, d in queries), default=0.0)

    normalized_queries = [(_normalize_sql(sql), dur) for sql, dur in queries]
    sql_counts = Counter(norm for norm, _ in normalized_queries)
    sql_durations: dict[str, float] = {}
    for norm, dur in normalized_queries:
        sql_durations[norm] = sql_durations.get(norm, 0.0) + dur

    duplicate_statements = {
        sql: count for sql, count in sql_counts.items() if count > 1
    }
    total_duplicated_executions = sum(
        count - 1 for count in duplicate_statements.values()
    )

    req_path = request.full_path.rstrip("?") if request.full_path else request.path
    lines = [
        "[PROFILE] ------------------------------------------------------------",
        f"[PROFILE] Request:          {request.method} {req_path}",
        f"[PROFILE] Status:           {response.status_code}",
        f"[PROFILE] total_time:       {total_time_ms:.2f} ms",
        f"[PROFILE] sql_count:        {sql_count}",
        f"[PROFILE] sql_execute_time: {sql_total_time_ms:.2f} ms",
        f"[PROFILE] slowest_sql_time: {slowest_sql_time_ms:.2f} ms",
    ]

    if sql_count > 0:
        lines.append(
            f"[PROFILE] duplicate_sql:    {len(duplicate_statements)} distinct SQL repeated ({total_duplicated_executions} redundant runs)"
        )

        sorted_by_dur = sorted(queries, key=lambda item: item[1], reverse=True)
        lines.append(
            f"[PROFILE] --- Slowest SQL (Top {min(SLOWEST_SQL_LIMIT, len(queries))}) ---"
        )
        for idx, (raw_sql, dur) in enumerate(
            sorted_by_dur[:SLOWEST_SQL_LIMIT], 1
        ):
            lines.append(
                f"[PROFILE]   #{idx} [{dur:.2f} ms] {_truncate(_normalize_sql(raw_sql))}"
            )

        if duplicate_statements:
            lines.append("[PROFILE] --- Repeated SQL (Execution Counts) ---")
            for sql, count in sorted(
                duplicate_statements.items(), key=lambda x: x[1], reverse=True
            ):
                dur = sql_durations[sql]
                lines.append(
                    f"[PROFILE]   [x{count}, total {dur:.2f} ms] {_truncate(sql)}"
                )

    lines.append("[PROFILE] ------------------------------------------------------------")
    print("\n".join(lines), flush=True)

    return response


def init_profiler(app: Flask) -> None:
    if getattr(app, "_profiler_initialized", False):
        return
    app._profiler_initialized = True
    app.before_request(start_profiling_request)
    app.after_request(finish_profiling_request)
