from conftest import Api, expect

NEW_USER = {"username": "Maria", "first_name": "Maria", "last_name": "Muster", "email": "maria@example.com"}


def register(api: Api, **overrides) -> dict:
    return expect(api.post("/users", json={**NEW_USER, **overrides}), 201)


def protocol_types(api: Api, user_id: int, protocol_type: str) -> list[dict]:
    return expect(
        api.get(f"/protocols?user_id={user_id}&protocol_type={protocol_type}", user=1, role="moderator"), 200
    )


# ------------------------------------------------------------------ registration


def test_register_creates_a_plain_user_without_exposing_secrets(api: Api, db):
    response = api.post("/users", json=NEW_USER)
    body = expect(response, 201)
    assert response.headers["location"] == f"/users/{body['user_id']}"
    assert body["role"] == "user" and body["is_active"] is True
    assert "password_hash" not in body and "password" not in body
    stored = db.one('SELECT password_hash FROM "Users" WHERE user_id = %s', (body["user_id"],))
    assert stored["password_hash"] == "!"
    assert len(protocol_types(api, body["user_id"], "create_user")) == 1


def test_register_rejects_role_and_other_unknown_fields(api: Api):
    expect(api.post("/users", json={**NEW_USER, "role": "admin"}), 422)
    expect(api.post("/users", json={**NEW_USER, "is_active": False}), 422)
    expect(api.post("/users", json={**NEW_USER, "user_id": 99}), 422)


def test_register_validates_input(api: Api):
    expect(api.post("/users", json={**NEW_USER, "email": "not-an-email"}), 422)
    expect(api.post("/users", json={**NEW_USER, "username": "ab"}), 422)
    expect(api.post("/users", json={**NEW_USER, "username": "bad name!"}), 422)
    expect(api.post("/users", json={"username": "Maria"}), 422)


def test_register_email_is_unique_case_insensitively(api: Api):
    register(api)
    body = expect(api.post("/users", json={**NEW_USER, "username": "Maria2", "email": "MARIA@Example.com"}), 409)
    assert "E-Mail" in body["detail"]


def test_register_username_is_unique_by_the_database(api: Api):
    register(api)
    body = expect(api.post("/users", json={**NEW_USER, "email": "other@example.com"}), 409)
    assert body["sqlstate"] == "23505"
    assert body["constraint"] == "Users_username_key"


def test_a_failed_registration_leaves_nothing_behind(api: Api, db):
    register(api)
    expect(api.post("/users", json={**NEW_USER, "email": "other@example.com"}), 409)
    assert db.scalar('SELECT count(*) FROM "Users" WHERE username = %s', ("Maria",)) == 1


# ------------------------------------------------------------------ own profile


def test_get_me(api: Api):
    body = expect(api.get("/users/me", user=1), 200)
    assert body["username"] == "Alice" and body["email"] == "alice@example.com"
    assert "password_hash" not in body


def test_update_me_changes_fields_and_logs(api: Api):
    before = expect(api.get("/users/me", user=2), 200)
    body = expect(api.patch("/users/me", user=2, json={"email": "bob.new@example.com", "last_name": "Smith-Jones"}), 200)
    assert body["email"] == "bob.new@example.com" and body["last_name"] == "Smith-Jones"
    assert body["first_name"] == before["first_name"]
    # now() is the start of the transaction, so inside the rollback tests it does not advance (see test_commit.py)
    assert body["updated_at"] >= before["updated_at"]
    entries = protocol_types(api, 2, "update_user")
    assert any("email" in e["details"] and "last_name" in e["details"] for e in entries)


def test_update_me_rejects_duplicates_and_empty_bodies(api: Api):
    expect(api.patch("/users/me", user=2, json={"email": "ALICE@example.com"}), 409)
    expect(api.patch("/users/me", user=2, json={}), 422)
    expect(api.patch("/users/me", user=2, json={"role": "admin"}), 422)
    # the own address is not a duplicate of itself
    expect(api.patch("/users/me", user=2, json={"email": "BOB@example.com"}), 200)


def test_deactivation_is_refused_while_lents_are_open(api: Api):
    # every seed user takes part in at least one requested/accepted/active lent
    for user_id in range(1, 11):
        body = expect(api.delete("/users/me", user=user_id), 409)
        assert "offene Ausleihen" in body["detail"], user_id


def test_deactivation_hides_the_user_and_the_copies(api: Api):
    user = register(api)
    uid = str(user["user_id"])
    address = expect(
        api.post(
            "/collection-addresses",
            user=uid,
            json={"streetname": "Weg", "streetnummber": "1", "city": "Kassel", "state": "HE", "postal_code": "34117"},
        ),
        201,
    )
    copy = expect(
        api.post(
            "/copies",
            user=uid,
            json={
                "book": {"title": "Nur fuer den Test", "authors": ["Test Autor"]},
                "collection_address_id": address["address_id"],
            },
        ),
        201,
    )
    assert any(c["book_copie_id"] == copy["book_copie_id"] for c in expect(api.get("/copies?q=Nur fuer"), 200))

    body = expect(api.delete("/users/me", user=uid), 200)
    assert body["is_active"] is False
    assert len(protocol_types(api, int(uid), "delete_user")) == 1

    expect(api.get("/users/me", user=uid), 403)
    assert not expect(api.get("/copies?q=Nur fuer"), 200)
    expect(api.get(f"/copies/{copy['book_copie_id']}", user=2), 404)
    expect(api.get(f"/users/{uid}", user=2), 404)
    expect(api.get(f"/users/{uid}/books", user=2), 404)
    # moderators still see everything
    assert expect(api.get(f"/copies/{copy['book_copie_id']}", user=2, role="moderator"), 200)["available"] is False


# ------------------------------------------------------------------ other users


def test_other_users_see_only_the_public_part(api: Api):
    body = expect(api.get("/users/2", user=1), 200)
    assert set(body) == {"user_id", "username", "created_at"}
    assert body["username"] == "Bob"


def test_self_and_staff_see_the_full_profile(api: Api):
    assert expect(api.get("/users/2", user=2), 200)["email"] == "bob@example.com"
    assert expect(api.get("/users/2", user=1, role="moderator"), 200)["email"] == "bob@example.com"


def test_get_unknown_user_is_404(api: Api):
    expect(api.get("/users/999", user=1), 404)
    expect(api.get("/users/9223372036854775808", user=1), 422)
    expect(api.get("/users/0", user=1), 422)
    expect(api.get("/users/abc", user=1), 422)


def test_staff_can_list_and_filter_users(api: Api):
    assert len(expect(api.get("/users", user=1, role="moderator"), 200)) == 10
    assert [u["username"] for u in expect(api.get("/users?q=ALI", user=1, role="moderator"), 200)] == ["Alice"]
    assert expect(api.get("/users?role=admin", user=1, role="moderator"), 200) == []
    assert len(expect(api.get("/users?limit=3&offset=8", user=1, role="moderator"), 200)) == 2
    expect(api.get("/users", user=1), 403)


def test_search_input_is_escaped(api: Api):
    assert expect(api.get("/users?q=%25", user=1, role="moderator"), 200) == []
    assert expect(api.get("/users?q=_", user=1, role="moderator"), 200) == []


def test_admin_changes_role_and_status(api: Api):
    body = expect(api.patch("/users/3", user=1, role="admin", json={"role": "moderator"}), 200)
    assert body["role"] == "moderator"
    body = expect(api.patch("/users/3", user=1, role="admin", json={"is_active": False}), 200)
    assert body["is_active"] is False
    expect(api.get("/users/me", user=3), 403)
    expect(api.patch("/users/999", user=1, role="admin", json={"role": "admin"}), 404)
    expect(api.patch("/users/3", user=1, role="admin", json={}), 422)
    expect(api.patch("/users/3", user=1, role="admin", json={"role": "boss"}), 422)
    assert len(protocol_types(api, 1, "update_user")) == 2


# ------------------------------------------------------------------ database functions per user


def test_stats_call_get_full_user_stats(api: Api):
    body = expect(api.get("/users/2/stats", user=2), 200)
    assert body["username"] == "Bob" and body["email"] == "bob@example.com"
    assert body["total_books"] == 1
    assert body["total_lents"] == 4  # as owner of copy 3 and as borrower
    assert body["total_reviews"] == 2
    assert body["total_messages"] == 2


def test_stats_are_restricted(api: Api):
    expect(api.get("/users/2/stats", user=1), 403)
    assert expect(api.get("/users/2/stats", user=1, role="moderator"), 200)["user_id"] == 2
    expect(api.get("/users/999/stats", user=1, role="moderator"), 404)


def test_user_books_call_get_all_books_by_user(api: Api):
    books = expect(api.get("/users/2/books", user=1), 200)
    assert [(b["book_copie_id"], b["title"]) for b in books] == [(3, "Lustiges Taschenbuch Nr. 234")]
    assert books[0]["city"] == "Kassel" and books[0]["max_lent_duration"] == 15
    expect(api.get("/users/999/books", user=1), 404)


def test_user_lents_call_get_all_lents_by_user(api: Api):
    lents = expect(api.get("/users/1/lents", user=1), 200)
    assert {l["lent_id"]: l["my_role"] for l in lents} == {
        1: "borrower",
        3: "owner",
        12: "owner",
        17: "borrower",
        19: "borrower",
    }
    assert [l["lent_id"] for l in lents] == sorted((l["lent_id"] for l in lents), reverse=True)
    owner = expect(api.get("/users/1/lents?role=owner", user=1), 200)
    assert {l["lent_id"] for l in owner} == {3, 12}
    requested = expect(api.get("/users/1/lents?lent_status=requested", user=1), 200)
    assert [l["lent_id"] for l in requested] == [19]
    assert requested[0]["lent_status"] == "requested" and requested[0]["lent_from"] == 8


def test_user_lents_are_restricted(api: Api):
    expect(api.get("/users/2/lents", user=1), 403)
    assert expect(api.get("/users/2/lents", user=1, role="moderator"), 200)
    expect(api.get("/users/999/lents", user=1, role="moderator"), 404)
