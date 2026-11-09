"""Opt-in, low-overhead SQLAlchemy lifecycle counters for isolated performance drills.

Disabled unless ``ALC_DB_PERF_DIAGNOSTICS=1`` is set before importing ``app.database``.
Callbacks retain only monotonic timestamps and aggregate counts; they never log SQL,
parameters, users, or connection URLs.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from contextvars import ContextVar, Token
from math import ceil

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine

_lock = threading.Lock()
_active_transactions: dict[int, float] = {}
_checkout_wait_samples: deque[float] = deque(maxlen=10_000)
_transaction_samples: deque[float] = deque(maxlen=10_000)
_counts = {
    "checkouts": 0,
    "checkins": 0,
    "transactions_started": 0,
    "commits": 0,
    "rollbacks": 0,
    "transaction_seconds_total": 0.0,
    "transaction_seconds_max": 0.0,
    "checkout_wait_count": 0,
    "checkout_wait_seconds_total": 0.0,
    "checkout_wait_seconds_max": 0.0,
}
_request_sql_metrics: ContextVar[dict | None] = ContextVar("request_sql_metrics", default=None)


def start_request_metrics() -> tuple[Token, dict[str, int | float]]:
    metrics: dict[str, int | float] = {
        "sql_statement_count": 0, "sql_statement_ms": 0.0,
        "pool_checkout_count": 0, "pool_checkout_ms": 0.0,
    }
    return _request_sql_metrics.set(metrics), metrics


def finish_request_metrics(token: Token) -> None:
    _request_sql_metrics.reset(token)


def install(engine: AsyncEngine) -> None:
    sync_engine = engine.sync_engine
    pool = sync_engine.pool
    original_connect = pool.connect

    def timed_connect(*args, **kwargs):
        started = time.perf_counter()
        try:
            return original_connect(*args, **kwargs)
        finally:
            elapsed = max(0.0, time.perf_counter() - started)
            with _lock:
                _counts["checkout_wait_count"] += 1
                _counts["checkout_wait_seconds_total"] += elapsed
                _counts["checkout_wait_seconds_max"] = max(
                    _counts["checkout_wait_seconds_max"], elapsed
                )
                _checkout_wait_samples.append(elapsed)
            metrics = _request_sql_metrics.get()
            if metrics is not None:
                metrics["pool_checkout_count"] += 1
                metrics["pool_checkout_ms"] += elapsed * 1000

    pool.connect = timed_connect

    def checkout(_dbapi_connection, connection_record, _connection_proxy) -> None:
        connection_record.info["alc_checked_out_at"] = time.perf_counter()
        with _lock:
            _counts["checkouts"] += 1

    def checkin(_dbapi_connection, connection_record) -> None:
        connection_record.info.pop("alc_checked_out_at", None)
        with _lock:
            _counts["checkins"] += 1

    def begin(connection) -> None:
        now = time.perf_counter()
        key = id(connection)
        connection.info["alc_transaction_key"] = key
        with _lock:
            _active_transactions[key] = now
            _counts["transactions_started"] += 1

    def finish(connection, event_name: str) -> None:
        key = connection.info.pop("alc_transaction_key", None)
        if key is None:
            return
        now = time.perf_counter()
        with _lock:
            started = _active_transactions.pop(key, None)
            if started is not None:
                elapsed = max(0.0, now - started)
                _counts["transaction_seconds_total"] += elapsed
                _counts["transaction_seconds_max"] = max(
                    _counts["transaction_seconds_max"], elapsed
                )
                _transaction_samples.append(elapsed)
            _counts[event_name] += 1

    def before_cursor_execute(connection, _cursor, _statement, _parameters, _context, _many):
        connection.info["alc_statement_started_at"] = time.perf_counter()

    def after_cursor_execute(connection, _cursor, _statement, _parameters, _context, _many):
        started = connection.info.pop("alc_statement_started_at", None)
        metrics = _request_sql_metrics.get()
        if started is not None and metrics is not None:
            metrics["sql_statement_count"] += 1
            metrics["sql_statement_ms"] += (time.perf_counter() - started) * 1000

    event.listen(sync_engine.pool, "checkout", checkout)
    event.listen(sync_engine.pool, "checkin", checkin)
    event.listen(sync_engine, "begin", begin)
    event.listen(sync_engine, "commit", lambda connection: finish(connection, "commits"))
    event.listen(sync_engine, "rollback", lambda connection: finish(connection, "rollbacks"))
    event.listen(sync_engine, "before_cursor_execute", before_cursor_execute)
    event.listen(sync_engine, "after_cursor_execute", after_cursor_execute)


def snapshot(engine: AsyncEngine) -> dict[str, int | float]:
    now = time.perf_counter()
    with _lock:
        ages = [max(0.0, now - started) for started in _active_transactions.values()]
        result = dict(_counts)
        checkout_waits = sorted(_checkout_wait_samples)
        transactions = sorted(_transaction_samples)

    def percentile(values: list[float], p: float) -> float:
        if not values:
            return 0.0
        return values[max(0, ceil(p * len(values)) - 1)]

    pool = engine.pool
    result.update(
        pool_checked_out=pool.checkedout(),
        pool_size=pool.size(),
        pool_overflow=pool.overflow(),
        open_transactions=len(ages),
        transaction_age_max=max(ages, default=0.0),
        checkout_wait_seconds_p50=percentile(checkout_waits, 0.50),
        checkout_wait_seconds_p95=percentile(checkout_waits, 0.95),
        checkout_wait_seconds_p99=percentile(checkout_waits, 0.99),
        transaction_seconds_p50=percentile(transactions, 0.50),
        transaction_seconds_p95=percentile(transactions, 0.95),
        transaction_seconds_p99=percentile(transactions, 0.99),
    )
    return result
