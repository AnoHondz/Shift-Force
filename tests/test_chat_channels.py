from io import BytesIO

import pytest
from conftest import TEST_PASSWORD
from PIL import Image, PngImagePlugin

from app.extensions import db
from app.models import AuditLog, ChatAttachment, Role, TeamChannel, TeamMessage, User


def login(client, username="admin_user"):
    return client.post("/login", data={"username": username, "password": TEST_PASSWORD})


def picture_bytes(size=(64, 48), mode="RGB"):
    stream = BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("private-location", "Remove this original metadata")
    Image.new(mode, size, "#7562bd").save(stream, format="PNG", pnginfo=metadata)
    stream.seek(0)
    return stream


@pytest.mark.parametrize("role", Role.CHOICES)
def test_only_admin_and_executive_create_channels(client, app, users, role):
    with app.app_context():
        db.session.get(User, users["admin_id"]).role = role
        db.session.commit()
    login(client)
    response = client.post("/chat/channels", data={"name": "Shift Swaps", "topic": "Find cover"})
    assert response.status_code == (302 if role in {Role.ADMIN, Role.EXECUTIVE} else 403)
    with app.app_context():
        channel = TeamChannel.query.filter_by(name="shift-swaps").first()
        if role in {Role.ADMIN, Role.EXECUTIVE}:
            assert channel and channel.created_by_id == users["admin_id"]
            assert AuditLog.query.filter_by(action_type="chat_channel_created").count() == 1
        else:
            assert channel is None


def test_channel_validation_duplicate_and_csrf(client, app, users):
    login(client)
    for name in ["", "x" * 41, "<script>", "../../secret", "hi_crew"]:
        assert client.post("/chat/channels", data={"name": name}).status_code == 400
    assert client.post("/chat/channels", data={"name": "general"}).status_code == 409
    assert (
        client.post("/chat/channels", data={"name": "new", "topic": "x" * 161}).status_code == 400
    )
    app.config["WTF_CSRF_ENABLED"] = True
    assert client.post("/chat/channels", data={"name": "new"}).status_code == 400


def test_channel_conversations_and_history_do_not_mix(client, app, users):
    login(client)
    response = client.post("/chat/channels", data={"name": "shift-swaps"})
    channel_id = int(response.location.rsplit("/", 1)[1])
    client.post("/api/chat/messages", json={"body": "General only"})
    for index in range(30):
        client.post("/api/chat/messages", json={"channel_id": channel_id, "body": f"Cover {index}"})
    recent = client.get(f"/api/chat/messages?channel_id={channel_id}").json
    assert len(recent["messages"]) == 25 and recent["has_older"]
    before = recent["messages"][0]["id"]
    older = client.get(f"/api/chat/messages?channel_id={channel_id}&before={before}").json
    assert len(older["messages"]) == 5 and not older["has_older"]
    assert all(item["channel_id"] == channel_id for item in older["messages"])
    assert [item["body"] for item in client.get("/api/chat/messages").json["messages"]] == [
        "General only"
    ]
    assert b"General only" not in client.get(f"/chat/{channel_id}").data
    client.post("/logout")
    login(client, "employee_user")
    assert client.get(f"/chat/{channel_id}").status_code == 200
    client.post("/api/chat/messages", json={"channel_id": channel_id, "body": "I can cover"})
    assert (
        client.get(
            f"/api/chat/messages?channel_id={channel_id}&after={recent['messages'][-1]['id']}"
        ).json["messages"][0]["body"]
        == "I can cover"
    )
    assert client.get("/api/chat/messages?channel_id=999999").status_code == 404
    assert client.get("/api/chat/messages?channel_id=bad").status_code == 400
    assert client.get("/api/chat/messages?before=2&after=1").status_code == 400


def test_picture_only_message_is_normalized_protected_and_persists(client, app, users):
    login(client, "employee_user")
    response = client.post(
        "/api/chat/messages", data={"body": "", "picture": (picture_bytes(), "../../work.png")}
    )
    assert response.status_code == 201
    message = response.json["message"]
    assert message["body"] == "" and message["author"] == "Regular Employee"
    attachment = message["attachment"]
    assert "/" not in attachment["filename"] and ".." not in attachment["filename"]
    image_response = client.get(attachment["url"])
    assert image_response.status_code == 200
    assert image_response.headers["Content-Type"] == "image/jpeg"
    assert image_response.headers["X-Content-Type-Options"] == "nosniff"
    assert image_response.headers["Cache-Control"] == "no-store"
    with Image.open(BytesIO(image_response.data)) as picture:
        assert picture.size == (64, 48) and "private-location" not in picture.info
    with app.app_context():
        assert TeamMessage.query.one().user_id == users["employee_id"]
        assert ChatAttachment.query.one().data == image_response.data
    client.post("/logout")
    assert client.get(attachment["url"]).status_code == 302
    login(client)
    assert client.get(attachment["url"]).data == image_response.data
    with app.app_context():
        db.session.get(User, users["admin_id"]).employee_id = None
        db.session.commit()
    assert client.get(attachment["url"]).status_code == 403


@pytest.mark.parametrize("kind", ["fake", "svg", "too_big", "too_many_pixels"])
def test_invalid_pictures_rejected_atomically(client, app, users, kind):
    login(client)
    raw = {
        "fake": lambda: BytesIO(b"not a picture"),
        "svg": lambda: BytesIO(
            b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        ),
        "too_big": lambda: BytesIO(b"x" * (5 * 1024 * 1024 + 1)),
        "too_many_pixels": lambda: picture_bytes((3500, 3500)),
    }[kind]()
    response = client.post(
        "/api/chat/messages", data={"body": "Hello", "picture": (raw, "fake.jpg")}
    )
    assert response.status_code == 400
    with app.app_context():
        assert TeamMessage.query.count() == 0 and ChatAttachment.query.count() == 0


def test_transparency_resize_and_http_upload_limit(client, app, users):
    login(client)
    response = client.post(
        "/api/chat/messages",
        data={
            "body": "Transparent photo",
            "picture": (picture_bytes((2400, 20), "RGBA"), "photo.png"),
        },
    )
    assert response.status_code == 201
    image_response = client.get(response.json["message"]["attachment"]["url"])
    assert image_response.mimetype == "image/png"
    with Image.open(BytesIO(image_response.data)) as picture:
        assert picture.width == 2048 and picture.mode == "RGBA"
    app.config["MAX_CONTENT_LENGTH"] = 1024
    response = client.post(
        "/api/chat/messages",
        data={"body": "Too large", "picture": (BytesIO(b"x" * 2048), "photo.jpg")},
    )
    assert response.status_code == 413 and response.is_json


def test_picture_upload_requires_csrf_and_today_has_only_shift_and_schedule(client, app, users):
    login(client)
    app.config["WTF_CSRF_ENABLED"] = True
    assert (
        client.post(
            "/api/chat/messages", data={"body": "Hi", "picture": (picture_bytes(), "photo.png")}
        ).status_code
        == 400
    )
    page = client.get("/dashboard")
    assert b'aria-label="Your shift"' in page.data and b'aria-label="Your schedule"' in page.data
    assert b"chat-compose" not in page.data and b"workplace-management-link" not in page.data
    assert b"Shiftspace" in page.data
