"""The generated OpenAPI document is the contract of the dashboard (Swagger UI)."""
import pytest
from openapi_spec_validator import validate

from app.main import app

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}

# Operations that work without X-User-Id
PUBLIC_OPERATIONS = {
    ("GET", "/"),
    ("GET", "/health"),
    ("GET", "/meta/enums"),
    ("GET", "/meta/contract"),
    ("POST", "/users"),
    ("GET", "/authors"),
    ("GET", "/books"),
    ("GET", "/books/{book_id}"),
    ("GET", "/books/{book_id}/reviews"),
    ("GET", "/copies"),
}


@pytest.fixture(scope="module")
def spec() -> dict:
    app.openapi_schema = None
    return app.openapi()


def operations(spec: dict):
    for path, item in spec["paths"].items():
        for method, operation in item.items():
            if method in HTTP_METHODS:
                yield method.upper(), path, operation


def test_spec_is_valid_openapi(spec: dict):
    validate(spec)


def test_covers_the_planned_api_surface(spec: dict):
    assert len(list(operations(spec))) >= 50


def test_every_operation_is_documented(spec: dict):
    problems = []
    tag_names = {t["name"] for t in spec["tags"]}
    for method, path, operation in operations(spec):
        if not operation.get("summary"):
            problems.append(f"{method} {path}: summary missing")
        if len(operation.get("description", "")) < 15:
            problems.append(f"{method} {path}: description missing")
        if not operation.get("tags") or operation["tags"][0] not in tag_names:
            problems.append(f"{method} {path}: tag missing or unknown")
    assert not problems, "\n".join(problems)


def test_operation_ids_are_unique(spec: dict):
    ids = [operation["operationId"] for _, _, operation in operations(spec)]
    assert len(ids) == len(set(ids))


def test_only_dev_user_id_is_a_security_requirement(spec: dict):
    schemes = spec["components"]["securitySchemes"]
    assert list(schemes) == ["DevUserId"]
    assert schemes["DevUserId"] == {
        "type": "apiKey",
        "in": "header",
        "name": "X-User-Id",
        "description": schemes["DevUserId"]["description"],
    }
    for method, path, operation in operations(spec):
        if (method, path) in PUBLIC_OPERATIONS:
            assert "security" not in operation, f"{method} {path} must be public"
        else:
            assert operation.get("security") == [{"DevUserId": []}], f"{method} {path}: wrong security"


def test_test_role_is_an_optional_header_parameter_not_a_security_scheme(spec: dict):
    for method, path, operation in operations(spec):
        if (method, path) in PUBLIC_OPERATIONS:
            continue
        params = [p for p in operation.get("parameters", []) if p["name"] == "X-Test-Role"]
        assert len(params) == 1, f"{method} {path}: X-Test-Role parameter missing"
        assert params[0]["in"] == "header"
        assert params[0]["required"] is False


def test_server_url_comes_from_the_environment(spec: dict):
    assert spec["servers"][0]["url"] == "http://localhost:8000"


def test_error_responses_use_the_common_error_model(spec: dict):
    operation = spec["paths"]["/lents"]["post"]
    for status in ("401", "403", "404", "409", "422"):
        assert status in operation["responses"], status
    schema = operation["responses"]["409"]["content"]["application/json"]["schema"]
    assert schema == {"$ref": "#/components/schemas/ErrorResponse"}


@pytest.mark.parametrize("path", ["/docs", "/redoc"])
def test_fastapi_own_docs_are_disabled(api, path: str):
    # they would load their assets from a CDN; the dashboard is the swagger-ui container
    assert api.get(path).status_code == 404
