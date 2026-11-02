from typing import Annotated, Any

from fastapi import APIRouter, Query, Response

from ..db import Db, Where, like_contains
from ..deps import DbDep, PageDep, PathId, QueryId, User
from ..enums import BookLanguage, Genre
from ..errors import conflict, not_found
from ..responses import errs
from ..schemas import Author, AuthorCreate, BookDetail, BookInput, BookItem, BookReview

router = APIRouter(tags=["Katalog"])

BOOK_SELECT = """
WITH av AS (SELECT book_id, count(*) AS n FROM available_books GROUP BY book_id)
SELECT b.book_id, b.title, b.published_date, b.genre, b.language,
       COALESCE((SELECT jsonb_agg(jsonb_build_object('author_id', a.author_id, 'name', a.name)
                                  ORDER BY a.name, a.author_id)
                 FROM "BookAuthors" ba JOIN "Authors" a ON a.author_id = ba.author_id
                 WHERE ba.book_id = b.book_id), '[]'::jsonb) AS authors,
       (SELECT count(*)
        FROM "Book_copy" bc
        JOIN "CollectionAdresses" ca ON ca.address_id = bc.collection_address_id
        JOIN "Users" u ON u.user_id = ca.user_id AND u.is_active
        WHERE bc.book_id = b.book_id) AS copies_total,
       COALESCE(av.n, 0) AS copies_available,
       r.avg_rating,
       COALESCE(r.review_count, 0) AS review_count
FROM "Books" b
LEFT JOIN av ON av.book_id = b.book_id
LEFT JOIN LATERAL (
    SELECT round(avg(rv.rating), 2) AS avg_rating, count(*) AS review_count
    FROM "Reviews" rv
    JOIN "Lents" l ON l.lent_id = rv.lent_id
    JOIN "Book_copy" bc2 ON bc2.book_copie_id = l.book_copie_id
    WHERE bc2.book_id = b.book_id
) r ON true
"""


def normalize(text: str) -> str:
    """Key for 'same name': trimmed and lower case. The SQL side compares lower(btrim(...)) with this key."""
    return text.strip().lower()


def find_or_create_author(db: Db, name: str) -> tuple[dict[str, Any], bool]:
    """Author with this (normalized) name, created if missing. Call inside db.tx().

    The schema has no UNIQUE constraint on Authors.name (report F11). Without the advisory lock two
    parallel requests would both insert. More than one existing match is reported, not guessed.
    """
    key = normalize(name)
    db.advisory_lock(f"author:{key}")
    rows = db.all('SELECT author_id, name FROM "Authors" WHERE lower(btrim(name)) = %s ORDER BY author_id', (key,))
    if len(rows) > 1:
        ids = ", ".join(str(r["author_id"]) for r in rows)
        raise conflict(f"Mehrdeutig: {len(rows)} Autoren heißen »{name}« (IDs {ids}). Das Schema erzwingt keine Eindeutigkeit.")
    if rows:
        return rows[0], False
    row = db.one('INSERT INTO "Authors" (name) VALUES (%s) RETURNING author_id, name', (name,))
    assert row is not None
    return row, True


def find_or_create_book(db: Db, book: BookInput) -> tuple[int, bool]:
    """Book with the same normalized title and the same set of author names, created if missing.

    Matching policy: exactly one match is reused (its other fields stay as they are), none creates a
    book, several matches are reported. The book lock is always taken before the author locks and
    authors are locked in sorted order, so parallel requests cannot deadlock. Call inside db.tx().
    """
    title_key = normalize(book.title)
    authors = {normalize(name): name for name in reversed(book.authors)}  # first spelling wins
    author_keys = sorted(authors)
    db.advisory_lock(f"book:{title_key}")
    matches = db.all(
        """
        SELECT b.book_id FROM "Books" b
        WHERE lower(btrim(b.title)) = %(title)s
          AND (SELECT COALESCE(array_agg(DISTINCT lower(btrim(a.name)) ORDER BY lower(btrim(a.name))), ARRAY[]::text[])
               FROM "BookAuthors" ba JOIN "Authors" a ON a.author_id = ba.author_id
               WHERE ba.book_id = b.book_id) = %(authors)s::text[]
        ORDER BY b.book_id
        """,
        {"title": title_key, "authors": author_keys},
    )
    if len(matches) > 1:
        ids = ", ".join(str(m["book_id"]) for m in matches)
        raise conflict(
            f"Mehrdeutig: {len(matches)} Bücher mit Titel »{book.title}« und denselben Autoren (IDs {ids}). "
            "Bitte book_id angeben."
        )
    if matches:
        return matches[0]["book_id"], False

    author_ids = [find_or_create_author(db, authors[key])[0]["author_id"] for key in author_keys]
    row = db.one(
        'INSERT INTO "Books" (title, published_date, genre, language) VALUES (%s, %s, %s, %s) RETURNING book_id',
        (
            book.title,
            book.published_date,
            book.genre.value if book.genre else None,
            book.language.value if book.language else None,
        ),
    )
    assert row is not None
    for author_id in dict.fromkeys(author_ids):
        db.run('INSERT INTO "BookAuthors" (book_id, author_id) VALUES (%s, %s)', (row["book_id"], author_id))
    return row["book_id"], True


@router.get("/authors", response_model=list[Author], summary="Autoren suchen")
def list_authors(
    db: DbDep,
    page: PageDep,
    q: Annotated[str | None, Query(max_length=100, description="Teil des Namens.")] = None,
) -> Any:
    """Autoren der Tabelle `Authors` mit der Anzahl ihrer Bücher, alphabetisch. Öffentlich."""
    where = Where()
    if q:
        where.add("a.name ILIKE %(q)s", q=like_contains(q))
    return db.all(
        f"""
        SELECT a.author_id, a.name,
               (SELECT count(*) FROM "BookAuthors" ba WHERE ba.author_id = a.author_id) AS book_count
        FROM "Authors" a {where.sql()}
        ORDER BY a.name, a.author_id LIMIT %(limit)s OFFSET %(offset)s
        """,
        {**where.params, "limit": page.limit, "offset": page.offset},
    )


@router.post(
    "/authors",
    status_code=201,
    response_model=Author,
    summary="Autor anlegen oder finden",
    responses={200: {"model": Author, "description": "Der Autor existierte schon (Name ohne Beachtung der Groß-/Kleinschreibung)."}}
    | errs(401, 403, 409, 422),
)
def create_author(body: AuthorCreate, response: Response, user: User, db: DbDep) -> Any:
    """Find-or-create: Die Datenbank erzwingt keine eindeutigen Autorennamen, deshalb sucht die API zuerst
    (ohne Beachtung der Groß-/Kleinschreibung) und legt nur an, wenn es den Namen noch nicht gibt (201, sonst 200).
    Gibt es den Namen bereits mehrfach, antwortet sie mit 409."""
    with db.tx():
        author, created = find_or_create_author(db, body.name)
    response.status_code = 201 if created else 200
    if created:
        response.headers["Location"] = f"/authors/{author['author_id']}"
    return {**author, "book_count": 0 if created else None}


@router.get("/books", response_model=list[BookItem], summary="Bücher suchen")
def list_books(
    db: DbDep,
    page: PageDep,
    q: Annotated[str | None, Query(max_length=100, description="Teil von Titel oder Autorenname.")] = None,
    genre: Genre | None = None,
    language: BookLanguage | None = None,
    author_id: QueryId | None = None,
    available_only: Annotated[bool, Query(description="Nur Bücher mit mindestens einem verfügbaren Exemplar.")] = False,
) -> Any:
    """Buchtitel mit Autoren, Anzahl der Exemplare, Anzahl der aktuell verfügbaren Exemplare (View
    `available_books`) und der durchschnittlichen Bewertung."""
    where = Where()
    if q:
        where.add(
            """(b.title ILIKE %(q)s OR EXISTS (
                   SELECT 1 FROM "BookAuthors" ba JOIN "Authors" a ON a.author_id = ba.author_id
                   WHERE ba.book_id = b.book_id AND a.name ILIKE %(q)s))""",
            q=like_contains(q),
        )
    if genre is not None:
        where.add("b.genre = %(genre)s", genre=genre.value)
    if language is not None:
        where.add("b.language = %(language)s", language=language.value)
    if author_id is not None:
        where.add(
            'EXISTS (SELECT 1 FROM "BookAuthors" ba2 WHERE ba2.book_id = b.book_id AND ba2.author_id = %(author_id)s)',
            author_id=author_id,
        )
    if available_only:
        where.add("COALESCE(av.n, 0) > 0")
    return db.all(
        f"{BOOK_SELECT} {where.sql()} ORDER BY b.title, b.book_id LIMIT %(limit)s OFFSET %(offset)s",
        {**where.params, "limit": page.limit, "offset": page.offset},
    )


@router.get("/books/{book_id}", response_model=BookDetail, summary="Buch mit verfügbaren Exemplaren", responses=errs(404))
def get_book(book_id: PathId, db: DbDep) -> Any:
    """Ein Buchtitel mit Autoren und Bewertungsdurchschnitt, dazu die Exemplare, die jetzt angefragt werden können
    (View `available_books`). Öffentlich."""
    book = db.one(f'{BOOK_SELECT} WHERE b.book_id = %s', (book_id,))
    if book is None:
        raise not_found("Buch")
    copies = db.all(
        """
        SELECT book_copie_id, book_id, title, authors, genre, language, condition, max_lent_duration,
               shipping_availability, shipping_cost, city, postal_code, owner_id, owner
        FROM available_books WHERE book_id = %s ORDER BY book_copie_id
        """,
        (book_id,),
    )
    return {**book, "available_copies": copies}


@router.get(
    "/books/{book_id}/reviews",
    response_model=list[BookReview],
    summary="Bewertungen eines Buchs",
    responses=errs(404),
)
def list_book_reviews(book_id: PathId, db: DbDep, page: PageDep) -> Any:
    """Bewertet wird der Buchtitel, nicht das Exemplar: Review -> Lent -> Book_copy -> Book."""
    if not db.scalar('SELECT EXISTS (SELECT 1 FROM "Books" WHERE book_id = %s)', (book_id,)):
        raise not_found("Buch")
    return db.all(
        """
        SELECT rv.review_id, rv.lent_id, rv.rating, rv.comment, rv.created_at, u.username AS reviewer
        FROM "Reviews" rv
        JOIN "Lents" l ON l.lent_id = rv.lent_id
        JOIN "Book_copy" bc ON bc.book_copie_id = l.book_copie_id
        JOIN "Users" u ON u.user_id = l.lent_to
        WHERE bc.book_id = %s
        ORDER BY rv.created_at DESC, rv.review_id DESC
        LIMIT %s OFFSET %s
        """,
        (book_id, page.limit, page.offset),
    )
