import pytest

from conftest import Api, expect

PROTECTED_SAMPLES = [
    ("GET", "/users/me"),
    ("GET", "/lents"),
    ("GET", "/messages"),
    ("GET", "/copies/1"),
    ("POST", "/lents"),
    ("GET", "/collection-addresses"),
]


@pytest.mark.parametrize(("method", "url"), PROTECTED_SAMPLES)
def test_missing_header_is_401(api: Api, method: str, url: str):
    response = api.call(method, url, json={} if method == "POST" else None)
    body = expect(response, 401)
    assert "X-User-Id" in body["detail"]


@pytest.mark.parametrize("value", ["abc", "0", "-1", "1.5", " ", "99999999999999999999999", "1; DROP TABLE x"])
def test_invalid_user_id_is_401(api: Api, value: str):
    expect(api.get("/users/me", headers={"X-User-Id": value}), 401)


def test_unknown_user_is_401(api: Api):
    expect(api.get("/users/me", user=999), 401)


def test_deactivated_user_is_403(api: Api, db):
    db.run('UPDATE "Users" SET is_active = false WHERE user_id = 5')
    body = expect(api.get("/users/me", user=5), 403)
    assert "deaktiviert" in body["detail"]


def test_role_comes_from_the_database_and_can_be_overridden_for_tests(api: Api):
    me = expect(api.get("/users/me", user=1), 200)
    assert me["role"] == "user" and me["effective_role"] == "user"
    me = expect(api.get("/users/me", user=1, role="moderator"), 200)
    assert me["role"] == "user" and me["effective_role"] == "moderator"


def test_invalid_test_role_is_422(api: Api):
    expect(api.get("/users/me", user=1, role="superuser"), 422)


def test_staff_endpoints_need_a_staff_role(api: Api):
    query = "/reports/monthly?year=2026&month=10"
    assert "Moderatoren" in expect(api.get(query, user=1), 403)["detail"]
    expect(api.get(query, user=1, role="moderator"), 200)
    expect(api.get(query, user=1, role="admin"), 200)


def test_admin_endpoints_need_the_admin_role(api: Api):
    body = {"role": "moderator"}
    expect(api.patch("/users/3", user=1, role="moderator", json=body), 403)
    expect(api.patch("/users/3", user=1, role="admin", json=body), 200)


@pytest.mark.parametrize("origin", ["http://localhost:8080", "http://127.0.0.1:8080"])
def test_cors_preflight_allows_the_dashboard_for_writes(api: Api, origin: str):
    response = api.client.options(
        "/lents",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-user-id,x-test-role",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert method in response.headers["access-control-allow-methods"]
    allowed = response.headers["access-control-allow-headers"].lower()
    assert "x-user-id" in allowed and "x-test-role" in allowed


def test_cors_rejects_other_origins(api: Api):
    response = api.client.options(
        "/lents",
        headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_simple_request_from_the_dashboard_gets_cors_headers(api: Api):
    response = api.get("/health", headers={"Origin": "http://localhost:8080"})
    assert response.headers["access-control-allow-origin"] == "http://localhost:8080"
    assert "Location" in response.headers.get("access-control-expose-headers", "")
