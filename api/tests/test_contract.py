"""Contract between the API and the database (the SQL files are the source of truth, the API adapts)."""
import pytest

from app.enums import DB_ENUMS
from app.errors import classify_raise_exception
from conftest import Api, expect


def test_enums_of_the_api_match_the_database(api: Api):
    db_enums = expect(api.get("/meta/enums"), 200)["enums"]
    for name, py_enum in DB_ENUMS.items():
        assert db_enums[name] == [member.value for member in py_enum], name
    assert set(db_enums) == set(DB_ENUMS), "the database has an enum type the API does not know"


def test_contract_report_is_ok(api: Api):
    report = expect(api.get("/meta/contract"), 200)
    failed = [c for c in report["checks"] if not c["ok"]]
    assert report["ok"], failed
    assert {c["name"] for c in report["checks"]} == {
        "Tabellen vorhanden",
        "Views vorhanden",
        "Funktionen mit erwarteter Signatur",
        "Enum-Werte identisch",
        "Rechte des API-Benutzers",
    }


@pytest.mark.parametrize(
    ("message", "status"),
    [
        # create_book_request
        ("User with ID 5 does not exist or is not active.", 404),
        ("Book copy with ID 99 does not exist.", 404),
        ("User with ID 1 cannot lend their own book copy with ID 1.", 422),
        ("Book copy with ID 4 can only be picked up, shipping is not available.", 422),
        ("Book copy with ID 4 is currently lent out.", 409),
        ("User with ID 1 has already requested book copy with ID 4.", 409),
        # accept / reject / hand over / return
        ("Lent request with ID 19 does not exist, is not in requested status or the copy does not belong to user with ID 3.", 409),
        ("Lent with ID 20 does not exist, is not in accepted status or the copy does not belong to user with ID 3.", 409),
        ("Lent with ID 13 does not exist, is not in active status or the copy does not belong to user with ID 3.", 409),
        ("The book copy of lent request 19 is already lent out.", 409),
        # write_review
        ("Lent with ID 1 does not exist, is not returned yet or was not borrowed by user with ID 3.", 409),
        ("Lent with ID 1 has already been reviewed.", 409),
        # create_collection_time_window
        ("Collection address with ID 1 does not exist or does not belong to user with ID 2.", 404),
        ("Time window 11:00:00 - 13:00:00 overlaps with an existing time window of address 1 on weekday 2.", 409),
        # book_must_be_returned
        ("Lent with ID 99 does not exist.", 404),
        # anything new falls back to a conflict
        ("Something unexpected.", 409),
    ],
)
def test_raise_exception_messages_are_classified(message: str, status: int):
    assert classify_raise_exception(message) == status
