"""Chat WebSocket server: rooms, messaging, presence, typing indicators."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import WebSocket, WebSocketDisconnect

from chat_db import (
    get_chat_db,
    get_user_by_token,
    get_room,
    create_message,
    get_room_messages,
    create_meetup,
    join_meetup,
    leave_meetup,
    get_meetup_attendees,
    find_or_create_dm,
    block_user,
    unblock_user,
    is_blocked,
    create_report,
    update_last_seen,
    purge_expired_messages,
    purge_expired_meetups,
    purge_expired_sessions,
)
from chat_moderation import moderate_message

logger = logging.getLogger(__name__)


class ChatRoom:
    def __init__(self):
        self.connections: dict[str, WebSocket] = {}
        self.user_names: dict[str, str] = {}

    async def broadcast(self, event: dict, exclude: str | None = None) -> None:
        payload = json.dumps(event)
        disconnected = []
        for user_id, ws in self.connections.items():
            if user_id == exclude:
                continue
            try:
                await ws.send_text(payload)
            except Exception:
                disconnected.append(user_id)
        for uid in disconnected:
            self.connections.pop(uid, None)
            self.user_names.pop(uid, None)

    async def send_to(self, user_id: str, event: dict) -> None:
        ws = self.connections.get(user_id)
        if ws:
            try:
                await ws.send_text(json.dumps(event))
            except Exception:
                self.connections.pop(user_id, None)
                self.user_names.pop(user_id, None)


class ConnectionManager:
    def __init__(self):
        self.rooms: dict[str, ChatRoom] = {}
        self.user_rooms: dict[str, set[str]] = {}
        self.user_ws: dict[str, WebSocket] = {}
        self._rate_buckets: dict[str, list[float]] = {}

    def _get_room(self, room_id: str) -> ChatRoom:
        if room_id not in self.rooms:
            self.rooms[room_id] = ChatRoom()
        return self.rooms[room_id]

    async def connect(self, ws: WebSocket, user_id: str) -> None:
        self.user_ws[user_id] = ws
        self.user_rooms.setdefault(user_id, set())

    def disconnect(self, user_id: str) -> set[str]:
        rooms = self.user_rooms.pop(user_id, set())
        self.user_ws.pop(user_id, None)
        for room_id in rooms:
            room = self.rooms.get(room_id)
            if room:
                room.connections.pop(user_id, None)
                room.user_names.pop(user_id, None)
        return rooms

    async def join_room(self, room_id: str, user_id: str, display_name: str) -> None:
        room = self._get_room(room_id)
        room.connections[user_id] = self.user_ws[user_id]
        room.user_names[user_id] = display_name
        self.user_rooms.setdefault(user_id, set()).add(room_id)
        await room.broadcast(
            {
                "event": "presence",
                "room_id": room_id,
                "user_id": user_id,
                "online": True,
            },
            exclude=user_id,
        )

    async def leave_room(self, room_id: str, user_id: str) -> None:
        room = self.rooms.get(room_id)
        if room:
            room.connections.pop(user_id, None)
            room.user_names.pop(user_id, None)
            await room.broadcast(
                {
                    "event": "presence",
                    "room_id": room_id,
                    "user_id": user_id,
                    "online": False,
                },
            )
        rooms = self.user_rooms.get(user_id)
        if rooms:
            rooms.discard(room_id)

    async def broadcast_to_room(
        self, room_id: str, event: dict, exclude: str | None = None
    ) -> None:
        room = self.rooms.get(room_id)
        if room:
            await room.broadcast(event, exclude=exclude)

    async def send_to_user(self, user_id: str, event: dict) -> None:
        ws = self.user_ws.get(user_id)
        if ws:
            try:
                await ws.send_text(json.dumps(event))
            except Exception:
                pass

    def get_online_users(self, room_id: str) -> list[dict]:
        room = self.rooms.get(room_id)
        if not room:
            return []
        return [
            {"user_id": uid, "display_name": name}
            for uid, name in room.user_names.items()
        ]

    def check_rate_limit(
        self, user_id: str, max_msgs: int = 5, window_secs: int = 10
    ) -> bool:
        now = time.monotonic()
        bucket = self._rate_buckets.setdefault(user_id, [])
        bucket[:] = [t for t in bucket if now - t < window_secs]
        if len(bucket) >= max_msgs:
            return False
        bucket.append(now)
        return True


manager = ConnectionManager()


async def handle_chat_ws(ws: WebSocket, token: str, event_id: str) -> None:
    db = get_chat_db()
    user = get_user_by_token(db, token)
    if not user:
        await ws.close(code=4001, reason="Invalid session")
        return

    await ws.accept()
    user_id = user["id"]
    display_name = user["display_name"]
    await manager.connect(ws, user_id)

    try:
        while True:
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            event = data.get("event")
            if not event:
                continue

            if event == "join_room":
                room_id = data.get("room_id")
                if room_id and get_room(db, room_id):
                    await manager.join_room(room_id, user_id, display_name)
                    messages = get_room_messages(db, room_id, limit=50)
                    await manager.send_to_user(
                        user_id,
                        {
                            "event": "room_history",
                            "room_id": room_id,
                            "messages": [
                                {
                                    "id": m["id"],
                                    "room_id": m["room_id"],
                                    "user_id": m["user_id"],
                                    "display_name": m["display_name"],
                                    "type": m["type"],
                                    "content": m["content"],
                                    "created_at": m["created_at"],
                                }
                                for m in reversed(messages)
                            ],
                            "online": manager.get_online_users(room_id),
                        },
                    )

            elif event == "leave_room":
                room_id = data.get("room_id")
                if room_id:
                    await manager.leave_room(room_id, user_id)

            elif event == "send_message":
                room_id = data.get("room_id")
                msg_type = data.get("type", "text")
                content = data.get("content", "")

                if not room_id or not content:
                    continue

                if not manager.check_rate_limit(user_id):
                    await manager.send_to_user(
                        user_id,
                        {
                            "event": "message_rejected",
                            "reason": "Slow down — too many messages.",
                        },
                    )
                    continue

                text_for_moderation = ""
                if msg_type == "text":
                    try:
                        text_for_moderation = json.loads(content).get("text", "")
                    except (json.JSONDecodeError, AttributeError):
                        text_for_moderation = content

                image_url = None
                if msg_type == "image":
                    try:
                        image_url = json.loads(content).get("url")
                    except (json.JSONDecodeError, AttributeError):
                        pass

                mod_result = await moderate_message(
                    db, user_id, text_for_moderation, image_url
                )

                if not mod_result["allowed"]:
                    response = {
                        "event": "message_rejected",
                        "reason": mod_result["reason"],
                    }
                    if mod_result["action"] == "ban":
                        response["event"] = "banned"
                    elif mod_result.get("strike_count"):
                        response["event"] = "strike"
                        response["count"] = mod_result["strike_count"]
                    await manager.send_to_user(user_id, response)
                    if mod_result["action"] == "ban":
                        await ws.close(code=4003, reason="Banned")
                        return
                    continue

                msg = create_message(db, room_id, user_id, msg_type, content)
                await manager.broadcast_to_room(
                    room_id,
                    {
                        "event": "message",
                        "id": msg["id"],
                        "room_id": room_id,
                        "user_id": user_id,
                        "display_name": display_name,
                        "type": msg_type,
                        "content": content,
                        "created_at": msg["created_at"],
                    },
                )

            elif event == "typing":
                room_id = data.get("room_id")
                active = data.get("active", False)
                if room_id:
                    await manager.broadcast_to_room(
                        room_id,
                        {
                            "event": "typing",
                            "room_id": room_id,
                            "user_id": user_id,
                            "active": active,
                        },
                        exclude=user_id,
                    )

            elif event == "create_meetup":
                stage_id = data.get("stage_id")
                title = data.get("title")
                meetup_time = data.get("meetup_time")
                if not title or not meetup_time:
                    continue

                meetup = create_meetup(
                    db,
                    user_id,
                    event_id,
                    stage_id,
                    title,
                    meetup_time,
                    location_lat=data.get("lat"),
                    location_lng=data.get("lng"),
                    location_label=data.get("label"),
                    note=data.get("note"),
                )

                if stage_id:
                    invite_content = json.dumps(
                        {
                            "meetup_id": meetup["id"],
                            "title": title,
                            "meetup_time": meetup_time,
                        }
                    )
                    invite_msg = create_message(
                        db, stage_id, user_id, "meetup_invite", invite_content
                    )
                    await manager.broadcast_to_room(
                        stage_id,
                        {
                            "event": "message",
                            "id": invite_msg["id"],
                            "room_id": stage_id,
                            "user_id": user_id,
                            "display_name": display_name,
                            "type": "meetup_invite",
                            "content": invite_content,
                            "created_at": invite_msg["created_at"],
                        },
                    )

                await manager.broadcast_to_room(
                    stage_id or f"{event_id}:general",
                    {
                        "event": "meetup_created",
                        "meetup": meetup,
                    },
                )

            elif event == "join_meetup":
                meetup_id = data.get("meetup_id")
                if meetup_id:
                    join_meetup(db, meetup_id, user_id)
                    attendees = [
                        {"id": a["id"], "display_name": a["display_name"]}
                        for a in get_meetup_attendees(db, meetup_id)
                    ]
                    await manager.broadcast_to_room(
                        meetup_id,
                        {
                            "event": "meetup_updated",
                            "meetup_id": meetup_id,
                            "attendees": attendees,
                        },
                    )

            elif event == "leave_meetup":
                meetup_id = data.get("meetup_id")
                if meetup_id:
                    leave_meetup(db, meetup_id, user_id)
                    attendees = [
                        {"id": a["id"], "display_name": a["display_name"]}
                        for a in get_meetup_attendees(db, meetup_id)
                    ]
                    await manager.broadcast_to_room(
                        meetup_id,
                        {
                            "event": "meetup_updated",
                            "meetup_id": meetup_id,
                            "attendees": attendees,
                        },
                    )

            elif event == "open_dm":
                target_user_id = data.get("target_user_id")
                if not target_user_id:
                    continue
                if is_blocked(db, target_user_id, user_id):
                    await manager.send_to_user(
                        user_id,
                        {
                            "event": "message_rejected",
                            "reason": "Cannot message this user.",
                        },
                    )
                    continue
                try:
                    room_id = find_or_create_dm(db, event_id, user_id, target_user_id)
                    await manager.send_to_user(
                        user_id,
                        {
                            "event": "dm_opened",
                            "room_id": room_id,
                        },
                    )
                except ValueError:
                    pass

            elif event == "block_user":
                target = data.get("target_user_id")
                if target:
                    block_user(db, user_id, target)

            elif event == "unblock_user":
                target = data.get("target_user_id")
                if target:
                    unblock_user(db, user_id, target)

            elif event == "report_message":
                message_id = data.get("message_id")
                reason = data.get("reason")
                if not message_id or not reason:
                    continue
                msg_row = db.execute(
                    "SELECT * FROM messages WHERE id = ?", (message_id,)
                ).fetchone()
                if msg_row:
                    create_report(
                        db,
                        user_id,
                        msg_row["user_id"],
                        msg_row["content"],
                        msg_row["room_id"],
                        reason,
                    )
                    await manager.send_to_user(
                        user_id,
                        {
                            "event": "report_confirmed",
                            "message_id": message_id,
                        },
                    )

    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Chat WebSocket error for user %s", user_id)
    finally:
        left_rooms = manager.disconnect(user_id)
        for room_id in left_rooms:
            await manager.broadcast_to_room(
                room_id,
                {
                    "event": "presence",
                    "room_id": room_id,
                    "user_id": user_id,
                    "online": False,
                },
            )
        update_last_seen(db, user_id)


async def purge_loop() -> None:
    while True:
        await asyncio.sleep(30)
        try:
            db = get_chat_db()
            expired_msgs = purge_expired_messages(db)
            for batch in expired_msgs:
                await manager.broadcast_to_room(
                    batch["room_id"],
                    {
                        "event": "messages_expired",
                        "room_id": batch["room_id"],
                        "message_ids": batch["message_ids"],
                    },
                )

            expired_meetups = purge_expired_meetups(db)
            for meetup_id in expired_meetups:
                await manager.broadcast_to_room(
                    meetup_id,
                    {
                        "event": "meetup_expired",
                        "meetup_id": meetup_id,
                    },
                )

            purge_expired_sessions(db)
        except Exception:
            logger.exception("Purge loop error")
