"""Visible-UI live ingestion for WeChat 4.x when WeFlow is unavailable.

This adapter does not inspect or decrypt WeChat databases. It uses wx4py's
UI Automation listener, whose Qt accessibility surface exposes message text
but not reliable sender identities. Consequently sender fields remain NULL.
"""
from __future__ import annotations

import hashlib
import re
import time
from collections import Counter
from datetime import datetime, timedelta

from loguru import logger

from ..config import settings
from ..db import get_conn, init_db, transaction
from ..models import Message, MsgType
from .group_identity import canonical_group_id, ui_group_id
from .ui_identity import (
    load_ui_roster,
    recognize_control_sender,
    save_ui_roster,
    stable_ui_member_id,
)
from .writer import write_messages


def _history_timestamp(label: str, now: datetime) -> int:
    text = (label or "").strip()
    match = re.search(r"(\d{1,2}):(\d{2})", text)
    hour, minute = (int(match.group(1)), int(match.group(2))) if match else (0, 0)
    if text.startswith("昨天"):
        date_value = (now - timedelta(days=1)).date()
    else:
        date_match = re.search(r"(\d{1,2})月(\d{1,2})日", text)
        if date_match:
            year = now.year
            month, day = int(date_match.group(1)), int(date_match.group(2))
            if month > now.month + 1:
                year -= 1
            date_value = datetime(year, month, day).date()
        else:
            date_value = now.date()
    return int(datetime.combine(date_value, datetime.min.time()).replace(hour=hour, minute=minute).timestamp())


def _backfill_visible_history(wx, groups: list[str]) -> None:
    now = datetime.now()
    with get_conn() as conn:
        for group in groups:
            group_id = canonical_group_id(conn, group)
            occurrences: Counter[tuple[str, str]] = Counter()
            messages: list[Message] = []
            # Separate calls preserve wx4py's exact today/yesterday filters.
            for since in ("yesterday", "today"):
                rows = wx.chat_window.get_chat_history(
                    group, target_type="group", since=since, max_count=5000
                )
                for row in rows:
                    content = str(row.get("content") or "").strip()
                    if not content:
                        continue
                    label = str(row.get("time") or "")
                    key = (label, content)
                    occurrence = occurrences[key]
                    occurrences[key] += 1
                    stable = hashlib.sha256(
                        f"{group}\0{label}\0{content}\0{occurrence}".encode("utf-8")
                    ).hexdigest()[:24]
                    msg_type = MsgType.LINK if row.get("type") == "link" else MsgType.TEXT
                    messages.append(Message(
                        wx_msg_id=f"ui-history:{stable}",
                        group_id=group_id,
                        group_name=group,
                        t=_history_timestamp(label, now),
                        type=msg_type,
                        content_text=content,
                        source="backfill",
                    ))
            write_messages(conn, messages)
            logger.info("ui ingest: backfilled {} visible rows for {!r}", len(messages), group)


def _sync_visible_rosters(wx, groups: list[str]) -> dict[str, list[str]]:
    rosters: dict[str, list[str]] = {}
    for group in groups:
        try:
            members = wx.group_manager.get_group_members(group)
            with get_conn() as conn:
                group_id = canonical_group_id(conn, group)
                with transaction(conn):
                    names = save_ui_roster(conn, group_id, members)
            rosters[group] = names
            logger.info("ui ingest: roster synced for {!r}: {} member(s)", group, len(names))
        except Exception as exc:
            logger.warning("ui ingest: roster sync failed for {!r}: {}", group, exc)
            with get_conn() as conn:
                group_id = canonical_group_id(conn, group)
                rosters[group] = load_ui_roster(conn, group_id)
    return rosters


def run_ui_live() -> None:
    if not settings.groups:
        raise RuntimeError("WO_GROUPS must contain exact group display names for wx4py ingest")
    init_db()
    try:
        from wx4py import WeChatClient
        from wx4py.features.messaging.listener import WeChatGroupListener
    except ImportError as exc:
        raise RuntimeError("wx4py is required for WO_INGEST_BACKEND=wx4py") from exc

    wx = WeChatClient()
    logger.info("ui ingest: connecting to visible WeChat")
    wx.connect()
    try:
        rosters = _sync_visible_rosters(wx, settings.groups) if settings.ui_sender_identity_enabled else {}
        # The raw watcher already provides complete history and stable sender
        # identity. Re-reading up to 5000 UI rows here is redundant and can
        # block startup indefinitely in a busy group, preventing the listener
        # from ever reaching new @mentions.
        if settings.raw_wechat_enabled:
            logger.info("ui ingest: raw sync enabled; skipping visible-history backfill")
        else:
            _backfill_visible_history(wx, settings.groups)

        def on_message(event) -> None:
            content = str(event.content or "").strip()
            if not content:
                return None
            try:
                runtime_id = tuple(event.raw.GetRuntimeId() or ()) if event.raw else ()
            except Exception:
                runtime_id = ()
            stamp_ms = int(float(event.timestamp) * 1000)
            event_key = hashlib.sha256(
                f"{event.group}\0{stamp_ms}\0{runtime_id}\0{content}".encode("utf-8")
            ).hexdigest()[:24]
            class_name = str(getattr(event.raw, "ClassName", "") or "")
            msg_type = MsgType.TEXT if "Text" in class_name else MsgType.LINK
            with get_conn() as conn:
                group_id = canonical_group_id(conn, event.group)
                sender_display = None
                sender_wxid = None
                if settings.ui_sender_identity_enabled:
                    sender_display, confidence = recognize_control_sender(
                        event.raw,
                        rosters.get(event.group, []),
                    )
                    if sender_display:
                        sender_wxid = stable_ui_member_id(group_id, sender_display)
                        logger.info(
                            "ui ingest: sender identified for {!r}: {!r} ({:.2f})",
                            event.group,
                            sender_display,
                            confidence,
                        )
                    else:
                        logger.warning(
                            "ui ingest: sender unresolved for {!r}; message remains anonymous",
                            event.group,
                        )
                message = Message(
                    wx_msg_id=f"ui-live:{event_key}",
                    group_id=group_id,
                    group_name=event.group,
                    sender_wxid=sender_wxid,
                    sender_display=sender_display,
                    t=int(event.timestamp),
                    type=msg_type,
                    content_text=content,
                    source="live",
                )
                write_messages(conn, [message])
            return None

        listener = WeChatGroupListener(
            wx,
            settings.groups,
            on_message,
            auto_reply=False,
            ignore_client_sent=True,
            reply_on_at=False,
        )
        logger.info("ui ingest: listening to exact groups {}", settings.groups)
        listener.start(block=True)
    finally:
        try:
            wx.disconnect()
        except Exception:
            logger.warning("ui ingest: wx4py disconnect failed")


def probe_ui_group(group: str) -> dict[str, object]:
    """Read-only compatibility probe; opens one exact group and sends nothing."""
    try:
        from wx4py import WeChatClient
    except ImportError as exc:
        raise RuntimeError("wx4py is required for the UI probe") from exc
    wx = WeChatClient()
    wx.connect()
    try:
        nickname = wx.group_manager.get_group_nickname(group)
        rows = wx.chat_window.get_chat_history(
            group, target_type="group", since="today", max_count=50
        )
        return {
            "group": group,
            "synthetic_group_id": ui_group_id(group),
            "bot_group_nickname": nickname or "",
            "visible_today_rows": len(rows),
        }
    finally:
        wx.disconnect()
