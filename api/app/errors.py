"""Error handling: ApiError, SQLSTATE -> HTTP mapping, JSON error format.

Error body: {"detail": "...", "sqlstate": "...", "constraint": "..."} (the last two only for database errors).
The database functions report every problem with a plain RAISE EXCEPTION (SQLSTATE P0001) and an
English text, so the HTTP status has to be derived from the message (see report F4).
"""
from __future__ import annotations

import logging
import re

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("api.errors")


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        detail: str,
        *,
        sqlstate: str | None = None,
        constraint: str | None = None,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.sqlstate = sqlstate
        self.constraint = constraint


def not_found(what: str) -> ApiError:
    return ApiError(404, f"{what} nicht gefunden.")


def forbidden(detail: str) -> ApiError:
    return ApiError(403, detail)


def conflict(detail: str) -> ApiError:
    return ApiError(409, detail)


def unprocessable(detail: str) -> ApiError:
    return ApiError(422, detail)


# Messages of the RAISE EXCEPTION statements in Phase2/5createFunctions.sql, first match wins.
_RAISE_RULES: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"^(User|Book copy|Lent|Lent request|Collection address) with ID \S+ does not exist\.$"), 404),
    (re.compile(r"does not exist or is not active"), 404),
    (re.compile(r"does not exist or does not belong"), 404),
    (re.compile(r"cannot lend their own"), 422),
    (re.compile(r"shipping is not available"), 422),
    (
        re.compile(
            r"currently lent out|has already requested|already lent out|has already been reviewed"
            r"|overlaps with an existing"
        ),
        409,
    ),
    (re.compile(r"does not exist, is not in \w+ status or"), 409),
    (re.compile(r"does not exist, is not returned yet or"), 409),
]


def classify_raise_exception(message: str) -> int:
    for pattern, status in _RAISE_RULES:
        if pattern.search(message):
            return status
    return 409


_STATE_MAP: dict[str, tuple[int, str]] = {
    "23505": (409, "Eindeutigkeit verletzt: der Wert existiert bereits."),
    "23503": (409, "Fremdschlüssel verletzt: der Datensatz wird noch referenziert oder der Verweis existiert nicht."),
    "23P01": (409, "Ausschluss-Constraint verletzt: Überschneidung mit einem vorhandenen Datensatz."),
    "23514": (422, "Prüfregel (CHECK) der Datenbank verletzt."),
    "23502": (422, "Pflichtfeld fehlt (NOT NULL)."),
    "22P02": (422, "Ungültiger Wert für den Datentyp."),
    "22003": (422, "Zahl außerhalb des erlaubten Bereichs."),
    "22001": (422, "Text ist zu lang."),
    "22007": (422, "Ungültiges Datum/Zeit-Format."),
    "22008": (422, "Datum/Zeit außerhalb des erlaubten Bereichs."),
    "40001": (409, "Serialisierungskonflikt, bitte erneut versuchen."),
    "40P01": (409, "Deadlock erkannt, bitte erneut versuchen."),
}


def db_error_to_api_error(exc: psycopg.Error) -> ApiError:
    sqlstate = exc.sqlstate
    constraint = exc.diag.constraint_name if exc.diag else None

    if sqlstate == "P0001":
        message = exc.diag.message_primary or "Die Datenbank hat die Aktion abgelehnt."
        return ApiError(classify_raise_exception(message), message, sqlstate=sqlstate)

    if sqlstate in _STATE_MAP:
        status, text = _STATE_MAP[sqlstate]
        if constraint:
            text = f"{text} (Constraint: {constraint})"
        return ApiError(status, text, sqlstate=sqlstate, constraint=constraint)

    if isinstance(exc, psycopg.OperationalError):
        log.warning("Database not reachable: %s", type(exc).__name__)
        return ApiError(503, "Die Datenbank ist nicht erreichbar. Bitte kurz warten und erneut versuchen.")

    if sqlstate and sqlstate.startswith("42"):
        # missing object or missing privilege: the API and the database disagree about the schema
        log.error("Database contract violated: sqlstate=%s message=%s", sqlstate, exc.diag.message_primary)
        return ApiError(
            500,
            "Vertragsbruch zwischen API und Datenbank (fehlendes Objekt oder fehlendes Recht).",
            sqlstate=sqlstate,
        )

    log.error("Unexpected database error: sqlstate=%s type=%s", sqlstate, type(exc).__name__)
    return ApiError(500, "Unerwarteter Datenbankfehler.", sqlstate=sqlstate)


def _response(err: ApiError) -> JSONResponse:
    body: dict[str, str] = {"detail": err.detail}
    if err.sqlstate:
        body["sqlstate"] = err.sqlstate
    if err.constraint:
        body["constraint"] = err.constraint
    return JSONResponse(status_code=err.status_code, content=body)


def install_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return _response(exc)

    @app.exception_handler(psycopg.Error)
    async def _db_error(_: Request, exc: psycopg.Error) -> JSONResponse:
        return _response(db_error_to_api_error(exc))

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        log.exception("Unhandled error", exc_info=exc)
        return JSONResponse(status_code=500, content={"detail": "Interner Fehler."})
