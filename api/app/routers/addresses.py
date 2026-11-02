from typing import Annotated, Any

from fastapi import APIRouter, Query, Response

from ..db import Db, set_clause
from ..deps import MAX_BIGINT, CurrentUser, DbDep, PageDep, PathId, User
from ..errors import conflict, forbidden, not_found
from ..responses import errs
from ..schemas import (
    CollectionAddress,
    CollectionAddressCreate,
    CollectionAddressUpdate,
    TimeWindow,
    TimeWindowCreate,
    UserAddress,
    UserAddressCreate,
)

router = APIRouter(tags=["Adressen"])

CA_COLS = (
    "address_id, user_id, streetname, streetnummber, city, state, postal_code, country, "
    "latitude, longitude, created_at, updated_at"
)
CA_UPDATABLE = ("streetname", "streetnummber", "city", "state", "postal_code", "country", "latitude", "longitude")
TW_COLS = "window_id, address_id, weekday, start_time, end_time"
UA_COLS = "address_id, user_id, street_address, city, state, postal_code, country, created_at, updated_at"

UserIdFilter = Annotated[
    int | None,
    Query(ge=1, le=MAX_BIGINT, description="Nur Moderatoren/Admins: Adressen eines anderen Benutzers."),
]


def _target_user(user: CurrentUser, user_id: int | None) -> int:
    target = user.user_id if user_id is None else user_id
    if target != user.user_id and not user.is_staff:
        raise forbidden("Fremde Adressen sehen nur Moderatoren/Admins.")
    return target


def _owned_collection_address(db: Db, address_id: int, user: CurrentUser) -> None:
    row = db.one('SELECT user_id FROM "CollectionAdresses" WHERE address_id = %s', (address_id,))
    if row is None:
        raise not_found("Abholadresse")
    if row["user_id"] != user.user_id:
        raise forbidden("Das ist nicht deine Abholadresse.")


# ------------------------------------------------------------------ collection addresses


@router.get(
    "/collection-addresses",
    response_model=list[CollectionAddress],
    summary="Eigene Abholadressen",
    responses=errs(401, 403),
)
def list_collection_addresses(user: User, db: DbDep, page: PageDep, user_id: UserIdFilter = None) -> Any:
    """Abholadressen sind nicht öffentlich. Ein Ausleiher sieht die Adresse in der Ausleihe, sobald sie angenommen ist."""
    return db.all(
        f'SELECT {CA_COLS} FROM "CollectionAdresses" WHERE user_id = %s ORDER BY address_id LIMIT %s OFFSET %s',
        (_target_user(user, user_id), page.limit, page.offset),
    )


@router.post(
    "/collection-addresses",
    status_code=201,
    response_model=CollectionAddress,
    summary="Abholadresse anlegen",
    responses=errs(401, 403, 422),
)
def create_collection_address(body: CollectionAddressCreate, response: Response, user: User, db: DbDep) -> Any:
    """Der Besitzer ist immer der Aufrufer. Koordinaten nur zusammen (beide oder keine), sie werden für die
    Umkreissuche in `GET /copies` gebraucht."""
    row = db.one(
        f"""
        INSERT INTO "CollectionAdresses"
            (user_id, streetname, streetnummber, city, state, postal_code, country, latitude, longitude)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING {CA_COLS}
        """,
        (
            user.user_id,
            body.streetname,
            body.streetnummber,
            body.city,
            body.state,
            body.postal_code,
            body.country,
            body.latitude,
            body.longitude,
        ),
    )
    assert row is not None
    response.headers["Location"] = f"/collection-addresses/{row['address_id']}"
    return row


@router.patch(
    "/collection-addresses/{address_id}",
    response_model=CollectionAddress,
    summary="Abholadresse ändern",
    responses=errs(401, 403, 404, 422),
)
def update_collection_address(address_id: PathId, body: CollectionAddressUpdate, user: User, db: DbDep) -> Any:
    """Nur der Besitzer. Es werden nur die mitgesendeten Felder geändert; `country`, `latitude` und `longitude`
    dürfen auf `null` gesetzt werden (Koordinaten nur beide zusammen)."""
    changes = {name: getattr(body, name) for name in body.model_fields_set}
    with db.tx():
        _owned_collection_address(db, address_id, user)
        row = db.one(
            f"""
            UPDATE "CollectionAdresses" SET {set_clause(changes, CA_UPDATABLE)}, updated_at = now()
            WHERE address_id = %(_id)s
            RETURNING {CA_COLS}
            """,
            {**changes, "_id": address_id},
        )
    return row


@router.delete(
    "/collection-addresses/{address_id}",
    status_code=204,
    summary="Abholadresse löschen",
    responses=errs(401, 403, 404, 409),
)
def delete_collection_address(address_id: PathId, user: User, db: DbDep) -> Response:
    """Nur der Besitzer. Solange Exemplare auf die Adresse verweisen, verweigert die Datenbank das Löschen
    (`ON DELETE RESTRICT`), die API meldet 409. Zeitfenster werden mitgelöscht (`CASCADE`)."""
    with db.tx():
        _owned_collection_address(db, address_id, user)
        if db.scalar('SELECT EXISTS (SELECT 1 FROM "Book_copy" WHERE collection_address_id = %s)', (address_id,)):
            raise conflict("Die Abholadresse wird noch von Exemplaren verwendet.")
        db.run('DELETE FROM "CollectionAdresses" WHERE address_id = %s', (address_id,))
    return Response(status_code=204)


# ------------------------------------------------------------------ time windows


@router.get(
    "/collection-addresses/{address_id}/time-windows",
    response_model=list[TimeWindow],
    summary="Abholzeitfenster einer Adresse",
    responses=errs(401, 403, 404),
)
def list_time_windows(address_id: PathId, user: User, db: DbDep) -> Any:
    """Die wöchentlich wiederkehrenden Zeitfenster, in denen Bücher von dieser Adresse abgeholt werden können
    (`weekday`: 0 = Sonntag … 6 = Samstag). Für jeden angemeldeten Benutzer sichtbar, die Adresse selbst nicht."""
    if not db.scalar('SELECT EXISTS (SELECT 1 FROM "CollectionAdresses" WHERE address_id = %s)', (address_id,)):
        raise not_found("Abholadresse")
    return db.all(
        f'SELECT {TW_COLS} FROM "CollectionTimeWindows" WHERE address_id = %s ORDER BY weekday, start_time',
        (address_id,),
    )


@router.post(
    "/collection-addresses/{address_id}/time-windows",
    status_code=201,
    response_model=TimeWindow,
    summary="Abholzeitfenster anlegen",
    responses=errs(401, 403, 404, 409, 422),
)
def create_time_window(address_id: PathId, body: TimeWindowCreate, response: Response, user: User, db: DbDep) -> Any:
    """Nur der Besitzer der Adresse. Ruft `create_collection_time_window` auf. Überschneidungen am selben
    Wochentag verweigert die Datenbank (Exclusion-Constraint `no_overlapping_window`), die API meldet 409.
    Das Ende zählt nicht mit: 10:00-12:00 und 12:00-14:00 sind erlaubt."""
    with db.tx():
        _owned_collection_address(db, address_id, user)
        window_id = db.scalar(
            "SELECT create_collection_time_window(%s, %s, %s, %s, %s)",
            (address_id, user.user_id, body.weekday, body.start_time, body.end_time),
        )
        row = db.one(f'SELECT {TW_COLS} FROM "CollectionTimeWindows" WHERE window_id = %s', (window_id,))
    response.headers["Location"] = f"/collection-addresses/{address_id}/time-windows"
    return row


@router.delete(
    "/time-windows/{window_id}",
    status_code=204,
    summary="Abholzeitfenster löschen",
    responses=errs(401, 403, 404),
)
def delete_time_window(window_id: PathId, user: User, db: DbDep) -> Response:
    """Nur der Besitzer der Abholadresse (404 bei unbekannter ID, 403 bei fremder Adresse)."""
    deleted = db.run(
        """
        DELETE FROM "CollectionTimeWindows" w USING "CollectionAdresses" a
        WHERE w.window_id = %s AND a.address_id = w.address_id AND a.user_id = %s
        """,
        (window_id, user.user_id),
    )
    if not deleted:
        if db.scalar('SELECT EXISTS (SELECT 1 FROM "CollectionTimeWindows" WHERE window_id = %s)', (window_id,)):
            raise forbidden("Das Zeitfenster gehört nicht zu deiner Abholadresse.")
        raise not_found("Zeitfenster")
    return Response(status_code=204)


# ------------------------------------------------------------------ user addresses


@router.get(
    "/user-addresses",
    response_model=list[UserAddress],
    summary="Eigene Wohnadressen",
    responses=errs(401, 403),
)
def list_user_addresses(user: User, db: DbDep, page: PageDep, user_id: UserIdFilter = None) -> Any:
    """Wohnadressen (Tabelle `UserAdresses`) sind für die Lieferung gedacht und von den Abholadressen getrennt."""
    return db.all(
        f'SELECT {UA_COLS} FROM "UserAdresses" WHERE user_id = %s ORDER BY address_id LIMIT %s OFFSET %s',
        (_target_user(user, user_id), page.limit, page.offset),
    )


@router.post(
    "/user-addresses",
    status_code=201,
    response_model=UserAddress,
    summary="Wohnadresse anlegen",
    responses=errs(401, 403, 422),
)
def create_user_address(body: UserAddressCreate, response: Response, user: User, db: DbDep) -> Any:
    """Legt eine Wohnadresse für den Aufrufer an. Das Schema verknüpft Ausleihen nicht mit einer Lieferadresse,
    für den Versand gilt die jüngste Wohnadresse des Ausleihers (siehe `GET /lents/{lent_id}`)."""
    row = db.one(
        f"""
        INSERT INTO "UserAdresses" (user_id, street_address, city, state, postal_code, country)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING {UA_COLS}
        """,
        (user.user_id, body.street_address, body.city, body.state, body.postal_code, body.country),
    )
    assert row is not None
    response.headers["Location"] = f"/user-addresses/{row['address_id']}"
    return row


@router.delete(
    "/user-addresses/{address_id}",
    status_code=204,
    summary="Wohnadresse löschen",
    responses=errs(401, 403, 404),
)
def delete_user_address(address_id: PathId, user: User, db: DbDep) -> Response:
    """Nur die eigene Wohnadresse (404 bei unbekannter ID, 403 bei fremder Adresse)."""
    deleted = db.run('DELETE FROM "UserAdresses" WHERE address_id = %s AND user_id = %s', (address_id, user.user_id))
    if not deleted:
        if db.scalar('SELECT EXISTS (SELECT 1 FROM "UserAdresses" WHERE address_id = %s)', (address_id,)):
            raise forbidden("Das ist nicht deine Wohnadresse.")
        raise not_found("Wohnadresse")
    return Response(status_code=204)
