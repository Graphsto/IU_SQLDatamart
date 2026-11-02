from conftest import Api, expect


def author_id(api: Api, name: str) -> int:
    """The seed inserts authors without ORDER BY, so their IDs are not in list order."""
    return next(a["author_id"] for a in expect(api.get("/authors?limit=200"), 200) if a["name"] == name)


def book_id(api: Api, title: str) -> int:
    return next(b["book_id"] for b in expect(api.get("/books?limit=200"), 200) if b["title"] == title)


def titles(items: list[dict]) -> list[str]:
    return [i["title"] for i in items]


# ------------------------------------------------------------------ authors


def test_list_and_search_authors(api: Api):
    authors = expect(api.get("/authors"), 200)
    assert len(authors) == 12
    assert [a["name"] for a in authors] == sorted(a["name"] for a in authors)
    orwell = expect(api.get("/authors?q=orwell"), 200)
    assert [(a["name"], a["book_count"]) for a in orwell] == [("George Orwell", 2)]


def test_create_author_is_find_or_create(api: Api, db):
    created = expect(api.post("/authors", user=1, json={"name": "Kai Meyer"}), 201)
    assert created["name"] == "Kai Meyer"
    again = api.post("/authors", user=2, json={"name": "  kai MEYER "})
    assert expect(again, 200)["author_id"] == created["author_id"]
    assert db.scalar('SELECT count(*) FROM "Authors" WHERE lower(name) = %s', ("kai meyer",)) == 1


def test_create_author_requires_login_and_a_name(api: Api):
    expect(api.post("/authors", json={"name": "Kai Meyer"}), 401)
    expect(api.post("/authors", user=1, json={"name": "  "}), 422)
    expect(api.post("/authors", user=1, json={"name": "x" * 201}), 422)


def test_create_author_reports_existing_duplicates_instead_of_guessing(api: Api, db):
    db.run('INSERT INTO "Authors" (name) VALUES (%s), (%s)', ("Doppelt", "doppelt"))
    body = expect(api.post("/authors", user=1, json={"name": "DOPPELT"}), 409)
    assert "Mehrdeutig" in body["detail"]


# ------------------------------------------------------------------ books


def test_list_books_with_counts(api: Api):
    books = expect(api.get("/books"), 200)
    assert len(books) == 12
    potter = next(b for b in books if b["title"].startswith("Harry Potter"))
    assert potter["authors"] == [{"author_id": author_id(api, "J.K. Rowling"), "name": "J.K. Rowling"}]
    assert (potter["copies_total"], potter["copies_available"]) == (1, 1)
    assert potter["review_count"] == 2 and potter["avg_rating"] == 4.5
    omen = next(b for b in books if b["title"] == "Ein gutes Omen")
    assert [a["name"] for a in omen["authors"]] == ["Neil Gaiman", "Terry Pratchett"]
    assert (omen["copies_total"], omen["copies_available"], omen["review_count"], omen["avg_rating"]) == (0, 0, 0, None)


def test_filter_books(api: Api):
    science_fiction = {"1984", "Schöne neue Welt", "Fahrenheit 451"}
    assert {b["title"] for b in expect(api.get("/books?genre=Science-Fiction"), 200)} == science_fiction
    assert titles(expect(api.get("/books?q=tolkien"), 200)) == ["Der Hobbit"]
    assert titles(expect(api.get("/books?q=SISYPHOS"), 200)) == ["Der Mythos des Sisyphos"]
    assert len(expect(api.get("/books?language=english"), 200)) == 3
    orwell = author_id(api, "George Orwell")
    assert {b["title"] for b in expect(api.get(f"/books?author_id={orwell}"), 200)} == {"1984", "Animal Farm"}
    available = expect(api.get("/books?available_only=true"), 200)
    assert {b["title"] for b in available} == {
        "Harry Potter und der Stein der Weisen",
        "Pride and Prejudice",
        "Schöne neue Welt",
        "Fahrenheit 451",
    }
    expect(api.get("/books?genre=Unbekannt"), 422)


def test_books_pagination(api: Api):
    page1 = expect(api.get("/books?limit=5"), 200)
    page2 = expect(api.get("/books?limit=5&offset=5"), 200)
    assert len(page1) == 5 and len(page2) == 5
    assert not {b["book_id"] for b in page1} & {b["book_id"] for b in page2}
    expect(api.get("/books?limit=0"), 422)
    expect(api.get("/books?limit=201"), 422)


def test_book_detail_lists_available_copies(api: Api):
    body = expect(api.get("/books/1"), 200)
    assert body["title"].startswith("Harry Potter")
    assert [c["book_copie_id"] for c in body["available_copies"]] == [1]
    assert body["available_copies"][0]["owner"] == "Alice"
    animal_farm = book_id(api, "Animal Farm")  # no copy at all
    assert expect(api.get(f"/books/{animal_farm}"), 200)["available_copies"] == []
    lent_out = expect(api.get(f"/books/{book_id(api, '1984')}"), 200)  # its only copy is lent out
    assert (lent_out["copies_total"], lent_out["copies_available"], lent_out["available_copies"]) == (1, 0, [])
    expect(api.get("/books/999"), 404)


def test_inactive_owners_do_not_count(api: Api, db):
    db.run('UPDATE "Users" SET is_active = false WHERE user_id = 1')
    body = expect(api.get("/books/1"), 200)
    assert (body["copies_total"], body["copies_available"]) == (0, 0)
    assert body["available_copies"] == []


def test_book_reviews(api: Api):
    reviews = expect(api.get("/books/1/reviews"), 200)
    assert sorted((r["reviewer"], r["rating"]) for r in reviews) == [("Bob", 4), ("Charlie", 5)]
    assert reviews[0]["created_at"] >= reviews[1]["created_at"]
    assert expect(api.get(f"/books/{book_id(api, 'Animal Farm')}/reviews"), 200) == []
    expect(api.get("/books/999/reviews"), 404)
