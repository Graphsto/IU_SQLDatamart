import math

from conftest import Api, expect

NEW_BOOK = {
    "title": "Die Wahrheit",
    "authors": ["Terry Pratchett"],
    "published_date": "2000-01-01",
    "genre": "Fantasy",
    "language": "german",
}

# Seed: copy -> owner address coordinates of the available copies (the seed puts "Kassel" in the USA)
COORDINATES = {1: (39.799, -89.644), 2: (39.5631, -95.1216), 6: (39.017, -94.2816), 8: (40.6687, -74.1143)}
NEW_YORK = (40.75, -74.0)


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0088 * 2 * math.asin(math.sqrt(h))


def ids(copies: list[dict]) -> list[int]:
    return [c["book_copie_id"] for c in copies]


def create_copy(api: Api, user: int = 1, **overrides) -> dict:
    body = {"book": NEW_BOOK, "collection_address_id": user, **overrides}
    return expect(api.post("/copies", user=user, json=body), 201)


# ------------------------------------------------------------------ search (view available_books)


def test_search_returns_only_available_copies_sorted_by_title(api: Api):
    copies = expect(api.get("/copies"), 200)
    assert ids(copies) == [8, 1, 2, 6]
    first = copies[0]
    assert first["title"] == "Fahrenheit 451" and first["owner"] == "Heidi" and first["city"] == "Kassel"
    assert first["distance_km"] is None


def test_search_filters(api: Api):
    assert ids(expect(api.get("/copies?shipping=available"), 200)) == [1]
    assert ids(expect(api.get("/copies?genre=Science-Fiction"), 200)) == [8, 6]
    assert ids(expect(api.get("/copies?language=english"), 200)) == [2]
    assert ids(expect(api.get("/copies?owner_id=4"), 200)) == [2]
    assert ids(expect(api.get("/copies?q=rowling"), 200)) == [1]
    assert ids(expect(api.get("/copies?q=PRIDE"), 200)) == [2]
    assert len(expect(api.get("/copies?city=KASSEL"), 200)) == 4
    assert expect(api.get("/copies?condition=poor"), 200) == []
    assert expect(api.get("/copies?q=orwell"), 200) == []  # his copies are lent out
    expect(api.get("/copies?condition=broken"), 422)


def test_search_pagination(api: Api):
    assert ids(expect(api.get("/copies?limit=2"), 200)) == [8, 1]
    assert ids(expect(api.get("/copies?limit=2&offset=2"), 200)) == [2, 6]


def test_radius_search_uses_the_coordinates_of_the_collection_address(api: Api):
    lat, lon = NEW_YORK
    near = expect(api.get(f"/copies?lat={lat}&lon={lon}&radius_km=100"), 200)
    assert ids(near) == [8]
    assert abs(near[0]["distance_km"] - haversine_km(NEW_YORK, COORDINATES[8])) < 0.5

    wide = expect(api.get(f"/copies?lat={lat}&lon={lon}&radius_km=1500&sort=distance"), 200)
    assert ids(wide) == [8, 1]
    for copy in wide:
        assert abs(copy["distance_km"] - haversine_km(NEW_YORK, COORDINATES[copy["book_copie_id"]])) < 0.5

    everywhere = expect(api.get(f"/copies?lat={lat}&lon={lon}&sort=distance"), 200)
    assert ids(everywhere) == [8, 1, 6, 2]
    assert all(c["distance_km"] is not None for c in everywhere)


def test_radius_search_combines_with_other_filters(api: Api):
    lat, lon = NEW_YORK
    assert ids(expect(api.get(f"/copies?lat={lat}&lon={lon}&radius_km=2000&shipping=available"), 200)) == [1]


def test_radius_search_needs_consistent_parameters(api: Api):
    expect(api.get("/copies?lat=40"), 422)
    expect(api.get("/copies?lon=-74"), 422)
    expect(api.get("/copies?radius_km=10"), 422)
    expect(api.get("/copies?sort=distance"), 422)
    expect(api.get("/copies?lat=40&lon=-74&radius_km=0"), 422)
    expect(api.get("/copies?lat=91&lon=-74"), 422)


def test_copies_without_coordinates_drop_out_of_a_radius_search(api: Api):
    expect(api.patch("/collection-addresses/1", user=1, json={"latitude": None, "longitude": None}), 200)
    lat, lon = NEW_YORK
    assert 1 not in ids(expect(api.get(f"/copies?lat={lat}&lon={lon}&radius_km=20000"), 200))
    assert 1 in ids(expect(api.get("/copies"), 200))


# ------------------------------------------------------------------ detail (view fullBookInfo)


def test_copy_detail_hides_the_owner_name_from_others(api: Api):
    other = expect(api.get("/copies/1", user=2), 200)
    assert other["title"].startswith("Harry Potter") and other["owner"] == "Alice"
    assert other["user_firstname"] is None and other["user_lastname"] is None
    assert other["available"] is True
    own = expect(api.get("/copies/1", user=1), 200)
    assert (own["user_firstname"], own["user_lastname"]) == ("Alice", "Doe")
    staff = expect(api.get("/copies/1", user=2, role="moderator"), 200)
    assert staff["user_firstname"] == "Alice"


def test_copy_detail_reports_availability(api: Api):
    assert expect(api.get("/copies/3", user=1), 200)["available"] is False  # active lent
    expect(api.get("/copies/999", user=1), 404)
    expect(api.get("/copies/1"), 401)


def test_copies_of_inactive_owners_are_hidden_although_the_view_shows_them(api: Api, db):
    db.run('UPDATE "Users" SET is_active = false WHERE user_id = 1')
    # fullBookInfo itself does not filter inactive owners (report F7)
    assert db.scalar("SELECT count(*) FROM fullBookInfo WHERE book_copie_id = 1") == 1
    expect(api.get("/copies/1", user=2), 404)
    assert expect(api.get("/copies/1", user=2, role="moderator"), 200)["available"] is False
    assert 1 not in ids(expect(api.get("/copies"), 200))


# ------------------------------------------------------------------ create


def test_create_copy_with_a_new_book_and_database_defaults(api: Api, db):
    authors_before = db.scalar('SELECT count(*) FROM "Authors"')
    response = api.post("/copies", user=1, json={"book": NEW_BOOK, "collection_address_id": 1})
    body = expect(response, 201)
    assert response.headers["location"] == f"/copies/{body['book_copie_id']}"
    assert body["title"] == "Die Wahrheit" and body["authors"] == "Terry Pratchett"
    assert body["genre"] == "Fantasy" and body["language"] == "german"
    assert body["owner_id"] == 1 and body["available"] is True
    assert body["max_lent_duration"] == 60  # database defaults
    assert body["shipping_availability"] == "not_available"
    assert body["shipping_cost"] == 4.0
    assert body["condition"] is None
    assert db.scalar('SELECT count(*) FROM "Authors"') == authors_before  # the author already existed
    log = expect(api.get(f"/protocols?book_copie_id={body['book_copie_id']}", user=1, role="moderator"), 200)
    assert [(p["protocol_type"], p["user_id"]) for p in log] == [("create_book", 1)]


def test_create_copy_with_explicit_values(api: Api):
    body = create_copy(
        api,
        condition="like_new",
        max_lent_duration=30,
        shipping_availability="available",
        shipping_cost=4.5,
    )
    assert (body["condition"], body["max_lent_duration"]) == ("like_new", 30)
    assert (body["shipping_availability"], body["shipping_cost"]) == ("available", 4.5)


def test_create_copy_finds_the_same_book_by_title_and_authors(api: Api, db):
    first = create_copy(api)
    again = create_copy(api, book={**NEW_BOOK, "title": " DIE wahrheit ", "authors": ["terry PRATCHETT"]})
    assert again["book_id"] == first["book_id"]
    assert again["book_copie_id"] != first["book_copie_id"]
    assert db.scalar('SELECT count(*) FROM "Books" WHERE lower(title) = %s', ("die wahrheit",)) == 1


def test_create_copy_with_another_author_set_is_another_book(api: Api, db):
    first = create_copy(api)
    other = create_copy(api, book={**NEW_BOOK, "authors": ["Terry Pratchett", "Neil Gaiman"]})
    assert other["book_id"] != first["book_id"]
    assert other["authors"] == "Neil Gaiman, Terry Pratchett"
    # each author set still maps to exactly one book
    assert create_copy(api, book={**NEW_BOOK, "authors": ["Neil Gaiman", "Terry Pratchett"]})["book_id"] == other["book_id"]
    assert create_copy(api)["book_id"] == first["book_id"]


def test_create_copy_creates_missing_authors_once(api: Api, db):
    create_copy(api, book={"title": "Neues Buch", "authors": ["Neue Autorin", "neue autorin", "Zweiter Autor"]})
    assert db.scalar('SELECT count(*) FROM "Authors" WHERE lower(name) = %s', ("neue autorin",)) == 1
    assert db.scalar('SELECT count(*) FROM "Authors" WHERE name = %s', ("Zweiter Autor",)) == 1


def test_create_copy_for_an_existing_book_id(api: Api):
    hobbit = next(b["book_id"] for b in expect(api.get("/books?q=Hobbit"), 200))
    body = expect(api.post("/copies", user=2, json={"book_id": hobbit, "collection_address_id": 2}), 201)
    assert body["title"] == "Der Hobbit" and body["owner"] == "Bob"


def test_create_copy_reports_ambiguous_books(api: Api, db):
    for _ in range(2):
        book_id = db.scalar('INSERT INTO "Books" (title) VALUES (%s) RETURNING book_id', ("Zwilling",))
        db.run(
            'INSERT INTO "BookAuthors" (book_id, author_id) SELECT %s, author_id FROM "Authors" WHERE name = %s',
            (book_id, "Terry Pratchett"),
        )
    body = expect(
        api.post("/copies", user=1, json={"book": {"title": "Zwilling", "authors": ["Terry Pratchett"]}, "collection_address_id": 1}),
        409,
    )
    assert "Mehrdeutig" in body["detail"] and "book_id" in body["detail"]


def test_create_copy_checks_the_address_and_the_book(api: Api):
    expect(api.post("/copies", user=1, json={"book": NEW_BOOK, "collection_address_id": 2}), 403)
    expect(api.post("/copies", user=1, json={"book": NEW_BOOK, "collection_address_id": 999}), 404)
    expect(api.post("/copies", user=1, json={"book_id": 999, "collection_address_id": 1}), 404)
    expect(api.post("/copies", json={"book": NEW_BOOK, "collection_address_id": 1}), 401)


def test_create_copy_validates_input(api: Api):
    base = {"collection_address_id": 1}
    expect(api.post("/copies", user=1, json=base), 422)  # neither book_id nor book
    expect(api.post("/copies", user=1, json={**base, "book_id": 1, "book": NEW_BOOK}), 422)  # both
    expect(api.post("/copies", user=1, json={**base, "book": {**NEW_BOOK, "authors": []}}), 422)
    expect(api.post("/copies", user=1, json={**base, "book": {**NEW_BOOK, "title": " "}}), 422)
    for field, value in [
        ("max_lent_duration", 0),
        ("max_lent_duration", 121),
        ("shipping_cost", -1),
        ("shipping_cost", 10),
        ("condition", "broken"),
        ("shipping_availability", "maybe"),
        ("owner_id", 2),
    ]:
        expect(api.post("/copies", user=1, json={**base, "book": NEW_BOOK, field: value}), 422)


# ------------------------------------------------------------------ update


def test_update_copy(api: Api):
    copy = create_copy(api)
    url = f"/copies/{copy['book_copie_id']}"
    body = expect(api.patch(url, user=1, json={"condition": "fair", "max_lent_duration": 45, "shipping_cost": 2.5}), 200)
    assert (body["condition"], body["max_lent_duration"], body["shipping_cost"]) == ("fair", 45, 2.5)
    log = expect(api.get(f"/protocols?book_copie_id={copy['book_copie_id']}&protocol_type=update_book", user=1, role="admin"), 200)
    assert len(log) == 1 and "condition" in log[0]["details"]


def test_update_copy_can_only_move_to_an_own_address(api: Api):
    copy = create_copy(api)
    url = f"/copies/{copy['book_copie_id']}"
    second = expect(
        api.post(
            "/collection-addresses",
            user=1,
            json={"streetname": "Zweitweg", "streetnummber": "2", "city": "Hannover", "state": "NI", "postal_code": "30159"},
        ),
        201,
    )
    assert expect(api.patch(url, user=1, json={"collection_address_id": second["address_id"]}), 200)["city"] == "Hannover"
    expect(api.patch(url, user=1, json={"collection_address_id": 2}), 403)  # would hand the copy to Bob
    expect(api.patch(url, user=1, json={"collection_address_id": 999}), 404)


def test_update_copy_ownership_and_validation(api: Api):
    expect(api.patch("/copies/1", user=2, json={"condition": "poor"}), 403)
    expect(api.patch("/copies/999", user=1, json={"condition": "poor"}), 404)
    expect(api.patch("/copies/1", user=1, json={}), 422)
    expect(api.patch("/copies/1", user=1, json={"max_lent_duration": 500}), 422)
    expect(api.patch("/copies/1", user=1, json={"book_id": 2}), 422)


# ------------------------------------------------------------------ delete


def test_delete_copy_without_lents(api: Api):
    copy = create_copy(api)
    url = f"/copies/{copy['book_copie_id']}"
    expect(api.delete(url, user=2), 403)
    expect(api.delete(url, user=1), 204)
    expect(api.get(url, user=1), 404)
    expect(api.delete(url, user=1), 404)
    log = expect(api.get("/protocols?protocol_type=delete_book&user_id=1", user=1, role="moderator"), 200)
    assert len(log) == 1 and log[0]["book_copie_id"] is None and str(copy["book_copie_id"]) in log[0]["details"]


def test_delete_copy_with_a_lent_history_is_refused(api: Api, db):
    # copy 1 has two returned lents with reviews: deleting would cascade over all of it
    body = expect(api.delete("/copies/1", user=1), 409)
    assert "Historie" in body["detail"]
    assert db.scalar('SELECT count(*) FROM "Lents" WHERE book_copie_id = 1') == 2
    assert db.scalar('SELECT count(*) FROM "Reviews"') == 13


def test_a_new_request_blocks_deleting_the_copy(api: Api):
    copy = create_copy(api, shipping_availability="available")
    expect(api.post("/lents", user=2, json={"book_copie_id": copy["book_copie_id"]}), 201)
    expect(api.delete(f"/copies/{copy['book_copie_id']}", user=1), 409)
