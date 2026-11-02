"""Connection pool and small SQL helpers. Plain SQL, no ORM."""
from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import get_settings

log = logging.getLogger("api.db")

Row = dict[str, Any]
Params = Sequence[Any] | Mapping[str, Any] | None

_pool: ConnectionPool | None = None


def open_pool() -> ConnectionPool:
    """Create the pool. Does not wait for the database: the API starts even if it is not up yet."""
    global _pool
    if _pool is not None:
        return _pool
    s = get_settings()
    conninfo = make_conninfo(
        host=s.db_host,
        port=s.db_port,
        dbname=s.db_name,
        user=s.db_user,
        password=s.db_password,
        connect_timeout=5,
        application_name="buechertausch-api",
    )
    pool = ConnectionPool(
        conninfo,
        min_size=s.db_pool_min,
        max_size=s.db_pool_max,
        timeout=s.db_pool_timeout,
        max_lifetime=1800,
        reconnect_timeout=60,
        kwargs={"autocommit": True, "row_factory": dict_row},
        # connections die when the database restarts (the init of this DB ends with an error, see report F1)
        check=ConnectionPool.check_connection,
        name="buechertausch",
        open=False,
    )
    pool.open(wait=False)
    _pool = pool
    log.info("Pool opened for %s@%s:%s/%s", s.db_user, s.db_host, s.db_port, s.db_name)
    return pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def get_pool() -> ConnectionPool:
    if _pool is None:
        raise RuntimeError("Connection pool is not open")
    return _pool


def escape_like(value: str) -> str:
    """Escape LIKE wildcards of user input (PostgreSQL default escape character is the backslash)."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def like_contains(value: str) -> str:
    return f"%{escape_like(value)}%"


class Where:
    """Collects WHERE clauses with named parameters. Clauses are code, values are always parameters."""

    def __init__(self) -> None:
        self._parts: list[str] = []
        self.params: dict[str, Any] = {}

    def add(self, clause: str, **params: Any) -> Where:
        self._parts.append(clause)
        self.params.update(params)
        return self

    def sql(self) -> str:
        return "WHERE " + " AND ".join(self._parts) if self._parts else ""


def set_clause(values: Mapping[str, Any], allowed: Iterable[str]) -> str:
    """`col = %(col)s, ...` for an UPDATE. Column names must come from the allow-list, never from user input."""
    allowed_set = set(allowed)
    unknown = set(values) - allowed_set
    if unknown or not values:
        raise ValueError(f"Unexpected columns for UPDATE: {sorted(unknown)}")
    return ", ".join(f"{column} = %({column})s" for column in values)


class Db:
    """One connection plus a few helpers.

    savepoint_per_statement=True is used by the rollback tests: the whole test runs in one outer
    transaction, and every statement gets its own savepoint, so an expected database error
    (constraint, RAISE EXCEPTION) does not poison the outer transaction. In production the pool
    connections are in autocommit mode and the flag is off.
    """

    def __init__(self, conn: psycopg.Connection[Row], *, savepoint_per_statement: bool = False) -> None:
        self.conn = conn
        self._savepoints = savepoint_per_statement

    def _execute(self, sql: str, params: Params) -> tuple[list[Row], int]:
        if self._savepoints:
            with self.conn.transaction():
                return self._exec(sql, params)
        return self._exec(sql, params)

    def _exec(self, sql: str, params: Params) -> tuple[list[Row], int]:
        cur = self.conn.execute(sql, params)
        rows = cur.fetchall() if cur.description is not None else []
        return rows, cur.rowcount

    def all(self, sql: str, params: Params = None) -> list[Row]:
        return self._execute(sql, params)[0]

    def one(self, sql: str, params: Params = None) -> Row | None:
        rows = self._execute(sql, params)[0]
        return rows[0] if rows else None

    def scalar(self, sql: str, params: Params = None) -> Any:
        row = self.one(sql, params)
        return next(iter(row.values())) if row else None

    def run(self, sql: str, params: Params = None) -> int:
        """Execute a statement and return the number of affected rows."""
        return self._execute(sql, params)[1]

    @contextmanager
    def tx(self) -> Iterator[None]:
        """Atomic block. Becomes a savepoint when an outer transaction is already open."""
        with self.conn.transaction():
            yield

    def advisory_lock(self, key: str) -> None:
        """Transaction-scoped lock on a text key. Only meaningful inside tx()."""
        self.run("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (key,))


def get_db() -> Iterator[Db]:
    """FastAPI dependency: one pool connection per request."""
    with get_pool().connection() as conn:
        yield Db(conn)
