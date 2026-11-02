from enum import StrEnum
from typing import Annotated, Any

from fastapi import APIRouter, Query, Response

from ..db import Db, Where, like_contains, set_clause
from ..deps import CurrentUser, DbDep, PageDep, PathId, QueryId, User
from ..enums import BookCondition, BookLanguage, Genre, ShippingAvailability
from ..errors import conflict, forbidden, not_found, unprocessable
from ..responses import errs
from ..schemas import AvailableCopy, CopyCreate, CopyDetail, CopyUpdate
from .catalog import find_or_create_book

router = APIRouter(tags=["Exemplare"])

EARTH_RADIUS_KM = 6371.0088
COPY_COLS = (
    "book_copie_id, book_id, condition, max_lent_duration, collection_address_id, "
    "shipping_availability, shipping_cost, created_at, updated_at"
)
COPY_UPDATABLE = ("condition", "max_lent_duration", "collection_address_id", "shipping_availability", "shipping_cost")


class CopySort(StrEnum):
    title = "title"
    distance = "distance"


def _protocol(db: Db, user_id: int, copy_id: int | None, protocol_type: str, details: str) -> None:
    db.run(
        'INSERT INTO "Protocols" (book_copie_id, user_id, protocol_type, details) VALUES (%s, %s, %s, %s)',
        (copy_id, user_id, protocol_type, details),
    )


@router.get(
    "/copies",
    response_model=list[AvailableCopy],
    summary="Verfügbare Exemplare suchen (mit Umkreissuche)",
    responses=errs(422),
)
def search_copies(
    db: DbDep,
    page: PageDep,
    q: Annotated[str | None, Query(max_length=100, description="Teil von Titel oder Autorenname.")] = None,
    genre: Genre | None = None,
    language: BookLanguage | None = None,
    condition: BookCondition | None = None,
    shipping: Annotated[ShippingAvailability | None, Query(description="Bietet der Besitzer Versand an?")] = None,
    city: Annotated[str | None, Query(max_length=100, description="Ort (ohne Beachtung der Groß-/Kleinschreibung).")] = None,
    owner_id: QueryId | None = None,
    lat: Annotated[float | None, Query(ge=-90, le=90, description="Breitengrad des Suchmittelpunkts.")] = None,
    lon: Annotated[float | None, Query(ge=-180, le=180, description="Längengrad des Suchmittelpunkts.")] = None,
    radius_km: Annotated[float | None, Query(gt=0, le=20000, description="Umkreis in km (benötigt lat und lon).")] = None,
    sort: CopySort = CopySort.title,
) -> Any:
    """Liest die View `available_books`: nur Exemplare aktiver Besitzer, die nicht angenommen oder verliehen sind.

    **Umkreissuche:** Mit `lat`, `lon` und `radius_km` werden die Koordinaten der Abholadresse
    (`CollectionAdresses.latitude/longitude`) per Haversine-Formel ausgewertet; die View selbst enthält keine
    Koordinaten, die API verbindet deshalb zusätzlich `Book_copy` und `CollectionAdresses`. Exemplare ohne
    Koordinaten fallen bei aktiver Umkreissuche heraus."""
    if (lat is None) != (lon is None):
        raise unprocessable("lat und lon nur gemeinsam angeben.")
    if radius_km is not None and lat is None:
        raise unprocessable("radius_km benötigt lat und lon.")
    if sort == CopySort.distance and lat is None:
        raise unprocessable("sort=distance benötigt lat und lon.")

    where = Where()
    if q:
        where.add("(ab.title ILIKE %(q)s OR ab.authors ILIKE %(q)s)", q=like_contains(q))
    if genre is not None:
        where.add("ab.genre = %(genre)s", genre=genre.value)
    if language is not None:
        where.add("ab.language = %(language)s", language=language.value)
    if condition is not None:
        where.add("ab.condition = %(condition)s", condition=condition.value)
    if shipping is not None:
        where.add("ab.shipping_availability = %(shipping)s", shipping=shipping.value)
    if city:
        where.add("lower(ab.city) = lower(%(city)s)", city=city)
    if owner_id is not None:
        where.add("ab.owner_id = %(owner_id)s", owner_id=owner_id)

    distance_select = ", NULL::float8 AS distance_km"
    distance_join = ""
    params: dict[str, Any] = {}
    if lat is not None:
        params |= {"lat": lat, "lon": lon}
        distance_select = ", d.km AS distance_km"
        distance_join = f"""
            JOIN "Book_copy" bc ON bc.book_copie_id = ab.book_copie_id
            JOIN "CollectionAdresses" ca ON ca.address_id = bc.collection_address_id
            CROSS JOIN LATERAL (
                SELECT {EARTH_RADIUS_KM} * 2 * asin(sqrt(least(1.0,
                    power(sin(radians(ca.latitude::float8 - %(lat)s) / 2), 2)
                    + cos(radians(%(lat)s)) * cos(radians(ca.latitude::float8))
                      * power(sin(radians(ca.longitude::float8 - %(lon)s) / 2), 2)))) AS km
            ) d
        """
        if radius_km is not None:
            where.add("d.km <= %(radius_km)s", radius_km=radius_km)
    order = "d.km, ab.title, ab.book_copie_id" if sort == CopySort.distance else "ab.title, ab.book_copie_id"

    return db.all(
        f"""
        SELECT ab.book_copie_id, ab.book_id, ab.title, ab.authors, ab.genre, ab.language, ab.condition,
               ab.max_lent_duration, ab.shipping_availability, ab.shipping_cost, ab.city, ab.postal_code,
               ab.owner_id, ab.owner{distance_select}
        FROM available_books ab
        {distance_join}
        {where.sql()}
        ORDER BY {order}
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        {**where.params, **params, "limit": page.limit, "offset": page.offset},
    )


def load_copy_detail(db: Db, copy_id: int, user: CurrentUser) -> dict[str, Any]:
    row = db.one(
        """
        SELECT fb.book_copie_id, fb.book_id, fb.title, fb.authors, fb.genre, fb.language, fb.condition,
               fb.max_lent_duration, fb.shipping_availability, fb.shipping_cost, fb.city, fb.postal_code,
               fb.owner_id, fb.owner, fb.user_firstname, fb.user_lastname,
               u.is_active AS owner_active,
               EXISTS (SELECT 1 FROM available_books ab WHERE ab.book_copie_id = fb.book_copie_id) AS available
        FROM fullBookInfo fb
        JOIN "Users" u ON u.user_id = fb.owner_id
        WHERE fb.book_copie_id = %s
        """,
        (copy_id,),
    )
    privileged = row is not None and (user.is_staff or user.user_id == row["owner_id"])
    # fullBookInfo does not filter inactive owners (available_books does): hide those copies from others
    if row is None or (not row["owner_active"] and not privileged):
        raise not_found("Exemplar")
    if not privileged:
        row["user_firstname"] = None
        row["user_lastname"] = None
    return row


@router.get(
    "/copies/{book_copie_id}",
    response_model=CopyDetail,
    summary="Exemplar im Detail",
    responses=errs(401, 403, 404),
)
def get_copy(book_copie_id: PathId, user: User, db: DbDep) -> Any:
    """Liest die View `fullBookInfo`. `available` kommt aus `available_books`. Vor- und Nachname des Besitzers
    sehen nur der Besitzer und Moderatoren/Admins."""
    return load_copy_detail(db, book_copie_id, user)


@router.post(
    "/copies",
    status_code=201,
    response_model=CopyDetail,
    summary="Exemplar anbieten",
    responses=errs(401, 403, 404, 409, 422),
)
def create_copy(body: CopyCreate, response: Response, user: User, db: DbDep) -> Any:
    """Legt ein Exemplar an der angegebenen **eigenen** Abholadresse an. Das Buch wird entweder per `book_id`
    gewählt oder per `book` (Titel + Autoren) gefunden bzw. angelegt: gleicher Titel und gleiche Autorenmenge
    (ohne Beachtung der Groß-/Kleinschreibung) bedeuten dasselbe Buch. Mehrere Treffer führen zu 409.
    Nicht angegebene Felder übernehmen die Standardwerte der Datenbank (`max_lent_duration` 60,
    `shipping_availability` not_available, `shipping_cost` 4.00). Die Datenbank prüft `max_lent_duration`
    und `shipping_cost` nicht, die API begrenzt sie auf 1-120 Tage und 0-9,99 Euro."""
    with db.tx():
        address = db.one('SELECT user_id FROM "CollectionAdresses" WHERE address_id = %s', (body.collection_address_id,))
        if address is None:
            raise not_found("Abholadresse")
        if address["user_id"] != user.user_id:
            raise forbidden("Exemplare können nur an eigenen Abholadressen angeboten werden.")

        if body.book_id is not None:
            title = db.scalar('SELECT title FROM "Books" WHERE book_id = %s', (body.book_id,))
            if title is None:
                raise not_found("Buch")
            book_id = body.book_id
        else:
            assert body.book is not None
            book_id, _ = find_or_create_book(db, body.book)
            title = body.book.title

        columns: dict[str, Any] = {"book_id": book_id, "collection_address_id": body.collection_address_id}
        for name in ("condition", "max_lent_duration", "shipping_availability", "shipping_cost"):
            value = getattr(body, name)
            if value is not None:
                columns[name] = value.value if hasattr(value, "value") else value
        row = db.one(
            f"""
            INSERT INTO "Book_copy" ({", ".join(columns)})
            VALUES ({", ".join(f"%({name})s" for name in columns)})
            RETURNING book_copie_id
            """,
            columns,
        )
        assert row is not None
        _protocol(db, user.user_id, row["book_copie_id"], "create_book", f"Copy of {title} added")
        detail = load_copy_detail(db, row["book_copie_id"], user)
    response.headers["Location"] = f"/copies/{detail['book_copie_id']}"
    return detail


def _lock_own_copy(db: Db, copy_id: int, user: CurrentUser) -> dict[str, Any]:
    """Locks the copy row. create_book_request locks the same row, so a copy cannot be deleted or moved while a
    request for it is being created. Call inside db.tx()."""
    row = db.one(
        """
        SELECT bc.book_copie_id, ca.user_id AS owner_id, b.title
        FROM "Book_copy" bc
        JOIN "CollectionAdresses" ca ON ca.address_id = bc.collection_address_id
        JOIN "Books" b ON b.book_id = bc.book_id
        WHERE bc.book_copie_id = %s
        FOR UPDATE OF bc
        """,
        (copy_id,),
    )
    if row is None:
        raise not_found("Exemplar")
    if row["owner_id"] != user.user_id:
        raise forbidden("Das ist nicht dein Exemplar.")
    return row


@router.patch(
    "/copies/{book_copie_id}",
    response_model=CopyDetail,
    summary="Exemplar ändern",
    responses=errs(401, 403, 404, 422),
)
def update_copy(book_copie_id: PathId, body: CopyUpdate, user: User, db: DbDep) -> Any:
    """Nur der Besitzer. Geändert werden nur die mitgesendeten (nicht-null) Felder. Der Besitzer ergibt sich aus der
    Abholadresse, deshalb ist ein Wechsel nur auf eine andere **eigene** Adresse möglich."""
    changes = {
        name: (value.value if hasattr(value, "value") else value)
        for name, value in body.model_dump(exclude_unset=True).items()
        if value is not None
    }
    with db.tx():
        copy = _lock_own_copy(db, book_copie_id, user)
        new_address = changes.get("collection_address_id")
        if new_address is not None:
            target = db.one('SELECT user_id FROM "CollectionAdresses" WHERE address_id = %s', (new_address,))
            if target is None:
                raise not_found("Abholadresse")
            if target["user_id"] != user.user_id:
                raise forbidden("Das Exemplar kann nur an eine eigene Abholadresse verschoben werden.")
        db.run(
            f'UPDATE "Book_copy" SET {set_clause(changes, COPY_UPDATABLE)}, updated_at = now() WHERE book_copie_id = %(_id)s',
            {**changes, "_id": book_copie_id},
        )
        _protocol(
            db,
            user.user_id,
            book_copie_id,
            "update_book",
            f"Copy of {copy['title']} changed: " + ", ".join(sorted(changes)),
        )
        return load_copy_detail(db, book_copie_id, user)


@router.delete(
    "/copies/{book_copie_id}",
    status_code=204,
    summary="Exemplar löschen",
    responses=errs(401, 403, 404, 409),
)
def delete_copy(book_copie_id: PathId, user: User, db: DbDep) -> Response:
    """Nur der Besitzer, und nur solange es **keine** Ausleihe zu dem Exemplar gibt (auch keine abgeschlossene):
    `Lents.book_copie_id` hat `ON DELETE CASCADE`, ein Löschen würde die gesamte Ausleihhistorie samt Bewertungen und
    Versand vernichten. Das Exemplar wird für die Prüfung gesperrt, damit nicht gleichzeitig eine Anfrage entsteht."""
    with db.tx():
        copy = _lock_own_copy(db, book_copie_id, user)
        lents = db.scalar('SELECT count(*) FROM "Lents" WHERE book_copie_id = %s', (book_copie_id,))
        if lents:
            raise conflict(
                f"Zu dem Exemplar gibt es {lents} Ausleihen. Löschen würde die Historie vernichten "
                "(ON DELETE CASCADE) und wird deshalb verweigert."
            )
        db.run('DELETE FROM "Book_copy" WHERE book_copie_id = %s', (book_copie_id,))
        # the protocol row must not point at the deleted copy (the FK would null it anyway)
        _protocol(db, user.user_id, None, "delete_book", f"Copy {book_copie_id} of {copy['title']} deleted")
    return Response(status_code=204)
