from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Response

from ..db import Db, Where
from ..deps import CurrentUser, DbDep, Page, PageDep, PathId, User
from ..enums import LentStatus, ShippingStatus
from ..errors import conflict, forbidden, not_found
from ..responses import errs
from ..schemas import (
    LentCreate,
    LentDetail,
    LentRow,
    OverdueStatus,
    ReviewCreate,
    ReviewOut,
    ShippingOut,
    ShippingUpdate,
)

router = APIRouter(tags=["Ausleihen"])

LENT_CORE = """
SELECT l.lent_id, l.lent_status, l.book_copie_id, b.book_id, b.title,
       (SELECT string_agg(a.name, ', ' ORDER BY a.name)
        FROM "BookAuthors" ba JOIN "Authors" a ON a.author_id = ba.author_id
        WHERE ba.book_id = b.book_id) AS authors,
       l.lent_to AS borrower_id, bu.username AS borrower,
       ca.user_id AS owner_id, ou.username AS owner,
       l.shipping_requested, l.lent_requesttimestamp, l.lent_startdate, l.lent_enddate, l.return_date,
       (l.lent_status = 'active' AND now() > l.lent_enddate) AS overdue,
       ca.address_id AS pickup_address_id
FROM "Lents" l
JOIN "Book_copy" bc ON bc.book_copie_id = l.book_copie_id
JOIN "Books" b ON b.book_id = bc.book_id
JOIN "CollectionAdresses" ca ON ca.address_id = bc.collection_address_id
JOIN "Users" ou ON ou.user_id = ca.user_id
JOIN "Users" bu ON bu.user_id = l.lent_to
"""
SHIPPING_COLS = "shipping_id, lent_id, shipping_status, tracking_number, created_at, updated_at"
SHIPPING_ORDER = {"pending": 0, "shipped": 1, "delivered": 2}

# Route name -> database function. A fixed mapping, never user input.
TRANSITIONS = {
    "accept": "accept_book_request",
    "reject": "reject_book_request",
    "hand-over": "hand_over_book",
    "return": "return_book",
}


def fetch_user_lents(
    db: Db,
    user_id: int,
    page: Page,
    lent_status: LentStatus | None,
    role: Literal["borrower", "owner"] | None,
) -> list[dict[str, Any]]:
    """getAllLentsbyUser() plus the role of the user in each lent."""
    where = Where()
    if lent_status is not None:
        where.add("f.lent_status = %(lent_status)s", lent_status=lent_status.value)
    if role == "borrower":
        where.add("f.lent_to = %(uid)s")
    elif role == "owner":
        where.add("f.lent_from = %(uid)s")
    return db.all(
        f"""
        SELECT f.*, CASE WHEN f.lent_to = %(uid)s THEN 'borrower' ELSE 'owner' END AS my_role
        FROM getAllLentsbyUser(%(uid)s) f
        {where.sql()}
        ORDER BY f.lent_id DESC
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        {**where.params, "uid": user_id, "limit": page.limit, "offset": page.offset},
    )


def load_lent(db: Db, lent_id: int, *, lock: bool = False) -> dict[str, Any]:
    suffix = " FOR UPDATE OF l" if lock else ""
    row = db.one(f"{LENT_CORE} WHERE l.lent_id = %s{suffix}", (lent_id,))
    if row is None:
        raise not_found("Ausleihe")
    return row


def require_participant(lent: dict[str, Any], user: CurrentUser) -> None:
    if user.user_id not in (lent["borrower_id"], lent["owner_id"]) and not user.is_staff:
        raise forbidden("Du bist an dieser Ausleihe nicht beteiligt.")


def lent_detail(db: Db, lent: dict[str, Any], user: CurrentUser) -> dict[str, Any]:
    lent_id = lent["lent_id"]
    is_owner = user.user_id == lent["owner_id"]
    is_borrower = user.user_id == lent["borrower_id"]
    running = lent["lent_status"] in ("accepted", "active")

    shipping = db.one(
        f'SELECT {SHIPPING_COLS} FROM "Shipping" WHERE lent_id = %s ORDER BY shipping_id LIMIT 1', (lent_id,)
    )
    review = db.one(
        'SELECT review_id, lent_id, rating, comment, created_at FROM "Reviews" WHERE lent_id = %s', (lent_id,)
    )

    pickup = None
    pickup_visible = running and (
        is_owner or user.is_staff or (is_borrower and lent["lent_status"] == "accepted" and not lent["shipping_requested"])
    )
    if pickup_visible:
        address = db.one(
            """
            SELECT address_id, streetname, streetnummber, city, state, postal_code, country
            FROM "CollectionAdresses" WHERE address_id = %s
            """,
            (lent["pickup_address_id"],),
        )
        windows = db.all(
            """
            SELECT window_id, address_id, weekday, start_time, end_time
            FROM "CollectionTimeWindows" WHERE address_id = %s ORDER BY weekday, start_time
            """,
            (lent["pickup_address_id"],),
        )
        pickup = {"address": address, "time_windows": windows}

    borrower_address = None
    if running and lent["shipping_requested"] and (is_owner or user.is_staff):
        borrower_address = db.one(
            """
            SELECT address_id, street_address, city, state, postal_code, country, created_at
            FROM "UserAdresses" WHERE user_id = %s
            ORDER BY created_at DESC, address_id DESC LIMIT 1
            """,
            (lent["borrower_id"],),
        )

    return {
        **{k: v for k, v in lent.items() if k != "pickup_address_id"},
        "shipping": shipping,
        "review": review,
        "pickup": pickup,
        "borrower_current_address": borrower_address,
    }


@router.post(
    "/lents",
    status_code=201,
    response_model=LentDetail,
    summary="Buch anfragen",
    responses=errs(401, 403, 404, 409, 422),
)
def request_book(body: LentCreate, response: Response, user: User, db: DbDep) -> Any:
    """Ruft `create_book_request` auf. Die Datenbankfunktion prüft: Exemplar existiert (404), nicht das eigene (422),
    Versand nur wenn angeboten (422), nicht gerade angenommen/verliehen (409), keine doppelte offene Anfrage (409).
    Neuer Status: `requested`."""
    with db.tx():
        # create_book_request does not look at the owner's status (available_books does): a request for a copy of a
        # deactivated owner would stay open forever, so the API treats such copies as gone, like GET /copies/{id}
        owner = db.one(
            """
            SELECT u.is_active FROM "Book_copy" bc
            JOIN "CollectionAdresses" ca ON ca.address_id = bc.collection_address_id
            JOIN "Users" u ON u.user_id = ca.user_id
            WHERE bc.book_copie_id = %s
            """,
            (body.book_copie_id,),
        )
        if owner is not None and not owner["is_active"]:
            raise not_found("Exemplar")
        lent_id = db.scalar("SELECT create_book_request(%s, %s, %s)", (body.book_copie_id, user.user_id, body.shipping))
        detail = lent_detail(db, load_lent(db, lent_id), user)
    response.headers["Location"] = f"/lents/{lent_id}"
    return detail


@router.get(
    "/lents",
    response_model=list[LentRow],
    summary="Meine Ausleihen",
    responses=errs(401, 403),
)
def list_my_lents(
    user: User,
    db: DbDep,
    page: PageDep,
    lent_status: LentStatus | None = None,
    role: Annotated[Literal["borrower", "owner"] | None, Query(description="Nur Ausleihen in dieser Rolle.")] = None,
) -> Any:
    """Ruft `getAllLentsbyUser` für den Aufrufer auf: Ausleihen als Ausleiher und als Besitzer, neueste zuerst."""
    return fetch_user_lents(db, user.user_id, page, lent_status, role)


@router.get(
    "/lents/{lent_id}",
    response_model=LentDetail,
    summary="Ausleihe im Detail",
    responses=errs(401, 403, 404),
)
def get_lent(lent_id: PathId, user: User, db: DbDep) -> Any:
    """Nur für Ausleiher, Besitzer und Moderatoren/Admins. Enthält Versand, Bewertung und, je nach Rolle und Status,
    Abholadresse mit Zeitfenstern bzw. die Wohnadresse des Ausleihers."""
    lent = load_lent(db, lent_id)
    require_participant(lent, user)
    return lent_detail(db, lent, user)


def _transition(db: Db, lent_id: int, user: CurrentUser, action: str) -> dict[str, Any]:
    with db.tx():
        lent = load_lent(db, lent_id)
        if lent["owner_id"] != user.user_id:
            if lent["borrower_id"] == user.user_id or user.is_staff:
                raise forbidden("Diese Aktion darf nur der Besitzer des Exemplars ausführen.")
            raise forbidden("Du bist an dieser Ausleihe nicht beteiligt.")
        db.run(f"SELECT {TRANSITIONS[action]}(%s, %s)", (lent_id, user.user_id))
        return lent_detail(db, load_lent(db, lent_id), user)


@router.post(
    "/lents/{lent_id}/accept",
    response_model=LentDetail,
    summary="Anfrage annehmen (Besitzer)",
    responses=errs(401, 403, 404, 409),
)
def accept_lent(lent_id: PathId, user: User, db: DbDep) -> Any:
    """`requested` -> `accepted` über `accept_book_request`. Das Exemplar ist danach für den Ausleiher reserviert;
    weitere offene Anfragen bleiben bestehen, können aber nicht mehr angenommen werden (eindeutiger Index
    `lents_one_open_per_copy`)."""
    return _transition(db, lent_id, user, "accept")


@router.post(
    "/lents/{lent_id}/reject",
    response_model=LentDetail,
    summary="Anfrage ablehnen (Besitzer)",
    responses=errs(401, 403, 404, 409),
)
def reject_lent(lent_id: PathId, user: User, db: DbDep) -> Any:
    """`requested` -> `rejected` über `reject_book_request`."""
    return _transition(db, lent_id, user, "reject")


@router.post(
    "/lents/{lent_id}/hand-over",
    response_model=LentDetail,
    summary="Übergabe bestätigen (Besitzer)",
    responses=errs(401, 403, 404, 409),
)
def hand_over_lent(lent_id: PathId, user: User, db: DbDep) -> Any:
    """`accepted` -> `active` über `hand_over_book`. Die Leihfrist beginnt jetzt (`max_lent_duration` des Exemplars).
    Die Funktion legt keinen Versand an: bei Versand pflegt die API ihn über `PUT /lents/{lent_id}/shipping`."""
    return _transition(db, lent_id, user, "hand-over")


@router.post(
    "/lents/{lent_id}/return",
    response_model=LentDetail,
    summary="Rückgabe bestätigen (Besitzer)",
    responses=errs(401, 403, 404, 409),
)
def return_lent(lent_id: PathId, user: User, db: DbDep) -> Any:
    """`active` -> `returned` über `return_book`. Danach kann der Ausleiher bewerten."""
    return _transition(db, lent_id, user, "return")


@router.get(
    "/lents/{lent_id}/overdue",
    response_model=OverdueStatus,
    summary="Ist die Rückgabe überfällig?",
    responses=errs(401, 403, 404),
)
def check_overdue(lent_id: PathId, user: User, db: DbDep) -> Any:
    """Ruft `book_must_be_returned` auf: true, wenn die Ausleihe `active` ist und das Rückgabedatum vorbei ist."""
    lent = load_lent(db, lent_id)
    require_participant(lent, user)
    must_be_returned = db.scalar("SELECT book_must_be_returned(%s)", (lent_id,))
    return {
        "lent_id": lent_id,
        "must_be_returned": must_be_returned,
        "lent_status": lent["lent_status"],
        "lent_enddate": lent["lent_enddate"],
    }


@router.post(
    "/lents/{lent_id}/review",
    status_code=201,
    response_model=ReviewOut,
    summary="Bewertung schreiben (Ausleiher)",
    responses=errs(401, 403, 404, 409, 422),
)
def review_lent(lent_id: PathId, body: ReviewCreate, response: Response, user: User, db: DbDep) -> Any:
    """Ruft `write_review` auf. Nur der Ausleiher, nur nach der Rückgabe (`returned`), nur einmal pro Ausleihe.
    Bewertet wird der Buchtitel."""
    with db.tx():
        lent = load_lent(db, lent_id)
        if lent["borrower_id"] != user.user_id:
            raise forbidden("Nur der Ausleiher kann bewerten.")
        review_id = db.scalar(
            "SELECT write_review(%s, %s, %s, %s)", (lent_id, user.user_id, body.rating, body.comment)
        )
        row = db.one(
            'SELECT review_id, lent_id, rating, comment, created_at FROM "Reviews" WHERE review_id = %s', (review_id,)
        )
    response.headers["Location"] = f"/lents/{lent_id}"
    return row


@router.put(
    "/lents/{lent_id}/shipping",
    response_model=ShippingOut,
    summary="Versandstatus setzen",
    responses={201: {"model": ShippingOut, "description": "Versand wurde neu angelegt."}} | errs(401, 403, 404, 409, 422),
)
def put_shipping(lent_id: PathId, body: ShippingUpdate, response: Response, user: User, db: DbDep) -> Any:
    """Legt den Versand an oder aktualisiert ihn (Tabelle `Shipping`). Nur bei Ausleihen mit `shipping_requested`
    und Status `accepted`/`active`. Der Status läuft nur vorwärts (`pending` -> `shipped` -> `delivered`).

    * **Besitzer:** legt an bzw. setzt `pending`/`shipped` und die Sendungsnummer, nicht `delivered`.
    * **Ausleiher:** bestätigt nur `delivered`, und nur wenn der Status `shipped` ist.
    * **Moderator/Admin:** darf alles, aber ebenfalls nur vorwärts.

    `Shipping.lent_id` ist in der Datenbank nicht UNIQUE. Die API sperrt deshalb die Ausleihe und hält die Regel
    "höchstens eine Versandzeile" selbst ein; existieren bereits mehrere, antwortet sie mit 409."""
    with db.tx():
        lent = load_lent(db, lent_id, lock=True)
        require_participant(lent, user)
        if not lent["shipping_requested"]:
            raise conflict("Für diese Ausleihe wurde kein Versand gewünscht (shipping_requested ist nicht gesetzt).")
        if lent["lent_status"] not in ("accepted", "active"):
            raise conflict(f"Versand ist nur bei accepted/active möglich, die Ausleihe ist {lent['lent_status']}.")

        rows = db.all(
            f'SELECT {SHIPPING_COLS} FROM "Shipping" WHERE lent_id = %s ORDER BY shipping_id FOR UPDATE', (lent_id,)
        )
        if len(rows) > 1:
            raise conflict(
                f"Zu dieser Ausleihe gibt es {len(rows)} Versandzeilen (das Schema erlaubt das). "
                "Die API ändert uneindeutige Daten nicht."
            )
        current = rows[0] if rows else None
        target = body.shipping_status

        if not user.is_staff:
            if user.user_id == lent["owner_id"]:
                if target == ShippingStatus.delivered:
                    raise forbidden("Die Zustellung bestätigt der Ausleiher.")
            else:
                if target != ShippingStatus.delivered or current is None or current["shipping_status"] != "shipped":
                    raise forbidden("Der Ausleiher kann nur den Empfang eines versendeten Pakets (delivered) bestätigen.")
                if body.tracking_number is not None:
                    raise forbidden("Die Sendungsnummer pflegt der Besitzer.")

        if current is None:
            row = db.one(
                f"""
                INSERT INTO "Shipping" (lent_id, shipping_status, tracking_number) VALUES (%s, %s, %s)
                RETURNING {SHIPPING_COLS}
                """,
                (lent_id, target.value, body.tracking_number),
            )
            response.status_code = 201
        else:
            if SHIPPING_ORDER[target.value] < SHIPPING_ORDER[current["shipping_status"]]:
                raise conflict(f"Der Versandstatus läuft nur vorwärts ({current['shipping_status']} -> {target.value} geht nicht).")
            row = db.one(
                f"""
                UPDATE "Shipping"
                SET shipping_status = %s, tracking_number = COALESCE(%s, tracking_number), updated_at = now()
                WHERE shipping_id = %s
                RETURNING {SHIPPING_COLS}
                """,
                (target.value, body.tracking_number, current["shipping_id"]),
            )
    return row
