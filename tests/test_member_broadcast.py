from __future__ import annotations

import time

from wechat_oracle.db import get_conn, init_db
from wechat_oracle.member_broadcast import (
    MemberProfileBroadcastScheduler,
    broadcast_status,
    create_or_resume_broadcast,
)


def _message(conn, sender: str | None, display: str, key: str) -> None:
    conn.execute(
        "INSERT INTO messages(group_id,group_name,t,type,sender_wxid,sender_display,content_text,source,status,dedupe_key) "
        "VALUES ('g','Group',1,'text',?,?,?,'backfill','raw',?)",
        (sender, display, "hello", key),
    )


def test_campaign_snapshot_skips_unknown_and_explicit_members(tmp_path) -> None:
    path = tmp_path / "broadcast.db"
    init_db(path)
    with get_conn(path) as conn:
        _message(conn, None, "Unknown", "u")
        _message(conn, "a", "Alice", "a")
        _message(conn, "b", "Bob", "b")
        # Materialize profiles the same way the member scheduler does.
        from wechat_oracle.member_knowledge import list_member_profiles
        list_member_profiles(conn, "g")
        status = create_or_resume_broadcast(
            conn, group_id="g", group_name="Group", skip_members=["a"]
        )
        assert status["counts"] == {"pending": 1}
        item = conn.execute(
            "SELECT sender_wxid,display_name,delivery_marker FROM member_profile_broadcast_items"
        ).fetchone()
        assert item["sender_wxid"] == "b"
        assert item["display_name"] == "Bob"
        assert int(item["delivery_marker"]) < 0


def test_scheduler_sends_only_completed_profile_and_finishes_campaign(tmp_path, monkeypatch) -> None:
    path = tmp_path / "broadcast.db"
    init_db(path)
    with get_conn(path) as conn:
        _message(conn, "a", "Alice", "a")
        from wechat_oracle.member_knowledge import list_member_profiles
        list_member_profiles(conn, "g")
        campaign = create_or_resume_broadcast(conn, group_id="g", group_name="Group")
        conn.execute(
            "UPDATE member_profiles SET summary_text='profile' WHERE group_id='g' AND sender_wxid='a'"
        )
        conn.execute(
            "INSERT INTO member_update_state(group_id,sender_wxid,cursor_msg_id,full_history_complete,last_status,updated_at) "
            "VALUES ('g','a',1,1,'succeeded',?)",
            (time.time(),),
        )

    monkeypatch.setattr(
        "wechat_oracle.member_broadcast.deliver_manual_text", lambda *args, **kwargs: "sent"
    )
    scheduler = MemberProfileBroadcastScheduler(db_path=path, replier=object())
    monkeypatch.setattr(scheduler, "_summary_window", lambda now: False)
    assert scheduler.maybe_submit(1000)["submitted"] == 1
    scheduler.close()
    with get_conn(path) as conn:
        item = conn.execute("SELECT status FROM member_profile_broadcast_items").fetchone()
        # Fake delivery has no summary row, so the direct sent result is sufficient.
        assert item["status"] == "sent"
        scheduler._finish_campaigns()
        assert broadcast_status(conn, campaign["campaign"])["status"] == "complete"
