"""The API covers the whole database, and the tests call the whole API (executable, not just claimed)."""
import os
import re
from pathlib import Path

import pytest

from app.main import app
from conftest import ROUTES_HIT, Api

APP_SOURCES = "\n".join(p.read_text(encoding="utf-8") for p in sorted((Path(__file__).parent.parent / "app").rglob("*.py")))
HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


def source_mentions(name: str) -> bool:
    return re.search(rf"\b{re.escape(name)}\b", APP_SOURCES, flags=re.IGNORECASE) is not None


def test_every_table_view_and_function_of_the_database_is_used_by_the_api(api: Api, db):
    tables = [r["name"] for r in db.all("SELECT table_name::text AS name FROM information_schema.tables WHERE table_schema = 'public'")]
    functions = [
        r["name"]
        for r in db.all(
            """
            SELECT p.proname::text AS name FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'public' AND p.prokind = 'f'
              AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = p.oid AND d.deptype IN ('e', 'i'))
            """
        )
    ]
    # the range type timerange creates its own constructor functions: they have an internal dependency and are skipped
    assert len(tables) == 16, tables  # 13 tables + 3 views
    assert len(functions) == 13, functions
    unused = [name for name in tables + functions if not source_mentions(name)]
    assert not unused, f"database objects no endpoint uses: {unused}"


@pytest.fixture(scope="session", autouse=True)
def every_operation_was_called():
    """At the end of a full run of the rollback tests every operation of the OpenAPI document must have been called.
    Enabled with REQUIRE_FULL_COVERAGE=1 (the compose command of the test stack does that), because a run of
    a single test file cannot reach everything."""
    yield
    if os.environ.get("REQUIRE_FULL_COVERAGE") != "1":
        return
    app.openapi_schema = None
    spec = app.openapi()
    declared = {
        (method.upper(), path)
        for path, item in spec["paths"].items()
        for method in item
        if method.upper() in HTTP_METHODS
    }
    missing = sorted(declared - ROUTES_HIT)
    assert not missing, f"operations no test called: {missing}"
    assert len(declared) >= 50
