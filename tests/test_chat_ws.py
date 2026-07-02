"""Tests for chat WebSocket server: rooms, messaging, presence, moderation integration."""

import asyncio
import json
import sqlite3
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

from chat_db import (
    init_chat_db,
    create_user,
    create_session,
    create_room,
    get_room_messages,
    get_strike_count,
    is_banned,
    create_message,
    purge_expired_messages,
)
from chat_moderation import moderate_message
from chat_ws import ConnectionManager, handle_chat_ws, manager as global_manager


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_chat_db(conn)
    yield conn
    conn.close()


@pytest.fixture
def event_id():
    return "test-event"


@pytest.fixture
def user1(db):
    return create_user(db, "google", "g-1", "Alice", "fp-1")


@pytest.fixture
def user2(db):
    return create_user(db, "apple", "a-2", "Bob", "fp-2")


@pytest.fixture
def session1(db, user1):
    return create_session(db, user1["id"])


@pytest.fixture
def session2(db, user2):
    return create_session(db, user2["id"])


@pytest.fixture
def stage_room(db, event_id):
    return create_room(db, "grand-hall", event_id, "stage", "Grand Hall")


@pytest.fixture
def mgr():
    return ConnectionManager()


class FakeWebSocket:
    def __init__(self):
        self.sent: list[str] = []
        self.to_receive: list[str] = []
        self.accepted = False
        self.closed = False
        self.close_code = None
        self._recv_index = 0

    async def accept(self):
        self.accepted = True

    async def send_text(self, data: str):
        self.sent.append(data)

    async def receive_text(self) -> str:
        if self._recv_index < len(self.to_receive):
            msg = self.to_receive[self._recv_index]
            self._recv_index += 1
            return msg
        raise Exception("WebSocketDisconnect")

    async def close(self, code: int = 1000, reason: str = ""):
        self.closed = True
        self.close_code = code

    def get_events(self) -> list[dict]:
        return [json.loads(s) for s in self.sent]

    def get_events_by_type(self, event_type: str) -> list[dict]:
        return [e for e in self.get_events() if e.get("event") == event_type]


# --- ConnectionManager ---


class TestConnectionManager:
    @pytest.mark.asyncio
    async def test_connect_and_join(self, mgr):
        ws = FakeWebSocket()
        await mgr.connect(ws, "user-1", "c1")
        await mgr.join_room("room-1", "user-1", "c1", "Alice")
        online = mgr.get_online_users("room-1")
        assert len(online) == 1
        assert online[0]["display_name"] == "Alice"

    @pytest.mark.asyncio
    async def test_disconnect_leaves_rooms(self, mgr):
        ws = FakeWebSocket()
        await mgr.connect(ws, "user-1", "c1")
        await mgr.join_room("room-1", "user-1", "c1", "Alice")
        _, left = mgr.disconnect("c1")
        assert "room-1" in left
        assert mgr.get_online_users("room-1") == []

    @pytest.mark.asyncio
    async def test_broadcast_to_room(self, mgr):
        ws1 = FakeWebSocket()
        ws2 = FakeWebSocket()
        await mgr.connect(ws1, "u1", "c1")
        await mgr.connect(ws2, "u2", "c2")
        await mgr.join_room("r1", "u1", "c1", "A")
        await mgr.join_room("r1", "u2", "c2", "B")
        await mgr.broadcast_to_room("r1", {"event": "test", "data": "hello"})
        assert len(ws1.sent) >= 1
        assert len(ws2.sent) >= 1

    @pytest.mark.asyncio
    async def test_broadcast_excludes_sender(self, mgr):
        ws1 = FakeWebSocket()
        ws2 = FakeWebSocket()
        await mgr.connect(ws1, "u1", "c1")
        await mgr.connect(ws2, "u2", "c2")
        await mgr.join_room("r1", "u1", "c1", "A")
        await mgr.join_room("r1", "u2", "c2", "B")
        presence_count = len(ws1.sent)
        await mgr.broadcast_to_room("r1", {"event": "test"}, exclude_conn="c1")
        assert len(ws1.sent) == presence_count
        assert len(ws2.get_events_by_type("test")) == 1

    @pytest.mark.asyncio
    async def test_send_to_user(self, mgr):
        ws = FakeWebSocket()
        await mgr.connect(ws, "u1", "c1")
        await mgr.send_to_user("u1", {"event": "hello"})
        assert ws.get_events_by_type("hello")

    @pytest.mark.asyncio
    async def test_rate_limit(self, mgr):
        for _ in range(5):
            assert mgr.check_rate_limit("u1", max_msgs=5, window_secs=10)
        assert not mgr.check_rate_limit("u1", max_msgs=5, window_secs=10)

    @pytest.mark.asyncio
    async def test_leave_room(self, mgr):
        ws = FakeWebSocket()
        await mgr.connect(ws, "u1", "c1")
        await mgr.join_room("r1", "u1", "c1", "A")
        await mgr.leave_room("r1", "c1")
        assert mgr.get_online_users("r1") == []

    @pytest.mark.asyncio
    async def test_multiple_rooms(self, mgr):
        ws = FakeWebSocket()
        await mgr.connect(ws, "u1", "c1")
        await mgr.join_room("r1", "u1", "c1", "A")
        await mgr.join_room("r2", "u1", "c1", "A")
        assert len(mgr.get_online_users("r1")) == 1
        assert len(mgr.get_online_users("r2")) == 1
        _, left = mgr.disconnect("c1")
        assert "r1" in left and "r2" in left


# --- Message Flow ---


class TestMessageFlow:
    @pytest.mark.asyncio
    async def test_send_and_receive(
        self, db, user1, user2, session1, session2, stage_room, event_id
    ):
        ws1 = FakeWebSocket()
        ws2 = FakeWebSocket()

        mgr = ConnectionManager()
        await mgr.connect(ws1, user1["id"], "c1")
        await mgr.connect(ws2, user2["id"], "c2")
        await mgr.join_room("grand-hall", user1["id"], "c1", "Alice")
        await mgr.join_room("grand-hall", user2["id"], "c2", "Bob")

        content = json.dumps({"text": "hello everyone"})
        msg = create_message(db, "grand-hall", user1["id"], "text", content)

        await mgr.broadcast_to_room(
            "grand-hall",
            {
                "event": "message",
                "id": msg["id"],
                "room_id": "grand-hall",
                "user_id": user1["id"],
                "display_name": "Alice",
                "type": "text",
                "content": content,
                "created_at": msg["created_at"],
            },
        )

        msgs1 = ws1.get_events_by_type("message")
        msgs2 = ws2.get_events_by_type("message")
        assert len(msgs1) == 1
        assert len(msgs2) == 1
        assert msgs2[0]["display_name"] == "Alice"
        assert json.loads(msgs2[0]["content"])["text"] == "hello everyone"

    @pytest.mark.asyncio
    async def test_message_stored_in_db(self, db, user1, stage_room):
        content = json.dumps({"text": "persisted"})
        create_message(db, "grand-hall", user1["id"], "text", content)
        messages = get_room_messages(db, "grand-hall")
        assert len(messages) == 1
        assert messages[0]["content"] == content

    @pytest.mark.asyncio
    async def test_typing_indicator(self, db):
        mgr = ConnectionManager()
        ws1 = FakeWebSocket()
        ws2 = FakeWebSocket()
        await mgr.connect(ws1, "u1", "c1")
        await mgr.connect(ws2, "u2", "c2")
        await mgr.join_room("r1", "u1", "c1", "A")
        await mgr.join_room("r1", "u2", "c2", "B")

        await mgr.broadcast_to_room(
            "r1",
            {
                "event": "typing",
                "room_id": "r1",
                "user_id": "u1",
                "active": True,
            },
            exclude_conn="c1",
        )

        typing_events = ws2.get_events_by_type("typing")
        assert len(typing_events) == 1
        assert typing_events[0]["active"] is True
        assert not ws1.get_events_by_type("typing")


# --- Presence ---


class TestPresence:
    @pytest.mark.asyncio
    async def test_join_broadcasts_presence(self, db):
        mgr = ConnectionManager()
        ws1 = FakeWebSocket()
        ws2 = FakeWebSocket()
        await mgr.connect(ws1, "u1", "c1")
        await mgr.connect(ws2, "u2", "c2")
        await mgr.join_room("r1", "u1", "c1", "A")
        await mgr.join_room("r1", "u2", "c2", "B")

        presence = ws1.get_events_by_type("presence")
        assert len(presence) == 1
        assert presence[0]["user_id"] == "u2"
        assert presence[0]["online"] is True

    @pytest.mark.asyncio
    async def test_leave_broadcasts_offline(self, db):
        mgr = ConnectionManager()
        ws1 = FakeWebSocket()
        ws2 = FakeWebSocket()
        await mgr.connect(ws1, "u1", "c1")
        await mgr.connect(ws2, "u2", "c2")
        await mgr.join_room("r1", "u1", "c1", "A")
        await mgr.join_room("r1", "u2", "c2", "B")

        await mgr.leave_room("r1", "c2")

        offline = [e for e in ws1.get_events_by_type("presence") if not e["online"]]
        assert len(offline) == 1
        assert offline[0]["user_id"] == "u2"


# --- Rate Limiting ---


class TestRateLimiting:
    @pytest.mark.asyncio
    async def test_rate_limit_resets(self):
        mgr = ConnectionManager()
        for _ in range(5):
            mgr.check_rate_limit("u1", max_msgs=5, window_secs=0.01)
        await asyncio.sleep(0.02)
        assert mgr.check_rate_limit("u1", max_msgs=5, window_secs=0.01)


# --- Moderation in flow ---


class TestModerationInFlow:
    @pytest.mark.asyncio
    async def test_blocked_message_not_broadcast(self, db, user1, stage_room):
        mgr = ConnectionManager()
        ws1 = FakeWebSocket()
        ws2 = FakeWebSocket()
        await mgr.connect(ws1, user1["id"], "c1")
        u2 = create_user(db, "apple", "a-2", "Bob", "fp-2")
        await mgr.connect(ws2, u2["id"], "c2")
        await mgr.join_room("grand-hall", user1["id"], "c1", "Alice")
        await mgr.join_room("grand-hall", u2["id"], "c2", "Bob")

        with patch(
            "chat_moderation.check_openai_moderation",
            new_callable=AsyncMock,
            return_value=None,
        ):
            mod = await moderate_message(db, user1["id"], "got molly?")
            assert not mod["allowed"]

        assert get_room_messages(db, "grand-hall") == []

    @pytest.mark.asyncio
    async def test_clean_message_stored_and_broadcast(self, db, user1, stage_room):
        mgr = ConnectionManager()
        ws = FakeWebSocket()
        await mgr.connect(ws, user1["id"], "c1")
        await mgr.join_room("grand-hall", user1["id"], "c1", "Alice")

        with patch(
            "chat_moderation.check_openai_moderation",
            new_callable=AsyncMock,
            return_value=None,
        ):
            mod = await moderate_message(db, user1["id"], "great set!")
            assert mod["allowed"]

        content = json.dumps({"text": "great set!"})
        msg = create_message(db, "grand-hall", user1["id"], "text", content)
        await mgr.broadcast_to_room(
            "grand-hall",
            {
                "event": "message",
                "id": msg["id"],
                "room_id": "grand-hall",
                "user_id": user1["id"],
                "display_name": "Alice",
                "type": "text",
                "content": content,
                "created_at": msg["created_at"],
            },
        )

        assert len(get_room_messages(db, "grand-hall")) == 1
        assert ws.get_events_by_type("message")


# --- Purge Notifications ---


class TestPurgeNotifications:
    @pytest.mark.asyncio
    async def test_expired_messages_notified(self, db, user1, stage_room):
        mgr = ConnectionManager()
        ws = FakeWebSocket()
        await mgr.connect(ws, user1["id"], "c1")
        await mgr.join_room("grand-hall", user1["id"], "c1", "Alice")

        msg = create_message(
            db, "grand-hall", user1["id"], "text", '{"text":"bye"}', ttl_minutes=0
        )
        expired = purge_expired_messages(db)

        for batch in expired:
            await mgr.broadcast_to_room(
                batch["room_id"],
                {
                    "event": "messages_expired",
                    "room_id": batch["room_id"],
                    "message_ids": batch["message_ids"],
                },
            )

        expire_events = ws.get_events_by_type("messages_expired")
        assert len(expire_events) == 1
        assert msg["id"] in expire_events[0]["message_ids"]
