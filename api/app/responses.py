"""Shared OpenAPI response descriptions."""
from typing import Any

from pydantic import BaseModel

from .schemas import ErrorResponse


class ValidationErrorItem(BaseModel):
    loc: list[str | int]
    msg: str
    type: str


class ValidationErrorResponse(BaseModel):
    detail: list[ValidationErrorItem]


_TEXT = {
    401: "Header `X-User-Id` fehlt oder die Benutzer-ID ist unbekannt.",
    403: "Für diesen Benutzer bzw. diese Rolle nicht erlaubt.",
    404: "Objekt nicht gefunden.",
    409: "Konflikt mit dem Zustand der Daten (Status, Eindeutigkeit, Fremdschlüssel, Überschneidung).",
    422: (
        "Ungültige Eingabe (FastAPI-Validierung, `detail` ist eine Liste) oder Verstoß gegen "
        "eine Regel der Datenbank (`ErrorResponse` mit `sqlstate`)."
    ),
    503: "Die Datenbank ist nicht erreichbar.",
}


def errs(*codes: int) -> dict[int | str, dict[str, Any]]:
    """`responses=` entries for the given status codes, all with the common error body."""
    out: dict[int | str, dict[str, Any]] = {}
    for code in codes:
        model: Any = ValidationErrorResponse | ErrorResponse if code == 422 else ErrorResponse
        out[code] = {"model": model, "description": _TEXT[code]}
    return out
