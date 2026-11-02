"""What building an API on top of this schema showed about the database design.

Facts are checked with plain SQL as app_user, the same role the API uses. The F numbers refer to
Phase3/API_Testbericht.md. Tests named test_positive_* show rules the database enforces itself.
Tests marked xfail(strict=True) describe behaviour the SQL files are meant to have but do not have:
when the SQL is fixed they start to pass, the strict marker then fails the run and the marker must go.
"""
import psycopg
import pytest
from psycopg import errors

from conftest import Api, expect

VIEWS = ("available_books", "overdue_lents", "fullbookinfo")


def expected_error(db, sql: str, params=None) -> errors.Error:
    with pytest.raises(psycopg.Error) as excinfo:
        db.run(sql, params)
    return excinfo.value


# ------------------------------------------------------------------ positive: rules the database enforces


def test_positive_one_open_lent_per_copy(db):
    # copy 5 already has an accepted lent (21): a second open lent violates the partial unique index
    error = expected_error(db, 'INSERT INTO "Lents" (lent_status, book_copie_id, lent_to) VALUES (%s, 5, 1)', ("accepted",))
    assert isinstance(error, errors.UniqueViolation) and error.diag.constraint_name == "lents_one_open_per_copy"
    # any number of open *requests* is fine
    db.run('INSERT INTO "Lents" (lent_status, book_copie_id, lent_to) VALUES (%s, 5, 1)', ("requested",))
    db.run('INSERT INTO "Lents" (lent_status, book_copie_id, lent_to) VALUES (%s, 5, 2)', ("requested",))


def test_positive_overlapping_time_windows_are_rejected(db):
    error = expected_error(
        db,
        'INSERT INTO "CollectionTimeWindows" (address_id, weekday, start_time, end_time) VALUES (1, 1, %s, %s)',
        ("11:00", "13:00"),  # seed: address 1, Monday 09:00-12:00
    )
    assert isinstance(error, errors.ExclusionViolation) and error.diag.constraint_name == "no_overlapping_window"
    db.run(
        'INSERT INTO "CollectionTimeWindows" (address_id, weekday, start_time, end_time) VALUES (1, 1, %s, %s)',
        ("12:00", "13:00"),
    )


def test_positive_protocols_are_append_only(db):
    assert isinstance(expected_error(db, 'UPDATE "Protocols" SET details = %s', ("x",)), errors.InsufficientPrivilege)
    assert isinstance(expected_error(db, 'DELETE FROM "Protocols"'), errors.InsufficientPrivilege)
    db.run('INSERT INTO "Protocols" (protocol_type, details) VALUES (%s, %s)', ("create_user", "appended"))


def test_positive_the_api_role_cannot_change_the_schema(db):
    assert isinstance(expected_error(db, "CREATE TABLE intruder (id int)"), errors.InsufficientPrivilege)
    assert isinstance(expected_error(db, 'DROP TABLE "Users" CASCADE'), errors.InsufficientPrivilege)
    assert isinstance(expected_error(db, 'ALTER TABLE "Users" ADD COLUMN x int'), errors.InsufficientPrivilege)


@pytest.mark.parametrize(
    ("sql", "constraint"),
    [
        ('INSERT INTO "Reviews" (lent_id, rating) VALUES (1, 6)', "Reviews_rating_check"),
        ('INSERT INTO "CollectionAdresses" (user_id, streetname, streetnummber, city, state, postal_code, latitude, longitude) '
         "VALUES (1, 'a', '1', 'b', 'c', 'd', 91, 0)", "latitude_range"),
        ('INSERT INTO "CollectionAdresses" (user_id, streetname, streetnummber, city, state, postal_code, latitude) '
         "VALUES (1, 'a', '1', 'b', 'c', 'd', 10)", "coordinates_complete"),
        ('INSERT INTO "Messages" (sender_id, receiver_id, content) VALUES (1, 1, %s)', "message_not_self"),
        ('INSERT INTO "Lents" (lent_status, book_copie_id, lent_to, lent_startdate, lent_enddate) '
         "VALUES ('requested', 2, 1, now(), now() - interval '1 day')", "lent_dates_valid"),
        ('INSERT INTO "Lents" (lent_status, book_copie_id, lent_to, lent_startdate, lent_enddate) '
         "VALUES ('requested', 2, 1, now(), now() + interval '121 days')", "lent_date_max_duration"),
    ],
)
def test_positive_check_constraints(db, sql: str, constraint: str):
    params = ("hi",) if "%s" in sql else None
    error = expected_error(db, sql, params)
    assert isinstance(error, errors.CheckViolation) and error.diag.constraint_name == constraint


def test_positive_one_review_per_lent(db):
    error = expected_error(db, 'INSERT INTO "Reviews" (lent_id, rating) VALUES (1, 3)')  # lent 1 has one already
    assert isinstance(error, errors.UniqueViolation) and error.diag.constraint_name == "Reviews_lent_id_key"


def test_positive_enums_reject_unknown_values(db):
    assert isinstance(expected_error(db, 'UPDATE "Users" SET role = %s WHERE user_id = 1', ("boss",)), errors.InvalidTextRepresentation)


# ------------------------------------------------------------------ F1: init error and view privileges


def test_f1_views_cannot_be_written_in_practice_because_they_are_not_updatable(db):
    for sql in (
        "INSERT INTO available_books (title) VALUES ('x')",
        "UPDATE available_books SET title = 'x'",
        "DELETE FROM overdue_lents",
        "DELETE FROM fullBookInfo",
    ):
        assert isinstance(expected_error(db, sql), errors.ObjectNotInPrerequisiteState), sql


@pytest.mark.xfail(
    strict=True,
    reason="F1: 9grantPrivileges.sql ends with REVOKE ... user_ranking (view does not exist), the statement fails, "
    "so the views keep INSERT/UPDATE/DELETE from the GRANT ON ALL TABLES; fullBookInfo is not in the list either",
)
def test_f1_desired_views_are_read_only_for_the_api_role(db):
    for view in VIEWS:
        for privilege in ("INSERT", "UPDATE", "DELETE"):
            assert not db.scalar("SELECT has_table_privilege('app_user', %s, %s)", (f"public.{view}", privilege)), (view, privilege)


# ------------------------------------------------------------------ F2, F3: roles and privileges


def test_f2_the_seed_has_no_moderator_and_no_admin(db):
    assert [r["role"] for r in db.all('SELECT DISTINCT role FROM "Users"')] == ["user"]


def test_f3_the_api_role_may_read_password_hashes_and_the_api_must_never_select_them(db):
    assert db.scalar("SELECT has_column_privilege('app_user', 'public.\"Users\"', 'password_hash', 'SELECT')") is True
    assert db.scalar('SELECT count(*) FROM "Users" WHERE password_hash <> %s', ("",)) == 10


def test_f3_every_function_takes_the_acting_user_as_a_parameter(db):
    # the database trusts the caller: any app_user session can act as any user (the API has to authorize)
    db.run("SELECT hand_over_book(%s, %s)", (20, 10))  # lent 20 is accepted, owner of its copy is user 10
    assert db.scalar('SELECT lent_status::text FROM "Lents" WHERE lent_id = 20') == "active"


# ------------------------------------------------------------------ F4: error reporting of the functions


def test_f4_all_function_errors_share_one_sqlstate_and_differ_only_in_the_message(db):
    calls = (
        ("SELECT create_book_request(999, 1, false)", "does not exist"),
        ("SELECT create_book_request(1, 1, false)", "cannot lend their own"),
        ("SELECT accept_book_request(999, 1)", "does not exist, is not in requested status"),
        ("SELECT write_review(1, 2, 5, 'x')", "does not exist, is not returned yet or was not borrowed"),
    )
    states = set()
    for sql, text in calls:
        error = expected_error(db, sql)
        assert isinstance(error, errors.RaiseException) and text in error.diag.message_primary
        states.add(error.sqlstate)
    assert states == {"P0001"}


def test_f4_one_message_covers_missing_row_wrong_state_and_wrong_owner(db):
    # lent 1 exists, is returned and belongs to owner 4: three different causes, one text, one SQLSTATE
    for lent_id, user_id in ((999, 4), (1, 4), (1, 2)):
        message = expected_error(db, "SELECT accept_book_request(%s, %s)", (lent_id, user_id)).diag.message_primary
        assert "does not exist, is not in requested status or the copy does not belong to user" in message


# ------------------------------------------------------------------ F5: missing checks on the copy


def test_f5_the_database_accepts_nonsense_lent_durations_and_costs(db):
    for duration, cost in ((0, 4), (-5, 4), (500, 4), (30, -5)):
        db.run(
            'INSERT INTO "Book_copy" (book_id, collection_address_id, max_lent_duration, shipping_cost) VALUES (1, 1, %s, %s)',
            (duration, cost),
        )


def test_f5_the_error_for_a_too_long_duration_appears_late_at_the_first_request(api: Api, db):
    copy_id = db.scalar(
        'INSERT INTO "Book_copy" (book_id, collection_address_id, max_lent_duration) VALUES (1, 1, 500) RETURNING book_copie_id'
    )
    body = expect(api.post("/lents", user=2, json={"book_copie_id": copy_id}), 422)
    # a raw CHECK violation of the lent, not a message the borrower could act on
    assert body["sqlstate"] == "23514" and body["constraint"] == "lent_date_max_duration"


@pytest.mark.xfail(strict=True, reason="F5: no CHECK on Book_copy.max_lent_duration (Tutor_Feedback: positive Leihdauern)")
def test_f5_desired_database_rejects_non_positive_lent_durations(db):
    with pytest.raises(errors.CheckViolation):
        db.run('INSERT INTO "Book_copy" (book_id, collection_address_id, max_lent_duration) VALUES (1, 1, 0)')


@pytest.mark.xfail(strict=True, reason="F5: no CHECK on Book_copy.shipping_cost (Tutor_Feedback: Versandkosten)")
def test_f5_desired_database_rejects_negative_shipping_costs(db):
    with pytest.raises(errors.CheckViolation):
        db.run('INSERT INTO "Book_copy" (book_id, collection_address_id, shipping_cost) VALUES (1, 1, -5)')


def test_f5_numeric_3_2_caps_the_shipping_cost_at_9_99(db):
    error = expected_error(db, 'INSERT INTO "Book_copy" (book_id, collection_address_id, shipping_cost) VALUES (1, 1, 10)')
    assert isinstance(error, errors.NumericValueOutOfRange)


# ------------------------------------------------------------------ F6: gaps in the lending workflow


def test_f6_shipping_lent_id_is_not_unique(db):
    for status in ("pending", "shipped"):
        db.run('INSERT INTO "Shipping" (lent_id, shipping_status) VALUES (21, %s)', (status,))  # lent 21 has one already
    assert db.scalar('SELECT count(*) FROM "Shipping" WHERE lent_id = 21') == 3


def test_f6_hand_over_does_not_create_the_shipping_row(api: Api, db):
    lent = expect(api.post("/lents", user=3, json={"book_copie_id": 1, "shipping": True}), 201)["lent_id"]
    expect(api.post(f"/lents/{lent}/accept", user=1), 200)
    expect(api.post(f"/lents/{lent}/hand-over", user=1), 200)
    assert db.scalar('SELECT count(*) FROM "Shipping" WHERE lent_id = %s', (lent,)) == 0


def test_f6_the_request_function_ignores_the_status_of_the_owner(db):
    db.run('UPDATE "Users" SET is_active = false WHERE user_id = 4')  # owner of copy 2
    assert db.scalar("SELECT count(*) FROM available_books WHERE book_copie_id = 2") == 0  # the view hides the copy
    lent_id = db.scalar("SELECT create_book_request(2, 1, false)")  # the function does not
    assert db.scalar('SELECT lent_status::text FROM "Lents" WHERE lent_id = %s', (lent_id,)) == "requested"


def test_f6_a_lent_has_no_delivery_address_and_a_request_cannot_be_cancelled(db):
    columns = {r["column_name"] for r in db.all("SELECT column_name FROM information_schema.columns WHERE table_name = 'Lents'")}
    assert not {c for c in columns if "address" in c}
    assert "cancelled" not in [r["enumlabel"] for r in db.all("SELECT enumlabel::text FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid WHERE t.typname = 'lent_status'")]


# ------------------------------------------------------------------ F7: views


def test_f7_available_books_has_no_coordinates_for_a_radius_search(db):
    columns = {r["column_name"] for r in db.all("SELECT column_name FROM information_schema.columns WHERE table_name = 'available_books'")}
    assert not columns & {"latitude", "longitude", "address_id", "collection_address_id"}


def test_f7_views_and_functions_expose_personal_data(db):
    assert "borrower_email" in {r["column_name"] for r in db.all("SELECT column_name FROM information_schema.columns WHERE table_name = 'overdue_lents'")}
    assert "user_lastname" in {r["column_name"] for r in db.all("SELECT column_name FROM information_schema.columns WHERE table_name = 'fullbookinfo'")}
    assert db.one("SELECT email FROM getFullUserStats(1)")["email"] == "alice@example.com"


def test_f7_full_book_info_does_not_filter_inactive_owners_but_available_books_does(db):
    db.run('UPDATE "Users" SET is_active = false WHERE user_id = 1')
    assert db.scalar("SELECT count(*) FROM fullBookInfo WHERE owner_id = 1") == 1
    assert db.scalar("SELECT count(*) FROM available_books WHERE owner_id = 1") == 0


# ------------------------------------------------------------------ F8: cascades


def test_f8_deleting_a_borrower_wipes_the_lent_history_but_an_owner_cannot_be_deleted(api: Api, db):
    owner_error = expected_error(db, 'DELETE FROM "Users" WHERE user_id = 3')  # owns a copy: RESTRICT through the address
    assert isinstance(owner_error, errors.ForeignKeyViolation)

    user = expect(api.post("/users", json={"username": "Leiher", "first_name": "L", "last_name": "L", "email": "l@example.com"}), 201)["user_id"]
    lent = expect(api.post("/lents", user=user, json={"book_copie_id": 2}), 201)["lent_id"]
    for action in ("accept", "hand-over", "return"):
        expect(api.post(f"/lents/{lent}/{action}", user=4), 200)
    expect(api.post(f"/lents/{lent}/review", user=user, json={"rating": 5}), 201)
    db.run('INSERT INTO "Messages" (sender_id, receiver_id, content) VALUES (%s, 1, %s)', (user, "bye"))

    db.run('DELETE FROM "Users" WHERE user_id = %s', (user,))
    assert db.scalar('SELECT count(*) FROM "Lents" WHERE lent_id = %s', (lent,)) == 0
    assert db.scalar('SELECT count(*) FROM "Reviews" WHERE lent_id = %s', (lent,)) == 0
    assert db.scalar('SELECT count(*) FROM "Messages" WHERE sender_id = %s', (user,)) == 0
    # the audit log survives, but the rows of the deleted user no longer know who acted
    orphaned = db.all(
        'SELECT protocol_type::text AS t FROM "Protocols" WHERE user_id IS NULL AND (details LIKE %s OR details = %s)',
        (f"%lent_id {lent}%", "User Leiher registered"),
    )
    assert {r["t"] for r in orphaned} == {"create_user", "create_request", "create_review"}


# ------------------------------------------------------------------ F9, F10, F11, F12, F13


def test_f9_updated_at_is_not_maintained_by_the_database(db):
    before = db.scalar('SELECT updated_at FROM "Users" WHERE user_id = 1')
    db.run('UPDATE "Users" SET first_name = %s WHERE user_id = 1', ("Neu",))
    assert db.scalar('SELECT updated_at FROM "Users" WHERE user_id = 1') == before


def test_f10_foreign_keys_without_a_supporting_index(db):
    rows = db.all(
        """
        SELECT c.conrelid::regclass::text AS tbl, a.attname::text AS col
        FROM pg_constraint c
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
        WHERE c.contype = 'f' AND c.connamespace = 'public'::regnamespace
          AND NOT EXISTS (SELECT 1 FROM pg_index i
                          WHERE i.indrelid = c.conrelid AND i.indkey[0] = c.conkey[1] AND i.indpred IS NULL)
        ORDER BY 1, 2
        """
    )
    assert [(r["tbl"], r["col"]) for r in rows] == [
        ('"CollectionAdresses"', "user_id"),
        ('"Lents"', "book_copie_id"),
        ('"Lents"', "lent_to"),
        ('"Messages"', "receiver_id"),
        ('"Messages"', "sender_id"),
        ('"Protocols"', "book_copie_id"),
        ('"Protocols"', "user_id"),
        ('"Shipping"', "lent_id"),
        ('"UserAdresses"', "user_id"),
    ]


def test_f11_the_schema_does_not_identify_authors_books_or_emails_uniquely(db):
    db.run('INSERT INTO "Authors" (name) VALUES (%s), (%s)', ("Dublette", "Dublette"))
    db.run('INSERT INTO "Books" (title) VALUES (%s), (%s)', ("Dublette", "Dublette"))
    db.run(
        'INSERT INTO "Users" (username, first_name, last_name, email, password_hash) VALUES (%s, %s, %s, %s, %s)',
        ("alice2", "A", "A", "ALICE@EXAMPLE.COM", "!"),  # alice@example.com exists
    )


def test_f12_mixed_case_function_names_are_folded_to_lower_case(db):
    assert isinstance(expected_error(db, 'SELECT * FROM "getFullUserStats"(1)'), errors.UndefinedFunction)
    assert db.scalar("SELECT count(*) FROM getFullUserStats(1)") == 1


def test_f13_the_seed_passwords_are_placeholders_so_a_login_cannot_be_tested(db):
    hashes = [r["password_hash"] for r in db.all('SELECT password_hash FROM "Users" ORDER BY user_id')]
    assert hashes[0] == "hashed_password_1"
    assert not any(h.startswith("$2") for h in hashes)  # the data dictionary says bcrypt
