from datetime import datetime

from conftest import Api, expect

# Seed: copy 2 belongs to Olaf (4), is available, no shipping. Copy 1 belongs to Alice (1), is available,
# shipping possible. Copy 8 (Heidi, 8) is available but already has a requested lent (19) from Alice.


def request_copy(api: Api, copy_id: int, user: int, shipping: bool = False) -> dict:
    return expect(api.post("/lents", user=user, json={"book_copie_id": copy_id, "shipping": shipping}), 201)


def act(api: Api, lent_id: int, action: str, user: int, status: int = 200) -> dict:
    return expect(api.post(f"/lents/{lent_id}/{action}", user=user), status)


def lent_log(api: Api, lent_id: int) -> list[tuple[str, int]]:
    entries = expect(api.get("/protocols?limit=200", user=1, role="moderator"), 200)
    mine = [e for e in entries if e["details"] and (e["details"] == f"lent_id {lent_id}" or e["details"].startswith(f"lent_id {lent_id},"))]
    return [(e["protocol_type"], e["user_id"]) for e in reversed(mine)]


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


# ------------------------------------------------------------------ the whole workflow


def test_complete_lifecycle_with_protocol_and_visibility(api: Api):
    stats_before = expect(api.get("/users/1/stats", user=1), 200)

    lent = request_copy(api, 2, 1)
    lent_id = lent["lent_id"]
    assert lent["lent_status"] == "requested"
    assert (lent["borrower_id"], lent["owner_id"], lent["book_copie_id"]) == (1, 4, 2)
    assert lent["title"] == "Pride and Prejudice" and lent["borrower"] == "Alice" and lent["owner"] == "Olaf"
    assert lent["shipping"] is None and lent["review"] is None and lent["pickup"] is None
    assert lent["overdue"] is False and lent["return_date"] is None

    # only the participants and staff can see a lent
    expect(api.get(f"/lents/{lent_id}", user=3), 403)
    expect(api.get(f"/lents/{lent_id}", user=4), 200)
    expect(api.get(f"/lents/{lent_id}", user=3, role="moderator"), 200)
    expect(api.get("/lents/999", user=1), 404)

    accepted = act(api, lent_id, "accept", 4)
    assert accepted["lent_status"] == "accepted"
    assert accepted["pickup"]["address"]["streetname"] == "Reistergasse"
    assert [(w["weekday"], w["start_time"], w["end_time"]) for w in accepted["pickup"]["time_windows"]] == [
        (4, "11:00:00", "15:00:00")
    ]
    assert expect(api.get(f"/lents/{lent_id}", user=1), 200)["pickup"]["address"]["city"] == "Kassel"
    assert expect(api.get("/copies", user=1), 200) and 2 not in [c["book_copie_id"] for c in expect(api.get("/copies"), 200)]

    active = act(api, lent_id, "hand-over", 4)
    assert active["lent_status"] == "active"
    assert (parse(active["lent_enddate"]) - parse(active["lent_startdate"])).days == 60  # max_lent_duration of the copy
    assert active["pickup"] is not None  # the owner still sees it
    assert expect(api.get(f"/lents/{lent_id}", user=1), 200)["pickup"] is None  # the borrower has the book now
    assert expect(api.get(f"/lents/{lent_id}/overdue", user=1), 200)["must_be_returned"] is False

    returned = act(api, lent_id, "return", 4)
    assert returned["lent_status"] == "returned" and returned["return_date"] is not None
    assert 2 in [c["book_copie_id"] for c in expect(api.get("/copies"), 200)]  # available again

    review = expect(api.post(f"/lents/{lent_id}/review", user=1, json={"rating": 5, "comment": "Great book!"}), 201)
    assert review["lent_id"] == lent_id and review["rating"] == 5 and review["comment"] == "Great book!"
    assert expect(api.get(f"/lents/{lent_id}", user=4), 200)["review"]["review_id"] == review["review_id"]

    assert lent_log(api, lent_id) == [
        ("create_request", 1),
        ("accept_request", 4),
        ("lending", 4),
        ("returning", 4),
        ("create_review", 1),
    ]
    stats_after = expect(api.get("/users/1/stats", user=1), 200)
    assert stats_after["total_lents"] == stats_before["total_lents"] + 1
    assert stats_after["total_reviews"] == stats_before["total_reviews"] + 1


def test_review_changes_the_book_rating(api: Api):
    pride = next(b for b in expect(api.get("/books?q=Pride"), 200))
    assert (pride["review_count"], pride["avg_rating"]) == (2, 3.5)
    lent_id = request_copy(api, 2, 1)["lent_id"]
    for action in ("accept", "hand-over", "return"):
        act(api, lent_id, action, 4)
    expect(api.post(f"/lents/{lent_id}/review", user=1, json={"rating": 5}), 201)
    pride = next(b for b in expect(api.get("/books?q=Pride"), 200))
    assert (pride["review_count"], pride["avg_rating"]) == (3, 4.0)


def test_reject_ends_the_request(api: Api):
    lent_id = request_copy(api, 2, 1)["lent_id"]
    assert act(api, lent_id, "reject", 4)["lent_status"] == "rejected"
    for action in ("accept", "reject", "hand-over", "return"):
        act(api, lent_id, action, 4, status=409)
    assert lent_log(api, lent_id) == [("create_request", 1), ("reject_request", 4)]
    assert 2 in [c["book_copie_id"] for c in expect(api.get("/copies"), 200)]  # still available


# ------------------------------------------------------------------ rules of the request


def test_request_rules_are_enforced_by_the_function(api: Api):
    own = expect(api.post("/lents", user=1, json={"book_copie_id": 1}), 422)
    assert "own book copy" in own["detail"] and own["sqlstate"] == "P0001"
    shipping = expect(api.post("/lents", user=1, json={"book_copie_id": 2, "shipping": True}), 422)
    assert "shipping is not available" in shipping["detail"]
    lent_out = expect(api.post("/lents", user=5, json={"book_copie_id": 3}), 409)  # lent 17 is active
    assert "currently lent out" in lent_out["detail"]
    unknown = expect(api.post("/lents", user=1, json={"book_copie_id": 999}), 404)
    assert "does not exist" in unknown["detail"]
    duplicate = expect(api.post("/lents", user=1, json={"book_copie_id": 8}), 409)  # lent 19 is already requested
    assert "already requested" in duplicate["detail"]


def test_request_validates_the_body(api: Api):
    expect(api.post("/lents", user=1, json={}), 422)
    expect(api.post("/lents", user=1, json={"book_copie_id": 0}), 422)
    expect(api.post("/lents", user=1, json={"book_copie_id": "x"}), 422)
    expect(api.post("/lents", user=1, json={"book_copie_id": 2, "lent_to": 3}), 422)
    expect(api.post("/lents", json={"book_copie_id": 2}), 401)


def test_requests_for_copies_of_deactivated_owners_are_refused_by_the_api(api: Api, db):
    db.run('UPDATE "Users" SET is_active = false WHERE user_id = 4')  # owner of copy 2
    expect(api.post("/lents", user=1, json={"book_copie_id": 2}), 404)
    assert db.scalar('SELECT count(*) FROM "Lents" WHERE book_copie_id = 2 AND lent_status = %s', ("requested",)) == 0


def test_failed_request_leaves_no_lent_and_no_protocol(api: Api, db):
    lents, protocols = db.scalar('SELECT count(*) FROM "Lents"'), db.scalar('SELECT count(*) FROM "Protocols"')
    expect(api.post("/lents", user=1, json={"book_copie_id": 1}), 422)
    assert (db.scalar('SELECT count(*) FROM "Lents"'), db.scalar('SELECT count(*) FROM "Protocols"')) == (lents, protocols)


def test_several_requests_for_one_copy_are_allowed_but_only_one_can_be_accepted(api: Api):
    first = request_copy(api, 2, 1)["lent_id"]
    second = request_copy(api, 2, 3)["lent_id"]
    act(api, first, "accept", 4)
    body = act(api, second, "accept", 4, status=409)
    assert "already lent out" in body["detail"]
    assert 2 not in [c["book_copie_id"] for c in expect(api.get("/copies"), 200)]
    expect(api.post("/lents", user=5, json={"book_copie_id": 2}), 409)


# ------------------------------------------------------------------ who may do what


def test_only_the_owner_decides(api: Api):
    lent_id = request_copy(api, 2, 1)["lent_id"]
    for action in ("accept", "reject", "hand-over", "return"):
        act(api, lent_id, action, 1, status=403)  # the borrower
        act(api, lent_id, action, 3, status=403)  # a stranger
    expect(api.post(f"/lents/{lent_id}/accept", user=3, role="admin"), 403)  # the function would refuse as well
    expect(api.post("/lents/999/accept", user=4), 404)
    expect(api.post(f"/lents/{lent_id}/accept"), 401)


def test_the_state_machine_is_enforced_by_the_functions(api: Api):
    lent_id = request_copy(api, 2, 1)["lent_id"]
    act(api, lent_id, "hand-over", 4, status=409)  # not accepted yet
    act(api, lent_id, "return", 4, status=409)
    act(api, lent_id, "accept", 4)
    act(api, lent_id, "accept", 4, status=409)
    act(api, lent_id, "reject", 4, status=409)
    act(api, lent_id, "return", 4, status=409)  # not active yet
    act(api, lent_id, "hand-over", 4)
    act(api, lent_id, "hand-over", 4, status=409)
    act(api, lent_id, "return", 4)
    act(api, lent_id, "return", 4, status=409)
    assert lent_log(api, lent_id) == [
        ("create_request", 1),
        ("accept_request", 4),
        ("lending", 4),
        ("returning", 4),
    ]


def test_review_rules(api: Api):
    lent_id = request_copy(api, 2, 1)["lent_id"]
    early = expect(api.post(f"/lents/{lent_id}/review", user=1, json={"rating": 5}), 409)
    assert "not returned yet" in early["detail"]
    for action in ("accept", "hand-over", "return"):
        act(api, lent_id, action, 4)
    url = f"/lents/{lent_id}/review"
    expect(api.post(url, user=4, json={"rating": 5}), 403)  # the owner cannot review
    expect(api.post(url, user=3, json={"rating": 5}), 403)
    expect(api.post(url, user=1, json={"rating": 0}), 422)
    expect(api.post(url, user=1, json={"rating": 6}), 422)
    expect(api.post(url, user=1, json={"rating": 3, "user_id": 3}), 422)
    expect(api.post("/lents/999/review", user=1, json={"rating": 3}), 404)
    response = api.post(url, user=1, json={"rating": 3})
    assert expect(response, 201)["comment"] is None
    assert response.headers["location"] == f"/lents/{lent_id}"
    again = expect(api.post(url, user=1, json={"rating": 4}), 409)
    assert "already been reviewed" in again["detail"]


# ------------------------------------------------------------------ lists and overdue


def test_my_lents(api: Api):
    lents = expect(api.get("/lents", user=1), 200)
    assert {l["lent_id"] for l in lents} == {1, 3, 12, 17, 19}
    assert {l["lent_id"]: l["my_role"] for l in lents}[19] == "borrower"
    assert [l["lent_id"] for l in expect(api.get("/lents?role=owner", user=1), 200)] == [12, 3]
    assert [l["lent_id"] for l in expect(api.get("/lents?lent_status=active", user=1), 200)] == [17]
    assert [l["lent_id"] for l in expect(api.get("/lents?limit=2", user=1), 200)] == [19, 17]
    expect(api.get("/lents"), 401)


def test_overdue_seed_lent(api: Api, db):
    db.run(
        "UPDATE \"Lents\" SET lent_startdate = now() - interval '50 days', lent_enddate = now() - interval '5 days' "
        "WHERE lent_id = 13"
    )
    db.run(
        "UPDATE \"Lents\" SET lent_startdate = now() - interval '10 days', lent_enddate = now() + interval '5 days' "
        "WHERE lent_id = 17"
    )
    body = expect(api.get("/lents/13/overdue", user=3), 200)  # owner Charlie, borrower Olaf
    assert body["must_be_returned"] is True and body["lent_status"] == "active"
    assert expect(api.get("/lents/13", user=4), 200)["overdue"] is True
    assert expect(api.get("/lents/17/overdue", user=1), 200)["must_be_returned"] is False
    assert expect(api.get("/lents/1/overdue", user=1), 200)["must_be_returned"] is False  # returned
    expect(api.get("/lents/13/overdue", user=1), 403)
    expect(api.get("/lents/999/overdue", user=1), 404)


def test_lent_detail_of_a_seed_lent_with_shipping(api: Api):
    detail = expect(api.get("/lents/18", user=7), 200)  # owner Irmtraut, borrower Heidi
    assert detail["lent_status"] == "active" and detail["shipping_requested"] is True
    assert detail["shipping"]["shipping_status"] == "shipped"
    assert detail["shipping"]["tracking_number"] == "DHL-00340434161094042559"
    assert detail["borrower_current_address"]["street_address"] == "Walnussweg"
    assert expect(api.get("/lents/18", user=8), 200)["borrower_current_address"] is None


# ------------------------------------------------------------------ shipping


def test_shipping_flow(api: Api, db):
    lent_id = request_copy(api, 1, 3, shipping=True)["lent_id"]  # Charlie wants Alice's copy shipped
    url = f"/lents/{lent_id}/shipping"
    expect(api.put(url, user=1, json={"shipping_status": "pending"}), 409)  # not accepted yet

    accepted = act(api, lent_id, "accept", 1)
    assert accepted["pickup"]["address"]["address_id"] == 1  # the owner sees where the copy is
    assert expect(api.get(f"/lents/{lent_id}", user=3), 200)["pickup"] is None  # shipping: no pickup for the borrower
    assert accepted["borrower_current_address"]["street_address"] == "Eichenweg"
    assert expect(api.get(f"/lents/{lent_id}", user=3), 200)["borrower_current_address"] is None

    created = api.put(url, user=1, json={"shipping_status": "pending"})
    assert expect(created, 201)["tracking_number"] is None
    expect(api.put(url, user=3, json={"shipping_status": "delivered"}), 403)  # nothing shipped yet
    expect(api.put(url, user=3, json={"shipping_status": "shipped"}), 403)
    expect(api.put(url, user=4, json={"shipping_status": "shipped"}), 403)  # a stranger

    shipped = expect(api.put(url, user=1, json={"shipping_status": "shipped", "tracking_number": "TEST-123"}), 200)
    assert shipped["shipping_status"] == "shipped" and shipped["tracking_number"] == "TEST-123"
    expect(api.put(url, user=1, json={"shipping_status": "delivered"}), 403)  # the borrower confirms delivery
    expect(api.put(url, user=3, json={"shipping_status": "delivered", "tracking_number": "OTHER"}), 403)
    expect(api.put(url, user=1, json={"shipping_status": "pending"}), 409)  # only forward

    delivered = expect(api.put(url, user=3, json={"shipping_status": "delivered"}), 200)
    assert delivered["shipping_status"] == "delivered" and delivered["tracking_number"] == "TEST-123"
    assert delivered["shipping_id"] == shipped["shipping_id"]
    assert db.scalar('SELECT count(*) FROM "Shipping" WHERE lent_id = %s', (lent_id,)) == 1
    assert expect(api.get(f"/lents/{lent_id}", user=3), 200)["shipping"]["shipping_status"] == "delivered"


def test_staff_may_create_and_advance_shipping(api: Api):
    lent_id = request_copy(api, 1, 3, shipping=True)["lent_id"]
    act(api, lent_id, "accept", 1)
    body = expect(api.put(f"/lents/{lent_id}/shipping", user=5, role="moderator", json={"shipping_status": "delivered"}), 201)
    assert body["shipping_status"] == "delivered"
    expect(api.put(f"/lents/{lent_id}/shipping", user=5, role="moderator", json={"shipping_status": "shipped"}), 409)


def test_shipping_preconditions(api: Api):
    pickup_lent = request_copy(api, 2, 1)["lent_id"]
    act(api, pickup_lent, "accept", 4)
    body = expect(api.put(f"/lents/{pickup_lent}/shipping", user=4, json={"shipping_status": "pending"}), 409)
    assert "kein Versand" in body["detail"]
    returned = expect(api.put("/lents/12/shipping", user=1, json={"shipping_status": "delivered"}), 409)  # returned
    assert "accepted/active" in returned["detail"]
    expect(api.put("/lents/18/shipping", user=1, json={"shipping_status": "delivered"}), 403)  # not a participant
    expect(api.put("/lents/999/shipping", user=1, json={"shipping_status": "pending"}), 404)
    expect(api.put("/lents/18/shipping", user=7, json={"shipping_status": "unknown"}), 422)
    expect(api.put("/lents/18/shipping", user=7, json={"shipping_status": "shipped", "tracking_number": ""}), 422)


def test_tracking_numbers_are_unique_by_the_database(api: Api):
    lent_id = request_copy(api, 1, 3, shipping=True)["lent_id"]
    act(api, lent_id, "accept", 1)
    url = f"/lents/{lent_id}/shipping"
    body = expect(api.put(url, user=1, json={"shipping_status": "shipped", "tracking_number": "DHL-00340434161094042557"}), 409)
    assert body["sqlstate"] == "23505" and body["constraint"] == "Shipping_tracking_number_key"
    assert expect(api.get(f"/lents/{lent_id}", user=1), 200)["shipping"] is None  # nothing half-written


def test_duplicate_shipping_rows_are_reported_not_guessed(api: Api, db):
    # the schema allows several Shipping rows per lent (Shipping.lent_id is not unique)
    db.run('INSERT INTO "Shipping" (lent_id, shipping_status) VALUES (18, %s)', ("pending",))
    body = expect(api.put("/lents/18/shipping", user=7, json={"shipping_status": "shipped"}), 409)
    assert "Versandzeilen" in body["detail"]
