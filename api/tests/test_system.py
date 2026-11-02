from conftest import Api, expect


def test_health_reports_database_and_dev_auth(api: Api):
    body = expect(api.get("/health"), 200)
    assert body["status"] == "ok"
    assert body["auth"] == "dev-header"
    assert body["database"]["user"] == "app_user"
    assert body["database"]["database"] == "buechertausch"
    assert body["database"]["version"].replace(".", "").isdigit()  # "17.11", no build or OS details


def test_root_lists_links(api: Api):
    body = expect(api.get("/"), 200)
    assert "X-User-Id" in body["warning"]
    assert any(link["url"].endswith("/openapi.json") for link in body["links"])
