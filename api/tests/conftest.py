"""Test fixtures. The tests run against the disposable database of api/docker-compose.test.yml.

Level 1 (default): every test runs in one outer transaction that is rolled back at the end, so each test starts
on the unchanged seed data and nothing is left behind. Every statement gets its own savepoint (see Db), so an
expected database error does not poison the outer transaction.

Level 2 (marker `commit`): real commits through the real connection pool, several connections.
"""
import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from app.config import get_settings
from app.db import Db, get_db
from app.main import app

SEED_USERS = 10
ROUTES_HIT: set[tuple[str, str]] = set()


class RecordRoutes:
    """ASGI middleware, only in the tests: remembers which route templates were called."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        await self.inner(scope, receive, send)
        route = scope.get("route")
        if scope["type"] == "http" and route is not None:
            ROUTES_HIT.add((scope["method"], route.path))


app.add_middleware(RecordRoutes)


def conninfo() -> str:
    s = get_settings()
    return make_conninfo(
        host=s.db_host, port=s.db_port, dbname=s.db_name, user=s.db_user, password=s.db_password, connect_timeout=3
    )


def reset_seed() -> None:
    """Load the seed of the project (Phase2/7createDummyData.sql: TRUNCATE ... RESTART IDENTITY, then INSERT) again.

    This wipes the whole database, so it only runs with the explicit opt-in ALLOW_TEST_DB_RESET=1, which only the
    compose file of the disposable test stack sets. It makes every pytest session start from the same state: the
    committing tests of an earlier run do not matter, IDs are deterministic and the relative seed dates are fresh.
    """
    if os.environ.get("ALLOW_TEST_DB_RESET") != "1":
        return
    s = get_settings()
    script = Path(os.environ.get("SEED_SQL", "/seed/7createDummyData.sql")).read_text(encoding="utf-8")
    admin = make_conninfo(
        host=s.db_host,
        port=s.db_port,
        dbname=s.db_name,
        user=os.environ.get("DB_ADMIN_USER", "admin_user"),
        password=os.environ["DB_ADMIN_PASSWORD"],
        connect_timeout=3,
    )
    with psycopg.connect(admin, autocommit=True) as connection:
        connection.execute(script)


@pytest.fixture(scope="session", autouse=True)
def database_ready() -> None:
    """Wait for the database. Its first start ends with an error and a restart (report F1), this takes a while."""
    deadline = time.monotonic() + 240
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(conninfo(), autocommit=True) as conn:
                row = conn.execute('SELECT count(*) FROM "Users"').fetchone()
                if row is not None and row[0] >= SEED_USERS:
                    break
        except psycopg.Error as exc:
            last = exc
        time.sleep(2)
    else:
        pytest.exit(f"Test database not ready: {last}", returncode=3)
    reset_seed()


class Api:
    """TestClient with the dev identity headers as keyword arguments."""

    def __init__(self, client: TestClient) -> None:
        self.client = client

    def call(self, method: str, url: str, *, user: int | None = None, role: str | None = None, **kwargs: Any):
        headers = dict(kwargs.pop("headers", {}))
        if user is not None:
            headers["X-User-Id"] = str(user)
        if role is not None:
            headers["X-Test-Role"] = role
        return self.client.request(method, url, headers=headers, **kwargs)

    def get(self, url: str, **kw: Any):
        return self.call("GET", url, **kw)

    def post(self, url: str, **kw: Any):
        return self.call("POST", url, **kw)

    def put(self, url: str, **kw: Any):
        return self.call("PUT", url, **kw)

    def patch(self, url: str, **kw: Any):
        return self.call("PATCH", url, **kw)

    def delete(self, url: str, **kw: Any):
        return self.call("DELETE", url, **kw)


def expect(response: Any, status: int) -> Any:
    """Assert the status code and return the JSON body (None for an empty body)."""
    assert response.status_code == status, (
        f"{response.request.method} {response.request.url.path}: expected {status}, "
        f"got {response.status_code}: {response.text}"
    )
    return response.json() if response.content else None


@pytest.fixture
def conn() -> Iterator[psycopg.Connection[Any]]:
    connection = psycopg.connect(conninfo(), autocommit=True, row_factory=dict_row)
    try:
        with connection.transaction(force_rollback=True):
            yield connection
    finally:
        connection.close()


@pytest.fixture
def db(conn: psycopg.Connection[Any]) -> Db:
    return Db(conn, savepoint_per_statement=True)


@pytest.fixture
def api(db: Db) -> Iterator[Api]:
    app.dependency_overrides[get_db] = lambda: db
    try:
        yield Api(TestClient(app))
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def committing_api() -> Iterator[Api]:
    """Real pool, real commits (lifespan opens and closes the pool). Only for tests marked `commit`."""
    with TestClient(app) as client:
        yield Api(client)
