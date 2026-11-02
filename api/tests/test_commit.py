"""Level 2: real commits through the real connection pool, several connections, parallel requests.

Run after the rollback tests, only against the disposable test database (the data stays behind there).
"""
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from app.config import get_settings
from app.db import Db
from app.deps import CurrentUser
from app.enums import UserRole
from app.main import app, create_app
from app.routers.lents import load_lent
from app.routers.copies import _lock_own_copy
from conftest import Api, conninfo, expect

pytestmark = pytest.mark.commit


def tag() -> str:
    return uuid.uuid4().hex[:10]


def new_user(api: Api) -> int:
    t = tag()
    body = {"username": f"u{t}", "first_name": "Test", "last_name": "User", "email": f"{t}@example.com"}
    return expect(api.post("/users", json=body), 201)["user_id"]


def new_address(api: Api, user: int) -> int:
    body = {"streetname": "Testweg", "streetnummber": "1", "city": "Kassel", "state": "HE", "postal_code": "34117"}
    return expect(api.post("/collection-addresses", user=user, json=body), 201)["address_id"]


def new_owner_with_copy(api: Api, shipping: bool = False) -> tuple[int, int]:
    owner = new_user(api)
    copy = {
        "book": {"title": f"Buch {tag()}", "authors": [f"Autor {tag()}"]},
        "collection_address_id": new_address(api, owner),
        "shipping_availability": "available" if shipping else "not_available",
    }
    return owner, expect(api.post("/copies", user=owner, json=copy), 201)["book_copie_id"]


def run_parallel(*calls: Callable[[], Any]) -> list[Any]:
    """Start all calls at the same moment, each in its own thread, and collect the results."""
    barrier = threading.Barrier(len(calls))

    def wrap(call: Callable[[], Any]) -> Callable[[], Any]:
        def inner() -> Any:
            barrier.wait(timeout=10)
            return call()

        return inner

    with ThreadPoolExecutor(len(calls)) as pool:
        futures = [pool.submit(wrap(call)) for call in calls]
        return [future.result(timeout=60) for future in futures]


@pytest.fixture
def raw():
    """A second connection, independent of the API pool: only sees what was really committed."""
    with psycopg.connect(conninfo(), autocommit=True, row_factory=dict_row) as connection:
        yield connection


def count(raw, sql: str, params: tuple = ()) -> int:
    return raw.execute(sql, params).fetchone()["n"]


# ------------------------------------------------------------------ commit and rollback


def test_writes_are_really_committed(committing_api: Api, raw):
    user = new_user(committing_api)
    assert count(raw, 'SELECT count(*) AS n FROM "Users" WHERE user_id = %s', (user,)) == 1
    assert count(raw, "SELECT count(*) AS n FROM \"Protocols\" WHERE user_id = %s AND protocol_type = 'create_user'", (user,)) == 1


def test_updated_at_advances_with_real_commits(committing_api: Api):
    user = new_user(committing_api)
    first = expect(committing_api.get("/users/me", user=user), 200)["updated_at"]
    time.sleep(0.05)
    second = expect(committing_api.patch("/users/me", user=user, json={"first_name": "Neu"}), 200)["updated_at"]
    assert second > first


def test_a_full_lifecycle_is_committed_step_by_step(committing_api: Api, raw):
    api = committing_api
    owner, copy = new_owner_with_copy(api)
    borrower = new_user(api)
    lent = expect(api.post("/lents", user=borrower, json={"book_copie_id": copy}), 201)["lent_id"]
    for action, status in (("accept", "accepted"), ("hand-over", "active"), ("return", "returned")):
        expect(api.post(f"/lents/{lent}/{action}", user=owner), 200)
        assert raw.execute('SELECT lent_status::text AS s FROM "Lents" WHERE lent_id = %s', (lent,)).fetchone()["s"] == status
    expect(api.post(f"/lents/{lent}/review", user=borrower, json={"rating": 4}), 201)
    assert count(raw, 'SELECT count(*) AS n FROM "Reviews" WHERE lent_id = %s', (lent,)) == 1


def test_a_failure_in_the_middle_of_a_write_rolls_everything_back(committing_api: Api, raw, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.routers.users._protocol", boom)  # runs after the INSERT of the user
    t = tag()
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/users", json={"username": f"u{t}", "first_name": "T", "last_name": "T", "email": f"{t}@example.com"}
    )
    assert response.status_code == 500 and response.json() == {"detail": "Interner Fehler."}
    assert count(raw, 'SELECT count(*) AS n FROM "Users" WHERE username = %s', (f"u{t}",)) == 0


def test_no_connection_stays_idle_in_transaction(committing_api: Api, raw):
    api = committing_api
    owner, copy = new_owner_with_copy(api)
    borrower = new_user(api)
    expect(api.post("/lents", user=borrower, json={"book_copie_id": copy}), 201)
    expect(api.post("/lents", user=borrower, json={"book_copie_id": copy}), 409)  # an error inside a transaction
    expect(api.post("/users", json={"username": f"u{tag()}", "first_name": "T", "last_name": "T", "email": "bad"}), 422)
    expect(api.get("/copies"), 200)
    time.sleep(0.3)
    stuck = count(
        raw,
        "SELECT count(*) AS n FROM pg_stat_activity WHERE application_name = 'buechertausch-api' AND state LIKE 'idle in transaction%%'",
    )
    assert stuck == 0  # the database would kill such a session after 60 s (idle_in_transaction_session_timeout)


# ------------------------------------------------------------------ races


def test_two_acceptances_for_one_copy_have_exactly_one_winner(committing_api: Api, raw):
    api = committing_api
    for _ in range(6):
        owner, copy = new_owner_with_copy(api)
        first, second = new_user(api), new_user(api)
        lents = [expect(api.post("/lents", user=u, json={"book_copie_id": copy}), 201)["lent_id"] for u in (first, second)]
        statuses = run_parallel(*[lambda lent=lent: api.post(f"/lents/{lent}/accept", user=owner).status_code for lent in lents])
        assert sorted(statuses) == [200, 409], statuses
        assert count(raw, "SELECT count(*) AS n FROM \"Lents\" WHERE book_copie_id = %s AND lent_status = 'accepted'", (copy,)) == 1


def test_deleting_a_copy_races_with_a_new_request_without_losing_history(committing_api: Api, raw):
    api = committing_api
    outcomes = set()
    for _ in range(10):
        owner, copy = new_owner_with_copy(api)
        borrower = new_user(api)
        requested, deleted = run_parallel(
            lambda: api.post("/lents", user=borrower, json={"book_copie_id": copy}).status_code,
            lambda: api.delete(f"/copies/{copy}", user=owner).status_code,
        )
        outcomes.add((requested, deleted))
        assert (requested, deleted) in {(201, 409), (404, 204)}, (requested, deleted)
        lents = count(raw, 'SELECT count(*) AS n FROM "Lents" WHERE book_copie_id = %s', (copy,))
        assert lents == (1 if requested == 201 else 0)  # a created request is never cascaded away
        assert count(raw, 'SELECT count(*) AS n FROM "Book_copy" WHERE book_copie_id = %s', (copy,)) == (1 if requested == 201 else 0)


def test_parallel_shipping_updates_keep_a_single_row(committing_api: Api, raw):
    api = committing_api
    for _ in range(5):
        owner, copy = new_owner_with_copy(api, shipping=True)
        borrower = new_user(api)
        lent = expect(api.post("/lents", user=borrower, json={"book_copie_id": copy, "shipping": True}), 201)["lent_id"]
        expect(api.post(f"/lents/{lent}/accept", user=owner), 200)

        def put(tracking: str) -> int:
            body = {"shipping_status": "shipped", "tracking_number": f"{tracking}-{tag()}"}
            return api.put(f"/lents/{lent}/shipping", user=owner, json=body).status_code

        statuses = run_parallel(lambda: put("A"), lambda: put("B"))
        assert sorted(statuses) == [200, 201], statuses
        assert count(raw, 'SELECT count(*) AS n FROM "Shipping" WHERE lent_id = %s', (lent,)) == 1


def test_parallel_copy_creation_finds_one_book_and_one_author(committing_api: Api, raw):
    api = committing_api
    for _ in range(5):
        title, author = f"Parallel {tag()}", f"Autor {tag()}"
        owners = [(owner, new_address(api, owner)) for owner in (new_user(api), new_user(api))]

        def create(owner: int, address: int):
            body = {"book": {"title": title, "authors": [author]}, "collection_address_id": address}
            return api.post("/copies", user=owner, json=body)

        responses = run_parallel(*[lambda o=o, a=a: create(o, a) for o, a in owners])
        assert [r.status_code for r in responses] == [201, 201]
        assert len({r.json()["book_id"] for r in responses}) == 1
        assert count(raw, 'SELECT count(*) AS n FROM "Books" WHERE title = %s', (title,)) == 1
        assert count(raw, 'SELECT count(*) AS n FROM "Authors" WHERE name = %s', (author,)) == 1


def test_parallel_author_creation_is_find_or_create(committing_api: Api, raw):
    api = committing_api
    user = new_user(api)
    for _ in range(5):
        name = f"Autor {tag()}"
        statuses = run_parallel(*[lambda: api.post("/authors", user=user, json={"name": name}).status_code for _ in range(2)])
        assert sorted(statuses) == [200, 201]
        assert count(raw, 'SELECT count(*) AS n FROM "Authors" WHERE name = %s', (name,)) == 1


def test_parallel_registration_with_one_email_has_one_winner(committing_api: Api, raw):
    api = committing_api
    for _ in range(5):
        email = f"{tag()}@example.com"

        def register() -> int:
            body = {"username": f"u{tag()}", "first_name": "T", "last_name": "T", "email": email}
            return api.post("/users", json=body).status_code

        assert sorted(run_parallel(register, register)) == [201, 409]
        assert count(raw, 'SELECT count(*) AS n FROM "Users" WHERE email = %s', (email,)) == 1


def test_parallel_mark_read_logs_once_and_both_answers_say_read(committing_api: Api, raw):
    api = committing_api
    sender, receiver = new_user(api), new_user(api)
    for _ in range(5):
        message = expect(api.post("/messages", user=sender, json={"receiver_id": receiver, "content": "x"}), 201)["message_id"]
        responses = run_parallel(*[lambda: api.post(f"/messages/{message}/read", user=receiver) for _ in range(2)])
        assert [r.status_code for r in responses] == [200, 200]
        assert all(r.json()["read_status"] is True for r in responses)
        assert count(raw, "SELECT count(*) AS n FROM \"Protocols\" WHERE protocol_type = 'read_message' AND details = %s", (f"message_id {message}",)) == 1


# ------------------------------------------------------------------ the locks behind the races (deterministic)


def blocked_while_held(
    request: Callable[[], int],
    hold: Callable[[psycopg.Connection], None],
    release: Callable[[psycopg.Connection], None] = lambda connection: None,
) -> tuple[bool, int]:
    """Another session holds a lock inside an open transaction. Returns whether the API request was still waiting
    after a second, and its status once the holder committed."""
    with psycopg.connect(conninfo(), autocommit=False, row_factory=dict_row) as holder:
        hold(holder)
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(request)
            time.sleep(1.0)
            waiting = not future.done()
            release(holder)
            holder.commit()
            return waiting, future.result(timeout=20)


def test_a_new_request_waits_for_the_lock_the_delete_handler_takes(committing_api: Api, raw):
    api = committing_api
    owner, copy = new_owner_with_copy(api)
    borrower = new_user(api)
    caller = CurrentUser(user_id=owner, username="x", stored_role=UserRole.user, role=UserRole.user)

    waiting, status = blocked_while_held(
        lambda: api.post("/lents", user=borrower, json={"book_copie_id": copy}).status_code,
        hold=lambda connection: _lock_own_copy(Db(connection), copy, caller),
        release=lambda connection: connection.execute('DELETE FROM "Book_copy" WHERE book_copie_id = %s', (copy,)),
    )
    assert waiting, "create_book_request must wait for the row lock of the copy"
    assert status == 404  # it then finds the copy gone instead of creating a lent that the delete would cascade away
    assert count(raw, 'SELECT count(*) AS n FROM "Lents" WHERE book_copie_id = %s', (copy,)) == 0


def test_a_shipping_update_waits_for_the_lock_on_the_lent(committing_api: Api, raw):
    api = committing_api
    owner, copy = new_owner_with_copy(api, shipping=True)
    borrower = new_user(api)
    lent = expect(api.post("/lents", user=borrower, json={"book_copie_id": copy, "shipping": True}), 201)["lent_id"]
    expect(api.post(f"/lents/{lent}/accept", user=owner), 200)

    waiting, status = blocked_while_held(
        lambda: api.put(f"/lents/{lent}/shipping", user=owner, json={"shipping_status": "pending"}).status_code,
        hold=lambda connection: load_lent(Db(connection), lent, lock=True),
    )
    assert waiting, "PUT shipping must lock the lent (Shipping.lent_id is not unique)"
    assert status == 201


def test_copy_creation_waits_for_the_lock_on_the_book(committing_api: Api):
    api = committing_api
    owner = new_user(api)
    address = new_address(api, owner)
    title = f"Gesperrt {tag()}"
    body = {"book": {"title": title, "authors": [f"Autor {tag()}"]}, "collection_address_id": address}

    waiting, status = blocked_while_held(
        lambda: api.post("/copies", user=owner, json=body).status_code,
        hold=lambda connection: Db(connection).advisory_lock(f"book:{title.lower()}"),
    )
    assert waiting, "find-or-create of a book must hold the advisory lock of its title"
    assert status == 201


def test_author_creation_waits_for_the_lock_on_the_name(committing_api: Api):
    api = committing_api
    user = new_user(api)
    name = f"Autor {tag()}"
    waiting, status = blocked_while_held(
        lambda: api.post("/authors", user=user, json={"name": name}).status_code,
        hold=lambda connection: Db(connection).advisory_lock(f"author:{name.lower()}"),
    )
    assert waiting, "find-or-create of an author must hold the advisory lock of its name"
    assert status == 201


# ------------------------------------------------------------------ pool and availability


def test_the_pool_survives_killed_connections(committing_api: Api, raw):
    # what happens to the pool when the database restarts (its first start does that once, report F1)
    expect(committing_api.get("/health"), 200)
    killed = count(
        raw,
        "SELECT count(pg_terminate_backend(pid)) AS n FROM pg_stat_activity "
        "WHERE application_name = 'buechertausch-api' AND pid <> pg_backend_pid()",
    )
    assert killed >= 1
    for _ in range(3):
        expect(committing_api.get("/health"), 200)


def test_the_api_starts_without_a_database_and_answers_503(monkeypatch):
    monkeypatch.setenv("DB_HOST", "127.0.0.1")
    monkeypatch.setenv("DB_PORT", "1")  # nothing listens there
    monkeypatch.setenv("DB_POOL_TIMEOUT", "1")
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            started = time.monotonic()
            response = client.get("/health")
            assert response.status_code == 503
            assert time.monotonic() - started < 5
            assert "nicht erreichbar" in response.json()["detail"]
            assert "127.0.0.1" not in response.text
            assert client.get("/openapi.json").status_code == 200  # the docs work without a database
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
