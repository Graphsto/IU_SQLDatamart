from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Response

from ..db import Db, Where, like_contains, set_clause
from ..deps import Admin, CurrentUser, DbDep, PageDep, PathId, Staff, User
from ..enums import LentStatus, UserRole
from ..errors import conflict, forbidden, not_found
from ..responses import errs
from ..schemas import (
    LentRow,
    UserAdminUpdate,
    UserBookCopy,
    UserCreate,
    UserMe,
    UserStats,
    UserUpdate,
    UserView,
)
from .lents import fetch_user_lents

router = APIRouter(tags=["Benutzer"])

USER_COLS = "user_id, username, first_name, last_name, email, role, is_active, created_at, updated_at"
OPEN_LENT_STATES = ("requested", "accepted", "active")


def _me(row: dict[str, Any], user: CurrentUser) -> dict[str, Any]:
    return {**row, "effective_role": user.role}


def _protocol(db: Db, user_id: int, protocol_type: str, details: str) -> None:
    db.run(
        'INSERT INTO "Protocols" (user_id, protocol_type, details) VALUES (%s, %s, %s)',
        (user_id, protocol_type, details),
    )


@router.post(
    "/users",
    status_code=201,
    response_model=UserView,
    response_model_exclude_none=True,
    summary="Registrieren (ohne Passwort)",
    responses=errs(409, 422),
)
def register_user(body: UserCreate, response: Response, db: DbDep) -> Any:
    """Legt einen Benutzer mit Rolle `user` an. Es gibt keinen Login: `password_hash` wird auf `!` gesetzt
    (Konvention: kein Login möglich). `role` und `is_active` können nicht mitgegeben werden.
    Die E-Mail-Adresse wird ohne Beachtung der Groß-/Kleinschreibung auf Duplikate geprüft (die Datenbank
    erzwingt nur eine exakte Eindeutigkeit)."""
    with db.tx():
        if db.scalar('SELECT EXISTS (SELECT 1 FROM "Users" WHERE lower(email) = lower(%s))', (body.email,)):
            raise conflict("Diese E-Mail-Adresse ist bereits registriert.")
        row = db.one(
            f"""
            INSERT INTO "Users" (username, first_name, last_name, email, password_hash)
            VALUES (%s, %s, %s, %s, '!')
            RETURNING {USER_COLS}
            """,
            (body.username, body.first_name, body.last_name, body.email),
        )
        assert row is not None
        _protocol(db, row["user_id"], "create_user", f"User {body.username} registered")
    response.headers["Location"] = f"/users/{row['user_id']}"
    return row


@router.get("/users/me", response_model=UserMe, summary="Eigenes Profil", responses=errs(401, 403))
def get_me(user: User, db: DbDep) -> Any:
    """Profil des Aufrufers (`X-User-Id`) inklusive der wirksamen Rolle."""
    row = db.one(f'SELECT {USER_COLS} FROM "Users" WHERE user_id = %s', (user.user_id,))
    return _me(row or {}, user)


@router.patch(
    "/users/me",
    response_model=UserMe,
    summary="Eigenes Profil ändern",
    responses=errs(401, 403, 409, 422),
)
def update_me(body: UserUpdate, user: User, db: DbDep) -> Any:
    """Ändert Vorname, Nachname und/oder E-Mail. `updated_at` setzt die API (die Datenbank hat keinen Trigger)."""
    changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    with db.tx():
        if "email" in changes and db.scalar(
            'SELECT EXISTS (SELECT 1 FROM "Users" WHERE lower(email) = lower(%s) AND user_id <> %s)',
            (changes["email"], user.user_id),
        ):
            raise conflict("Diese E-Mail-Adresse ist bereits registriert.")
        row = db.one(
            f"""
            UPDATE "Users" SET {set_clause(changes, ("first_name", "last_name", "email"))}, updated_at = now()
            WHERE user_id = %(_uid)s
            RETURNING {USER_COLS}
            """,
            {**changes, "_uid": user.user_id},
        )
        _protocol(db, user.user_id, "update_user", "Fields changed: " + ", ".join(sorted(changes)))
    return _me(row or {}, user)


@router.delete(
    "/users/me",
    response_model=UserMe,
    summary="Eigenes Konto deaktivieren",
    responses=errs(401, 403, 409),
)
def deactivate_me(user: User, db: DbDep) -> Any:
    """Deaktiviert das Konto (`is_active = false`), löscht es aber nicht: ein DELETE würde per
    `ON DELETE CASCADE` alle Ausleihen, Bewertungen und Nachrichten mitlöschen. Solange es offene Ausleihen
    (`requested`, `accepted`, `active`) als Ausleiher oder Besitzer gibt, antwortet die API mit 409.
    Danach sind die Exemplare nicht mehr in `available_books` sichtbar."""
    with db.tx():
        open_lents = db.scalar(
            """
            SELECT count(*) FROM "Lents" l
            JOIN "Book_copy" bc ON bc.book_copie_id = l.book_copie_id
            JOIN "CollectionAdresses" ca ON ca.address_id = bc.collection_address_id
            WHERE l.lent_status IN ('requested', 'accepted', 'active')
              AND (l.lent_to = %(uid)s OR ca.user_id = %(uid)s)
            """,
            {"uid": user.user_id},
        )
        if open_lents:
            raise conflict(
                f"Es gibt noch {open_lents} offene Ausleihen ({', '.join(OPEN_LENT_STATES)}). "
                "Erst abschließen oder ablehnen."
            )
        row = db.one(
            f'UPDATE "Users" SET is_active = false, updated_at = now() WHERE user_id = %s RETURNING {USER_COLS}',
            (user.user_id,),
        )
        _protocol(db, user.user_id, "delete_user", "User deactivated")
    return _me(row or {}, user)


@router.get(
    "/users",
    response_model=list[UserView],
    response_model_exclude_none=True,
    summary="Benutzer suchen (Moderator/Admin)",
    responses=errs(401, 403),
)
def list_users(
    user: Staff,
    db: DbDep,
    page: PageDep,
    q: Annotated[str | None, Query(max_length=100, description="Teil von Benutzername, Name oder E-Mail.")] = None,
    role: UserRole | None = None,
    is_active: bool | None = None,
) -> Any:
    """Suche über Benutzername, Vor-/Nachname und E-Mail (ohne Beachtung der Groß-/Kleinschreibung), mit Filtern für
    Rolle und Aktiv-Status. Nur für Moderatoren/Admins (Parameter `X-Test-Role`)."""
    where = Where()
    if q:
        where.add(
            "(username ILIKE %(q)s OR email ILIKE %(q)s OR first_name ILIKE %(q)s OR last_name ILIKE %(q)s)",
            q=like_contains(q),
        )
    if role is not None:
        where.add("role = %(role)s", role=role.value)
    if is_active is not None:
        where.add("is_active = %(is_active)s", is_active=is_active)
    return db.all(
        f'SELECT {USER_COLS} FROM "Users" {where.sql()} ORDER BY user_id LIMIT %(limit)s OFFSET %(offset)s',
        {**where.params, "limit": page.limit, "offset": page.offset},
    )


@router.get(
    "/users/{user_id}",
    response_model=UserView,
    response_model_exclude_none=True,
    summary="Benutzer ansehen",
    responses=errs(401, 403, 404),
)
def get_user(user_id: PathId, user: User, db: DbDep) -> Any:
    """Öffentlich sind `user_id`, `username` und `created_at`. Name, E-Mail, Rolle und Status sehen nur der
    Benutzer selbst und Moderatoren/Admins. Deaktivierte Benutzer sind für andere nicht sichtbar (404)."""
    row = db.one(f'SELECT {USER_COLS} FROM "Users" WHERE user_id = %s', (user_id,))
    if row is None:
        raise not_found("Benutzer")
    if user.user_id == user_id or user.is_staff:
        return row
    if not row["is_active"]:
        raise not_found("Benutzer")
    return {key: row[key] for key in ("user_id", "username", "created_at")}


@router.patch(
    "/users/{user_id}",
    response_model=UserView,
    response_model_exclude_none=True,
    summary="Rolle oder Status ändern (Admin)",
    responses=errs(401, 403, 404, 422),
)
def admin_update_user(user_id: PathId, body: UserAdminUpdate, admin: Admin, db: DbDep) -> Any:
    """Setzt `role` und/oder `is_active` eines Benutzers. Die API prüft nicht, ob dadurch offene Ausleihen
    zurückbleiben, wenn ein Benutzer gesperrt wird (die Datenbank kennt dafür keine Regel, siehe Testbericht)."""
    changes = {
        key: value.value if isinstance(value, UserRole) else value
        for key, value in body.model_dump(exclude_unset=True).items()
        if value is not None
    }
    with db.tx():
        row = db.one(
            f"""
            UPDATE "Users" SET {set_clause(changes, ("role", "is_active"))}, updated_at = now()
            WHERE user_id = %(_uid)s
            RETURNING {USER_COLS}
            """,
            {**changes, "_uid": user_id},
        )
        if row is None:
            raise not_found("Benutzer")
        _protocol(
            db,
            admin.user_id,
            "update_user",
            f"Admin changed user {user_id}: " + ", ".join(f"{k}={v}" for k, v in sorted(changes.items())),
        )
    return row


@router.get(
    "/users/{user_id}/stats",
    response_model=UserStats,
    summary="Statistik eines Benutzers",
    responses=errs(401, 403, 404),
)
def get_user_stats(user_id: PathId, user: User, db: DbDep) -> Any:
    """Ruft die Datenbankfunktion `getFullUserStats` auf. Das Ergebnis enthält die E-Mail-Adresse, deshalb nur
    für den Benutzer selbst und Moderatoren/Admins."""
    if user_id != user.user_id and not user.is_staff:
        raise forbidden("Statistik nur für das eigene Konto oder für Moderatoren/Admins.")
    row = db.one("SELECT * FROM getFullUserStats(%s)", (user_id,))
    if row is None:
        raise not_found("Benutzer")
    return row


@router.get(
    "/users/{user_id}/books",
    response_model=list[UserBookCopy],
    summary="Exemplare, die ein Benutzer anbietet",
    responses=errs(401, 403, 404),
)
def list_user_books(user_id: PathId, user: User, db: DbDep, page: PageDep) -> Any:
    """Ruft `getAllBooksbyUser` auf (alle Exemplare des Besitzers, auch gerade verliehene)."""
    owner = db.one('SELECT is_active FROM "Users" WHERE user_id = %s', (user_id,))
    if owner is None or (not owner["is_active"] and user.user_id != user_id and not user.is_staff):
        raise not_found("Benutzer")
    return db.all(
        "SELECT * FROM getAllBooksbyUser(%s) ORDER BY book_copie_id LIMIT %s OFFSET %s",
        (user_id, page.limit, page.offset),
    )


@router.get(
    "/users/{user_id}/lents",
    response_model=list[LentRow],
    summary="Ausleihen eines Benutzers",
    responses=errs(401, 403, 404),
)
def list_user_lents(
    user_id: PathId,
    user: User,
    db: DbDep,
    page: PageDep,
    lent_status: LentStatus | None = None,
    role: Annotated[Literal["borrower", "owner"] | None, Query(description="Nur Ausleihen in dieser Rolle.")] = None,
) -> Any:
    """Ruft `getAllLentsbyUser` auf (als Ausleiher und als Besitzer). Nur für das eigene Konto und Moderatoren/Admins."""
    if user_id != user.user_id and not user.is_staff:
        raise forbidden("Ausleihen nur für das eigene Konto oder für Moderatoren/Admins.")
    if not db.scalar('SELECT EXISTS (SELECT 1 FROM "Users" WHERE user_id = %s)', (user_id,)):
        raise not_found("Benutzer")
    return fetch_user_lents(db, user_id, page, lent_status, role)
