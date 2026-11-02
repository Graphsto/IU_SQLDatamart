from conftest import Api, expect


def send(api: Api, sender: int, receiver: int, content: str = "Hallo!") -> dict:
    return expect(api.post("/messages", user=sender, json={"receiver_id": receiver, "content": content}), 201)


def protocols(api: Api, protocol_type: str, user_id: int) -> list[dict]:
    return expect(api.get(f"/protocols?protocol_type={protocol_type}&user_id={user_id}", user=1, role="moderator"), 200)


def test_send_message(api: Api):
    response = api.post("/messages", user=1, json={"receiver_id": 2, "content": "Hallo Bob, ist das Buch noch frei?"})
    body = expect(response, 201)
    assert (body["sender_id"], body["receiver_id"], body["read_status"]) == (1, 2, False)
    assert response.headers["location"] == f"/messages/{body['message_id']}"
    assert len(protocols(api, "send_message", 1)) == 1
    assert f"to user 2" in protocols(api, "send_message", 1)[0]["details"]


def test_the_sender_is_always_the_caller(api: Api):
    expect(api.post("/messages", user=1, json={"receiver_id": 2, "content": "x", "sender_id": 3}), 422)


def test_message_validation(api: Api):
    expect(api.post("/messages", user=1, json={"receiver_id": 2, "content": ""}), 422)
    expect(api.post("/messages", user=1, json={"receiver_id": 2, "content": "x" * 4001}), 422)
    expect(api.post("/messages", user=1, json={"content": "x"}), 422)
    expect(api.post("/messages", json={"receiver_id": 2, "content": "x"}), 401)


def test_message_to_yourself_is_refused_by_the_check_constraint(api: Api, db):
    before = db.scalar('SELECT count(*) FROM "Messages"')
    body = expect(api.post("/messages", user=1, json={"receiver_id": 1, "content": "Hallo ich"}), 422)
    assert body["sqlstate"] == "23514" and body["constraint"] == "message_not_self"
    assert db.scalar('SELECT count(*) FROM "Messages"') == before
    assert protocols(api, "send_message", 1) == []


def test_message_receiver_must_exist_and_be_active(api: Api, db):
    expect(api.post("/messages", user=1, json={"receiver_id": 999, "content": "x"}), 404)
    db.run('UPDATE "Users" SET is_active = false WHERE user_id = 2')
    assert "deaktiviert" in expect(api.post("/messages", user=1, json={"receiver_id": 2, "content": "x"}), 409)["detail"]


def test_list_messages_shows_only_the_own_conversation(api: Api):
    messages = expect(api.get("/messages", user=1), 200)
    assert all(1 in (m["sender_id"], m["receiver_id"]) for m in messages)
    # seed: Alice <-> Bob (the seed does not assign message IDs in list order, so compare by content)
    assert {m["content"] for m in messages} == {
        "Hi Bob, I am interested in borrowing your book.",
        "Sure, Alice! When would you like to pick it up?",
    }
    assert [m["sender_id"] for m in expect(api.get("/messages?box=inbox", user=1), 200)] == [2]
    assert [m["receiver_id"] for m in expect(api.get("/messages?box=sent", user=1), 200)] == [2]
    assert expect(api.get("/messages?with_user_id=3", user=1), 200) == []
    assert len(expect(api.get("/messages?with_user_id=2", user=1), 200)) == 2
    assert [m["sent_at"] for m in messages] == sorted((m["sent_at"] for m in messages), reverse=True)
    expect(api.get("/messages?box=trash", user=1), 422)
    expect(api.get("/messages"), 401)


def test_unread_messages(api: Api):
    unread = expect(api.get("/messages?unread_only=true", user=5), 200)  # Kimberly wrote to Olad, Olad did not read it
    assert [(m["content"], m["read_status"]) for m in unread] == [("Sorry Olad, the book is not available right now.", False)]
    assert expect(api.get("/messages?unread_only=true", user=6), 200) == []  # sent, not received


def test_mark_as_read_is_for_the_receiver_and_idempotent(api: Api):
    message = send(api, 1, 3)
    url = f"/messages/{message['message_id']}/read"
    expect(api.post(url, user=1), 403)  # the sender
    expect(api.post(url, user=2), 404)  # a stranger does not learn that it exists
    expect(api.post("/messages/9999/read", user=3), 404)
    read = expect(api.post(url, user=3), 200)
    assert read["read_status"] is True
    expect(api.post(url, user=3), 200)
    assert len(protocols(api, "read_message", 3)) == 1  # only the first transition is logged
    assert expect(api.get("/messages?unread_only=true", user=3), 200) == []
