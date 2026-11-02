from conftest import Api, expect

STAFF = {"user": 1, "role": "moderator"}


def test_reports_are_staff_only(api: Api):
    for url in (
        "/reports/monthly?year=2026&month=10",
        "/reports/monthly/json?year=2026&month=10",
        "/reports/overdue-lents",
        "/protocols",
    ):
        expect(api.get(url, user=1), 403)
        expect(api.get(url), 401)
        expect(api.get(url, user=1, role="moderator"), 200)


def test_monthly_report_function_and_json_function_agree(api: Api, db):
    # the seed is relative to the time of the database start, so take the month of the newest request
    period = db.one(
        'SELECT extract(year FROM lent_requesttimestamp)::int AS y, extract(month FROM lent_requesttimestamp)::int AS m '
        'FROM "Lents" ORDER BY lent_requesttimestamp DESC LIMIT 1'
    )
    year, month = period["y"], period["m"]
    table = expect(api.get(f"/reports/monthly?year={year}&month={month}", **STAFF), 200)
    as_json = expect(api.get(f"/reports/monthly/json?year={year}&month={month}", **STAFF), 200)
    assert table == as_json
    assert (table["year"], table["month"]) == (year, month)
    # independent oracle: count the same thing with plain SQL
    expected = db.one(
        """
        SELECT (SELECT count(*) FROM "Lents"
                WHERE extract(year FROM lent_requesttimestamp) = %(y)s AND extract(month FROM lent_requesttimestamp) = %(m)s) AS lents,
               (SELECT count(*) FROM "Messages"
                WHERE extract(year FROM sent_at) = %(y)s AND extract(month FROM sent_at) = %(m)s) AS messages,
               (SELECT count(*) FROM "Reviews"
                WHERE extract(year FROM created_at) = %(y)s AND extract(month FROM created_at) = %(m)s) AS reviews
        """,
        {"y": year, "m": month},
    )
    assert table["total_lents"] == expected["lents"] > 0
    assert table["total_messages"] == expected["messages"]
    assert table["total_reviews"] == expected["reviews"]


def test_monthly_report_for_an_old_month_counts_the_registrations(api: Api, db):
    created = db.one('SELECT extract(year FROM created_at)::int AS y, extract(month FROM created_at)::int AS m FROM "Users" ORDER BY user_id LIMIT 1')
    body = expect(api.get(f"/reports/monthly?year={created['y']}&month={created['m']}", **STAFF), 200)
    assert body["total_users"] >= 1


def test_monthly_report_validates_the_period(api: Api):
    expect(api.get("/reports/monthly?year=2026&month=13", **STAFF), 422)
    expect(api.get("/reports/monthly?year=2026&month=0", **STAFF), 422)
    expect(api.get("/reports/monthly?year=1999&month=5", **STAFF), 422)
    expect(api.get("/reports/monthly?month=5", **STAFF), 422)
    expect(api.get("/reports/monthly/json?year=abc&month=5", **STAFF), 422)


def set_active_lent_dates(db, overdue: set[int]) -> None:
    """The seed dates are relative to the start of the database; pin them so the test does not age."""
    db.run(
        "UPDATE \"Lents\" SET lent_startdate = now() - interval '10 days', lent_enddate = now() + interval '5 days' "
        "WHERE lent_status = 'active'"
    )
    for lent_id in overdue:
        db.run(
            "UPDATE \"Lents\" SET lent_startdate = now() - interval '50 days', lent_enddate = now() - interval '5 days' "
            "WHERE lent_id = %s",
            (lent_id,),
        )


def test_overdue_lents_view(api: Api, db):
    set_active_lent_dates(db, overdue={13})
    rows = expect(api.get("/reports/overdue-lents", **STAFF), 200)
    assert [(r["lent_id"], r["title"], r["borrower"], r["owner"]) for r in rows] == [(13, "1984", "Olaf", "Charlie")]
    assert rows[0]["borrower_email"] == "olaf@example.com" and rows[0]["days_overdue"] == 5


def test_a_lent_becomes_overdue_when_its_end_date_passes(api: Api, db):
    set_active_lent_dates(db, overdue={13})
    assert expect(api.get("/lents/17/overdue", user=1), 200)["must_be_returned"] is False
    db.run("UPDATE \"Lents\" SET lent_enddate = now() - interval '3 days' WHERE lent_id = 17")
    rows = expect(api.get("/reports/overdue-lents", **STAFF), 200)
    assert [(r["lent_id"], r["days_overdue"]) for r in rows] == [(13, 5), (17, 3)]  # most overdue first
    assert expect(api.get("/lents/17/overdue", user=1), 200)["must_be_returned"] is True


def test_protocol_listing_and_filters(api: Api):
    everything = expect(api.get("/protocols?limit=200", **STAFF), 200)
    assert len(everything) > 50
    assert [p["protocol_id"] for p in everything] == sorted((p["protocol_id"] for p in everything), reverse=True)
    created = expect(api.get("/protocols?protocol_type=create_user&limit=200", **STAFF), 200)
    assert len(created) == 10 and {p["protocol_type"] for p in created} == {"create_user"}
    assert {p["user_id"] for p in expect(api.get("/protocols?user_id=4&limit=200", **STAFF), 200)} == {4}
    assert all(p["book_copie_id"] == 1 for p in expect(api.get("/protocols?book_copie_id=1&limit=200", **STAFF), 200))
    assert expect(api.get("/protocols?since=2999-01-01T00:00:00Z", **STAFF), 200) == []
    assert len(expect(api.get("/protocols?until=2999-01-01T00:00:00Z&limit=200", **STAFF), 200)) == len(everything)
    expect(api.get("/protocols?protocol_type=nothing", **STAFF), 422)
    expect(api.get("/protocols?since=yesterday", **STAFF), 422)


def test_protocol_pagination(api: Api):
    first = expect(api.get("/protocols?limit=3", **STAFF), 200)
    second = expect(api.get("/protocols?limit=3&offset=3", **STAFF), 200)
    assert len(first) == len(second) == 3
    assert not {p["protocol_id"] for p in first} & {p["protocol_id"] for p in second}
