from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Response

from ..db import Db, Where
from ..deps import DbDep, PageDep, PathId, QueryId, User
from ..errors import conflict, forbidden, not_found
from ..responses import errs
from ..schemas import MessageCreate, MessageOut

router = APIRouter(tags=["Nachrichten"])

MESSAGE_COLS = "message_id, sender_id, receiver_id, content, sent_at, read_status"


def _protocol(db: Db, user_id: int, protocol_type: str, details: str) -> None:
    db.run(
        'INSERT INTO "Protocols" (user_id, protocol_type, details) VALUES (%s, %s, %s)',
        (user_id, protocol_type, details),
    )


@router.post(
    "/messages",
    status_code=201,
    response_model=MessageOut,
    summary="Nachricht senden",
    responses=errs(401, 403, 404, 409, 422),
)
def send_message(body: MessageCreate, response: Response, user: User, db: DbDep) -> Any:
    """Der Absender ist immer der Aufrufer. Der Empfänger muss existieren und aktiv sein. Eine Nachricht an sich
    selbst verweigert die Datenbank (CHECK `message_not_self`): die API meldet das als 422 mit dem Constraint-Namen."""
    with db.tx():
        receiver = db.one('SELECT is_active FROM "Users" WHERE user_id = %s', (body.receiver_id,))
        if receiver is None:
            raise not_found("Empfänger")
        if not receiver["is_active"]:
            raise conflict("Der Empfänger ist deaktiviert.")
        row = db.one(
            f"""
            INSERT INTO "Messages" (sender_id, receiver_id, content) VALUES (%s, %s, %s)
            RETURNING {MESSAGE_COLS}
            """,
            (user.user_id, body.receiver_id, body.content),
        )
        assert row is not None
        _protocol(db, user.user_id, "send_message", f"message_id {row['message_id']} to user {body.receiver_id}")
    response.headers["Location"] = f"/messages/{row['message_id']}"
    return row


@router.get(
    "/messages",
    response_model=list[MessageOut],
    summary="Meine Nachrichten",
    responses=errs(401, 403),
)
def list_messages(
    user: User,
    db: DbDep,
    page: PageDep,
    box: Annotated[
        Literal["all", "inbox", "sent"], Query(description="`inbox` = empfangen, `sent` = gesendet, `all` = beides.")
    ] = "all",
    with_user_id: Annotated[QueryId | None, Query(description="Nur Nachrichten mit diesem Benutzer.")] = None,
    unread_only: Annotated[bool, Query(description="Nur ungelesene empfangene Nachrichten.")] = False,
) -> Any:
    """Es sind immer nur Nachrichten sichtbar, an denen der Aufrufer als Absender oder Empfänger beteiligt ist.
    Neueste zuerst."""
    where = Where()
    if box == "inbox":
        where.add("receiver_id = %(uid)s")
    elif box == "sent":
        where.add("sender_id = %(uid)s")
    else:
        where.add("(sender_id = %(uid)s OR receiver_id = %(uid)s)")
    if with_user_id is not None:
        where.add("(sender_id = %(other)s OR receiver_id = %(other)s)", other=with_user_id)
    if unread_only:
        where.add("receiver_id = %(uid)s AND NOT read_status")
    return db.all(
        f"""
        SELECT {MESSAGE_COLS} FROM "Messages" {where.sql()}
        ORDER BY sent_at DESC, message_id DESC LIMIT %(limit)s OFFSET %(offset)s
        """,
        {**where.params, "uid": user.user_id, "limit": page.limit, "offset": page.offset},
    )


@router.post(
    "/messages/{message_id}/read",
    response_model=MessageOut,
    summary="Als gelesen markieren (Empfänger)",
    responses=errs(401, 403, 404),
)
def mark_message_read(message_id: PathId, user: User, db: DbDep) -> Any:
    """Nur der Empfänger. Wiederholen ist harmlos: das Protokoll (`read_message`) bekommt nur beim ersten Mal
    einen Eintrag. Fremde Nachrichten sind für Unbeteiligte nicht sichtbar (404)."""
    with db.tx():
        message = db.one(f'SELECT {MESSAGE_COLS} FROM "Messages" WHERE message_id = %s', (message_id,))
        if message is None or user.user_id not in (message["sender_id"], message["receiver_id"]):
            raise not_found("Nachricht")
        if message["receiver_id"] != user.user_id:
            raise forbidden("Nur der Empfänger kann eine Nachricht als gelesen markieren.")
        updated = db.one(
            f'UPDATE "Messages" SET read_status = true WHERE message_id = %s AND NOT read_status RETURNING {MESSAGE_COLS}',
            (message_id,),
        )
        if updated is None:
            # already read, possibly by a concurrent request that committed after the first SELECT
            return db.one(f'SELECT {MESSAGE_COLS} FROM "Messages" WHERE message_id = %s', (message_id,))
        _protocol(db, user.user_id, "read_message", f"message_id {message_id}")
        return updated
