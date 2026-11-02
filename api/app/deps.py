"""Request dependencies: identity (dev header), roles, pagination."""
import re
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Path, Query, Security
from fastapi.security import APIKeyHeader

from .db import Db, get_db
from .enums import UserRole
from .errors import ApiError

_user_id_header = APIKeyHeader(
    name="X-User-Id",
    scheme_name="DevUserId",
    auto_error=False,
    description=(
        "**Nur für Tests.** ID eines Benutzers aus der Datenbank (Seed: 1 = Alice … 10 = Jürgen). "
        "Es gibt keinen Login und kein Passwort, jeder kann jeden Benutzer annehmen."
    ),
)

_USER_ID = re.compile(r"[1-9][0-9]{0,17}")


@dataclass(frozen=True)
class CurrentUser:
    user_id: int
    username: str
    stored_role: UserRole
    role: UserRole  # effective role (X-Test-Role overrides the stored one)

    @property
    def is_staff(self) -> bool:
        return self.role in (UserRole.moderator, UserRole.admin)

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.admin


def current_user(
    db: Annotated[Db, Depends(get_db)],
    x_user_id: Annotated[str | None, Security(_user_id_header)] = None,
    x_test_role: Annotated[
        UserRole | None,
        Header(
            alias="X-Test-Role",
            description=(
                "Optional, **nur für Tests**: überschreibt die Rolle des Benutzers für diese Anfrage. "
                "Nötig, weil alle Seed-Benutzer die Rolle `user` haben."
            ),
        ),
    ] = None,
) -> CurrentUser:
    if x_user_id is None or not _USER_ID.fullmatch(x_user_id.strip()):
        raise ApiError(
            401,
            "Header X-User-Id fehlt oder ist keine gültige Benutzer-ID (in Swagger UI: Authorize).",
        )
    row = db.one(
        'SELECT user_id, username, role, is_active FROM "Users" WHERE user_id = %s',
        (int(x_user_id.strip()),),
    )
    if row is None:
        raise ApiError(401, "Unbekannte Benutzer-ID in X-User-Id.")
    if not row["is_active"]:
        raise ApiError(403, "Der Benutzer ist deaktiviert oder gesperrt.")
    stored = UserRole(row["role"])
    return CurrentUser(
        user_id=row["user_id"],
        username=row["username"],
        stored_role=stored,
        role=x_test_role or stored,
    )


def require_staff(user: Annotated[CurrentUser, Depends(current_user)]) -> CurrentUser:
    if not user.is_staff:
        raise ApiError(403, "Nur Moderatoren und Admins dürfen das (Rolle per X-Test-Role setzen).")
    return user


def require_admin(user: Annotated[CurrentUser, Depends(current_user)]) -> CurrentUser:
    if not user.is_admin:
        raise ApiError(403, "Nur Admins dürfen das (Rolle per X-Test-Role setzen).")
    return user


@dataclass(frozen=True)
class Page:
    limit: int
    offset: int


def pagination(
    limit: Annotated[int, Query(ge=1, le=200, description="Maximale Anzahl Einträge.")] = 50,
    offset: Annotated[int, Query(ge=0, le=1_000_000, description="Anzahl übersprungener Einträge.")] = 0,
) -> Page:
    return Page(limit=limit, offset=offset)


DbDep = Annotated[Db, Depends(get_db)]
User = Annotated[CurrentUser, Depends(current_user)]
Staff = Annotated[CurrentUser, Depends(require_staff)]
Admin = Annotated[CurrentUser, Depends(require_admin)]
PageDep = Annotated[Page, Depends(pagination)]

MAX_BIGINT = 9_223_372_036_854_775_807
# IDs are bigint in the database; values outside the range would end in a numeric overflow error
PathId = Annotated[int, Path(ge=1, le=MAX_BIGINT, description="ID (bigint)")]
QueryId = Annotated[int, Query(ge=1, le=MAX_BIGINT)]
