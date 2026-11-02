"""Error mapping without a database: how the API reacts to what the database or the pool can throw."""
import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg import errors
from psycopg_pool import PoolTimeout

from app.errors import db_error_to_api_error, install_handlers


def mapped(exc: psycopg.Error):
    err = db_error_to_api_error(exc)
    return err.status_code, err.sqlstate


@pytest.mark.parametrize(
    ("exc", "status"),
    [
        (errors.UniqueViolation("duplicate"), 409),
        (errors.ForeignKeyViolation("fk"), 409),
        (errors.ExclusionViolation("overlap"), 409),
        (errors.SerializationFailure("retry"), 409),
        (errors.DeadlockDetected("retry"), 409),
        (errors.CheckViolation("check"), 422),
        (errors.NotNullViolation("null"), 422),
        (errors.InvalidTextRepresentation("enum"), 422),
        (errors.NumericValueOutOfRange("range"), 422),
        (errors.StringDataRightTruncation("long"), 422),
        (errors.UndefinedTable("missing"), 500),
        (errors.UndefinedFunction("missing"), 500),
        (errors.InsufficientPrivilege("grant"), 500),
        (errors.InternalError("boom"), 500),
    ],
)
def test_sqlstates_map_to_http_status(exc: psycopg.Error, status: int):
    assert mapped(exc)[0] == status


@pytest.mark.parametrize("exc", [psycopg.OperationalError("down"), PoolTimeout("no connection")])
def test_connection_problems_are_503(exc: psycopg.Error):
    assert db_error_to_api_error(exc).status_code == 503


def test_handlers_return_the_common_json_shape_without_internals():
    app = FastAPI()
    install_handlers(app)

    @app.get("/db-down")
    def db_down():
        raise psycopg.OperationalError("connection to server at 10.0.0.5 failed, password authentication failed")

    @app.get("/missing")
    def missing():
        raise errors.UndefinedTable('relation "secret_table" does not exist')

    @app.get("/crash")
    def crash():
        raise RuntimeError("internal detail /srv/app/secret.py")

    client = TestClient(app, raise_server_exceptions=False)
    down = client.get("/db-down")
    assert down.status_code == 503 and "10.0.0.5" not in down.text and "password" not in down.text.replace("Bitte", "")
    missing_response = client.get("/missing")
    assert missing_response.status_code == 500
    assert "secret_table" not in missing_response.text
    assert missing_response.json()["sqlstate"] == "42P01"
    crash_response = client.get("/crash")
    assert crash_response.status_code == 500 and crash_response.json() == {"detail": "Interner Fehler."}
