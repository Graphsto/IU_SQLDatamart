from conftest import Api, expect

ADDRESS = {
    "streetname": "Testweg",
    "streetnummber": "12a",
    "city": "Kassel",
    "state": "HE",
    "postal_code": "34117",
    "country": "DE",
    "latitude": 51.3127,
    "longitude": 9.4797,
}


def make_address(api: Api, user: int = 1, **overrides) -> dict:
    return expect(api.post("/collection-addresses", user=user, json={**ADDRESS, **overrides}), 201)


# ------------------------------------------------------------------ collection addresses


def test_list_own_collection_addresses(api: Api):
    addresses = expect(api.get("/collection-addresses", user=1), 200)
    assert [a["address_id"] for a in addresses] == [1]
    assert addresses[0]["user_id"] == 1


def test_collection_addresses_of_others_are_staff_only(api: Api):
    expect(api.get("/collection-addresses?user_id=2", user=1), 403)
    assert [a["address_id"] for a in expect(api.get("/collection-addresses?user_id=2", user=1, role="moderator"), 200)] == [2]


def test_create_collection_address_belongs_to_the_caller(api: Api):
    response = api.post("/collection-addresses", user=3, json=ADDRESS)
    body = expect(response, 201)
    assert body["user_id"] == 3
    assert response.headers["location"] == f"/collection-addresses/{body['address_id']}"
    assert body["streetnummber"] == "12a"
    assert body["latitude"] == 51.3127 and body["longitude"] == 9.4797


def test_create_collection_address_cannot_name_another_owner(api: Api):
    expect(api.post("/collection-addresses", user=3, json={**ADDRESS, "user_id": 1}), 422)


def test_coordinates_come_in_pairs_and_stay_in_range(api: Api):
    only_lat = {k: v for k, v in ADDRESS.items() if k != "longitude"}
    expect(api.post("/collection-addresses", user=1, json=only_lat), 422)
    expect(api.post("/collection-addresses", user=1, json={**ADDRESS, "latitude": 91}), 422)
    expect(api.post("/collection-addresses", user=1, json={**ADDRESS, "longitude": -180.5}), 422)
    without = {k: v for k, v in ADDRESS.items() if k not in ("latitude", "longitude", "country")}
    body = expect(api.post("/collection-addresses", user=1, json=without), 201)
    assert body["latitude"] is None and body["country"] is None


def test_update_collection_address(api: Api):
    address = make_address(api)
    url = f"/collection-addresses/{address['address_id']}"
    body = expect(api.patch(url, user=1, json={"city": "Göttingen"}), 200)
    assert body["city"] == "Göttingen" and body["streetname"] == "Testweg"
    # now() is the start of the transaction, so inside the rollback tests it does not advance (see test_commit.py)
    assert body["updated_at"] >= address["updated_at"]
    body = expect(api.patch(url, user=1, json={"latitude": None, "longitude": None, "country": None}), 200)
    assert body["latitude"] is None and body["longitude"] is None and body["country"] is None
    body = expect(api.patch(url, user=1, json={"latitude": 50.0, "longitude": 8.0}), 200)
    assert (body["latitude"], body["longitude"]) == (50.0, 8.0)


def test_update_collection_address_validation_and_ownership(api: Api):
    address = make_address(api)
    url = f"/collection-addresses/{address['address_id']}"
    expect(api.patch(url, user=1, json={}), 422)
    expect(api.patch(url, user=1, json={"latitude": 50.0}), 422)
    expect(api.patch(url, user=1, json={"latitude": 50.0, "longitude": None}), 422)
    expect(api.patch(url, user=1, json={"city": None}), 422)
    expect(api.patch(url, user=2, json={"city": "Berlin"}), 403)
    expect(api.patch("/collection-addresses/999", user=1, json={"city": "Berlin"}), 404)


def test_delete_collection_address(api: Api):
    address = make_address(api)
    url = f"/collection-addresses/{address['address_id']}"
    expect(api.delete(url, user=2), 403)
    expect(api.delete(url, user=1), 204)
    expect(api.delete(url, user=1), 404)


def test_delete_is_refused_while_copies_use_the_address(api: Api):
    body = expect(api.delete("/collection-addresses/1", user=1), 409)
    assert "Exemplaren" in body["detail"]


# ------------------------------------------------------------------ time windows


def test_list_seed_time_windows_sorted(api: Api):
    windows = expect(api.get("/collection-addresses/1/time-windows", user=2), 200)
    assert [(w["weekday"], w["start_time"], w["end_time"]) for w in windows] == [
        (1, "09:00:00", "12:00:00"),
        (6, "10:00:00", "12:00:00"),
    ]
    expect(api.get("/collection-addresses/999/time-windows", user=1), 404)


def test_create_time_window_through_the_database_function(api: Api):
    response = api.post(
        "/collection-addresses/1/time-windows",
        user=1,
        json={"weekday": 2, "start_time": "10:00:00", "end_time": "12:00:00"},
    )
    body = expect(response, 201)
    assert body["address_id"] == 1 and body["weekday"] == 2
    assert [w["weekday"] for w in expect(api.get("/collection-addresses/1/time-windows", user=1), 200)] == [1, 2, 6]


def test_overlapping_time_window_is_a_conflict_from_the_function(api: Api):
    window = {"weekday": 2, "start_time": "10:00", "end_time": "12:00"}
    expect(api.post("/collection-addresses/1/time-windows", user=1, json=window), 201)
    body = expect(
        api.post("/collection-addresses/1/time-windows", user=1, json={**window, "start_time": "11:00", "end_time": "13:00"}),
        409,
    )
    assert "overlaps" in body["detail"]
    assert body["sqlstate"] == "P0001"


def test_adjacent_time_windows_are_allowed(api: Api):
    expect(api.post("/collection-addresses/1/time-windows", user=1, json={"weekday": 2, "start_time": "10:00", "end_time": "12:00"}), 201)
    expect(api.post("/collection-addresses/1/time-windows", user=1, json={"weekday": 2, "start_time": "12:00", "end_time": "14:00"}), 201)
    # another weekday or another address never collides
    expect(api.post("/collection-addresses/1/time-windows", user=1, json={"weekday": 3, "start_time": "10:00", "end_time": "12:00"}), 201)


def test_time_window_validation_and_ownership(api: Api):
    url = "/collection-addresses/1/time-windows"
    expect(api.post(url, user=1, json={"weekday": 7, "start_time": "10:00", "end_time": "12:00"}), 422)
    expect(api.post(url, user=1, json={"weekday": 2, "start_time": "12:00", "end_time": "10:00"}), 422)
    expect(api.post(url, user=1, json={"weekday": 2, "start_time": "10:00", "end_time": "10:00"}), 422)
    expect(api.post(url, user=2, json={"weekday": 2, "start_time": "10:00", "end_time": "12:00"}), 403)
    expect(api.post("/collection-addresses/999/time-windows", user=1, json={"weekday": 2, "start_time": "10:00", "end_time": "12:00"}), 404)


def test_delete_time_window(api: Api):
    window = expect(
        api.post("/collection-addresses/1/time-windows", user=1, json={"weekday": 4, "start_time": "08:00", "end_time": "09:00"}),
        201,
    )
    url = f"/time-windows/{window['window_id']}"
    expect(api.delete(url, user=2), 403)
    expect(api.delete(url, user=1), 204)
    expect(api.delete(url, user=1), 404)


def test_deleting_an_address_removes_its_time_windows(api: Api, db):
    address = make_address(api)
    expect(
        api.post(f"/collection-addresses/{address['address_id']}/time-windows", user=1, json={"weekday": 1, "start_time": "08:00", "end_time": "09:00"}),
        201,
    )
    expect(api.delete(f"/collection-addresses/{address['address_id']}", user=1), 204)
    assert db.scalar('SELECT count(*) FROM "CollectionTimeWindows" WHERE address_id = %s', (address["address_id"],)) == 0


# ------------------------------------------------------------------ user addresses


def test_user_addresses_crud(api: Api):
    assert len(expect(api.get("/user-addresses", user=1), 200)) == 1
    body = {"street_address": "Neuweg 3", "city": "Kassel", "state": "HE", "postal_code": "34117", "country": "DE"}
    created = expect(api.post("/user-addresses", user=1, json=body), 201)
    assert created["user_id"] == 1
    assert len(expect(api.get("/user-addresses", user=1), 200)) == 2
    expect(api.delete(f"/user-addresses/{created['address_id']}", user=2), 403)
    expect(api.delete(f"/user-addresses/{created['address_id']}", user=1), 204)
    expect(api.delete(f"/user-addresses/{created['address_id']}", user=1), 404)


def test_user_addresses_validation_and_visibility(api: Api):
    expect(api.post("/user-addresses", user=1, json={"street_address": "x"}), 422)
    expect(api.get("/user-addresses?user_id=2", user=1), 403)
    assert expect(api.get("/user-addresses?user_id=2", user=1, role="admin"), 200)[0]["user_id"] == 2
