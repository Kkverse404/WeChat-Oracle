import time
from pathlib import Path

from wechat_oracle.config import settings
from wechat_oracle.db import get_conn, init_db
from wechat_oracle.dispatcher import _next_unprocessed
from wechat_oracle.ingest.writer import write_messages
from wechat_oracle.models import Message, MsgType
from wechat_oracle.raw_wechat.cli import _is_fresh_inbound_mention


def _message(
    *,
    source: str = "backfill",
    sender: str | None = "wxid_friend",
    text: str = "@Chris. hello",
    timestamp: int = 1000,
    wx_msg_id: str = "raw-1",
) -> Message:
    return Message(
        wx_msg_id=wx_msg_id,
        group_id="123@chatroom",
        group_name="test-group",
        sender_wxid=sender,
        sender_display="member" if sender else None,
        t=timestamp,
        type=MsgType.TEXT,
        content_text=text,
        source=source,
    )


def test_raw_reply_gate_accepts_only_fresh_inbound_exact_mentions(monkeypatch) -> None:
    monkeypatch.setattr(settings, "raw_wechat_reply_fallback_enabled", True)
    monkeypatch.setattr(settings, "raw_wechat_reply_fallback_max_age_seconds", 300)
    monkeypatch.setattr(settings, "bot_name", "Chris.")
    monkeypatch.setattr(settings, "bot_wxid", "wxid_bot")

    assert _is_fresh_inbound_mention(_message(), 1100)
    assert not _is_fresh_inbound_mention(_message(sender=None), 1100)
    assert not _is_fresh_inbound_mention(_message(sender="wxid_bot"), 1100)
    assert not _is_fresh_inbound_mention(_message(text="Chris. hello"), 1100)
    assert not _is_fresh_inbound_mention(_message(timestamp=700), 1100)


def test_fresh_raw_candidate_enters_dispatcher_without_changing_source(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "raw-fallback.db"
    init_db(db_path)
    now = time.time()
    message = _message(timestamp=int(now))
    with get_conn(db_path) as conn:
        assert write_messages(conn, [message]) == (1, 1)
        msg_id = conn.execute("SELECT msg_id FROM messages").fetchone()["msg_id"]
        conn.execute(
            "INSERT INTO raw_reply_candidates(msg_id,discovered_at,expires_at,reason) "
            "VALUES(?,?,?,'exact_mention')",
            (msg_id, now, now + 60),
        )

        rows = _next_unprocessed(conn, "Chris.", bot_wxid="wxid_bot")

        assert [row["msg_id"] for row in rows] == [msg_id]
        assert conn.execute("SELECT source FROM messages").fetchone()["source"] == "backfill"


def test_expired_raw_candidate_does_not_enter_dispatcher(tmp_path: Path) -> None:
    db_path = tmp_path / "expired.db"
    init_db(db_path)
    now = time.time()
    with get_conn(db_path) as conn:
        assert write_messages(conn, [_message(timestamp=int(now))]) == (1, 1)
        msg_id = conn.execute("SELECT msg_id FROM messages").fetchone()["msg_id"]
        conn.execute(
            "INSERT INTO raw_reply_candidates(msg_id,discovered_at,expires_at,reason) "
            "VALUES(?,?,?,'exact_mention')",
            (msg_id, now - 120, now - 1),
        )
        assert _next_unprocessed(conn, "Chris.", bot_wxid="wxid_bot") == []


def test_late_ui_event_reuses_exact_raw_row(tmp_path: Path) -> None:
    db_path = tmp_path / "reconcile.db"
    init_db(db_path)
    raw = _message()
    live = _message(
        source="live",
        sender=None,
        timestamp=1003,
        wx_msg_id="ui-live:event-1",
    )
    with get_conn(db_path) as conn:
        assert write_messages(conn, [raw]) == (1, 1)
        assert write_messages(conn, [live]) == (1, 0)
        row = conn.execute(
            "SELECT wx_msg_id,sender_wxid,sender_display,source FROM messages"
        ).fetchone()
        assert tuple(row) == ("raw-1", "wxid_friend", "member", "live")
