"""Persistent, restart-safe delivery of one profile card per group member."""

from __future__ import annotations

import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Iterable

from .config import settings
from .daily_summary import deliver_manual_text
from .db import get_conn, transaction
from .member_knowledge import UNKNOWN_MEMBER_ID, get_member_profile, render_member_profile_card


def create_or_resume_broadcast(
    conn,
    *,
    group_id: str,
    group_name: str,
    skip_members: Iterable[str] = (),
) -> dict[str, Any]:
    """Create a durable snapshot, or return the current unfinished campaign."""
    existing = conn.execute(
        "SELECT campaign_id,status FROM member_profile_broadcasts "
        "WHERE group_id=? AND status IN ('running','partial') ORDER BY campaign_id DESC LIMIT 1",
        (group_id,),
    ).fetchone()
    if existing is not None:
        campaign_id = int(existing["campaign_id"])
        with transaction(conn):
            conn.execute(
                "UPDATE member_profile_broadcasts SET status='running',updated_at=? WHERE campaign_id=?",
                (time.time(), campaign_id),
            )
            conn.execute(
                "UPDATE member_profile_broadcast_items SET status='pending',last_error='',updated_at=? "
                "WHERE campaign_id=? AND status='failed'",
                (time.time(), campaign_id),
            )
        return broadcast_status(conn, campaign_id)

    skip = {str(item).strip() for item in skip_members if str(item).strip()}
    now = time.time()
    with transaction(conn):
        cur = conn.execute(
            "INSERT INTO member_profile_broadcasts(group_id,group_name,status,created_at,updated_at) "
            "VALUES (?,?,'running',?,?)",
            (group_id, group_name, now, now),
        )
        campaign_id = int(cur.lastrowid)
        rows = conn.execute(
            """
            SELECT p.sender_wxid,
                   COALESCE(NULLIF(TRIM(p.display_name),''), p.sender_wxid) AS display_name
              FROM member_profiles p
             WHERE p.group_id=? AND p.sender_wxid<>? AND p.deleted_at IS NULL
               AND EXISTS (
                   SELECT 1 FROM messages m
                    WHERE m.group_id=p.group_id AND TRIM(COALESCE(m.sender_wxid,''))=p.sender_wxid
               )
             ORDER BY display_name, p.sender_wxid
            """,
            (group_id, UNKNOWN_MEMBER_ID),
        ).fetchall()
        for ordinal, row in enumerate(rows, start=1):
            member = str(row["sender_wxid"])
            if member in skip:
                continue
            item = conn.execute(
                "INSERT INTO member_profile_broadcast_items"
                "(campaign_id,sender_wxid,display_name,status,delivery_marker,updated_at) "
                "VALUES (?,?,?,'pending',?,?)",
                (campaign_id, member, str(row["display_name"]), -(campaign_id * 1_000_000 + ordinal), now),
            )
    return broadcast_status(conn, campaign_id)


def broadcast_status(conn, campaign_id: int | None = None) -> dict[str, Any]:
    if campaign_id is None:
        row = conn.execute(
            "SELECT campaign_id,group_id,group_name,status FROM member_profile_broadcasts "
            "ORDER BY campaign_id DESC LIMIT 1"
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT campaign_id,group_id,group_name,status FROM member_profile_broadcasts WHERE campaign_id=?",
            (int(campaign_id),),
        ).fetchone()
    if row is None:
        return {"campaign": None, "counts": {}}
    counts = {
        str(item["status"]): int(item["n"])
        for item in conn.execute(
            "SELECT status,COUNT(*) n FROM member_profile_broadcast_items "
            "WHERE campaign_id=? GROUP BY status",
            (int(row["campaign_id"]),),
        ).fetchall()
    }
    return {
        "campaign": int(row["campaign_id"]),
        "group_id": str(row["group_id"]),
        "group_name": str(row["group_name"]),
        "status": str(row["status"]),
        "counts": counts,
    }


class MemberProfileBroadcastScheduler:
    """Send completed campaign members one at a time; building stays with the KB scheduler."""

    def __init__(self, *, db_path: Path, replier: Any):
        self.db_path = Path(db_path)
        self.replier = replier
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="member-profile-broadcast")
        self._future: Future[Any] | None = None

    def _summary_window(self, now: float) -> bool:
        local = time.localtime(now)
        protected = int(settings.summary_sync_grace_seconds) + 120
        return local.tm_min * 60 + local.tm_sec <= protected

    def maybe_submit(self, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else float(now)
        if self._future is not None:
            if not self._future.done():
                return {"submitted": 0, "busy": True}
            try:
                self._future.result()
            except Exception as exc:
                # Never leak message/profile text into scheduler status or logs.
                error = f"{type(exc).__name__}: broadcast item failed"
                self._future = None
                return {"submitted": 0, "busy": False, "error": error}
            self._future = None
        if self._summary_window(now):
            return {"submitted": 0, "paused_for_summary": True}
        with get_conn(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT i.item_id
                  FROM member_profile_broadcast_items i
                  JOIN member_profile_broadcasts b ON b.campaign_id=i.campaign_id
                  JOIN member_update_state s
                    ON s.group_id=b.group_id AND s.sender_wxid=i.sender_wxid
                  JOIN member_profiles p
                    ON p.group_id=b.group_id AND p.sender_wxid=i.sender_wxid
                 WHERE b.status='running' AND i.status IN ('pending','sending')
                   AND s.full_history_complete=1
                   AND (LENGTH(TRIM(p.summary_text))>0 OR EXISTS (
                       SELECT 1 FROM member_claims c
                        WHERE c.group_id=b.group_id AND c.sender_wxid=i.sender_wxid AND c.status='current'
                   ))
                 ORDER BY b.campaign_id,i.item_id LIMIT 1
                """
            ).fetchone()
        if row is None:
            self._finish_campaigns()
            return {"submitted": 0, "busy": False}
        item_id = int(row["item_id"])
        self._future = self._executor.submit(self._deliver_item, item_id)
        return {"submitted": 1, "item_id": item_id}

    def _deliver_item(self, item_id: int) -> None:
        with get_conn(self.db_path) as conn:
            item = conn.execute(
                """
                SELECT i.*,b.group_id,b.group_name,b.status campaign_status
                  FROM member_profile_broadcast_items i
                  JOIN member_profile_broadcasts b ON b.campaign_id=i.campaign_id
                 WHERE i.item_id=?
                """,
                (item_id,),
            ).fetchone()
            if item is None or str(item["campaign_status"]) != "running":
                return
            with transaction(conn):
                conn.execute(
                    "UPDATE member_profile_broadcast_items SET status='sending',attempt_count=attempt_count+1,updated_at=? "
                    "WHERE item_id=? AND status IN ('pending','sending')",
                    (time.time(), item_id),
                )
            profile = get_member_profile(conn, str(item["group_id"]), str(item["sender_wxid"]))
            if profile is None:
                return
            profile = dict(profile)
            profile["display_name"] = str(item["display_name"])
            result = deliver_manual_text(
                conn,
                group_id=str(item["group_id"]),
                group_name=str(item["group_name"]),
                text=render_member_profile_card(profile),
                replier=self.replier,
                require_current_authorization=True,
                delivery_marker=int(item["delivery_marker"]),
            )
            run = conn.execute(
                "SELECT status FROM summary_runs WHERE group_id=? AND period_start=? AND trigger_kind='manual'",
                (str(item["group_id"]), int(item["delivery_marker"])),
            ).fetchone()
            run_status = str(run["status"]) if run is not None else ""
            if result == "sent" or run_status == "sent":
                status, error = "sent", ""
            elif result == "unknown" or run_status == "unknown":
                status, error = "unknown", "delivery outcome unknown; never retry"
            else:
                status, error = "failed", f"delivery_{result}"
            with transaction(conn):
                conn.execute(
                    "UPDATE member_profile_broadcast_items SET status=?,last_error=?,updated_at=? WHERE item_id=?",
                    (status, error, time.time(), item_id),
                )
        self._finish_campaigns()

    def _finish_campaigns(self) -> None:
        with get_conn(self.db_path) as conn, transaction(conn):
            rows = conn.execute(
                "SELECT campaign_id FROM member_profile_broadcasts WHERE status='running'"
            ).fetchall()
            for row in rows:
                campaign_id = int(row["campaign_id"])
                counts = {
                    str(item["status"]): int(item["n"])
                    for item in conn.execute(
                        "SELECT status,COUNT(*) n FROM member_profile_broadcast_items WHERE campaign_id=? GROUP BY status",
                        (campaign_id,),
                    ).fetchall()
                }
                total = sum(counts.values())
                if counts.get("pending", 0) or counts.get("sending", 0):
                    continue
                if total and counts.get("sent", 0) == total:
                    conn.execute(
                        "UPDATE member_profile_broadcasts SET status='complete',updated_at=? WHERE campaign_id=?",
                        (time.time(), campaign_id),
                    )
                elif total:
                    conn.execute(
                        "UPDATE member_profile_broadcasts SET status='partial',updated_at=? WHERE campaign_id=?",
                        (time.time(), campaign_id),
                    )

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=False)


__all__ = ["MemberProfileBroadcastScheduler", "broadcast_status", "create_or_resume_broadcast"]
