"""GUI-side actions mirroring CLI send/build flows without typer.

Every real-send function here follows the exact authorization chain of its
CLI counterpart: exact canonical-group resolution, `build_replier(force_send=
True)`, the idempotent outbox, and `require_current_authorization=True`.
Failures raise ValueError with user-presentable messages; the GUI shows them
in message boxes.
"""

from __future__ import annotations

import contextlib
import io
import json
from typing import Any

from ..config import settings
from ..daily_summary import (
    deliver_manual_text,
    latest_active_hour,
    reset_failed_summary_period,
    resolve_summary_groups,
    run_summary_group,
)
from ..db import get_conn, init_db
from ..llm import build_llm_client
from ..member_broadcast import broadcast_status, create_or_resume_broadcast
from ..member_knowledge import (
    get_member_profile,
    render_member_profile_card,
    select_random_completed_profile,
)
from ..replier import build_replier
from ..time_ranges import latest_mature_summary_periods


def list_send_groups() -> list[tuple[str, str]]:
    init_db()
    with get_conn() as conn:
        return [(str(a), str(b)) for a, b in resolve_summary_groups(conn)]


def resolve_send_group(conn, selector: str) -> tuple[str, str]:
    matches = [
        item for item in resolve_summary_groups(conn)
        if selector.strip() in {item[0], item[1]}
    ]
    if not matches:
        raise ValueError("群必须精确匹配当前已选且已授权发送的群 id 或名称")
    if len(matches) != 1:
        raise ValueError("群选择器有歧义；请使用精确的 canonical 群 id")
    return matches[0]


def resolve_member(conn, group_id: str, selector: str) -> str:
    from .member_knowledge import list_member_profiles

    exact: list[str] = []
    raw = conn.execute(
        """
        SELECT DISTINCT CASE
            WHEN sender_wxid IS NULL OR TRIM(sender_wxid)='' THEN '__unknown__'
            ELSE TRIM(sender_wxid) END AS member
          FROM messages
         WHERE group_id=?
        """,
        (group_id,),
    ).fetchall()
    exact.extend(str(row["member"]) for row in raw if selector == str(row["member"]))
    for item in list_member_profiles(conn, group_id):
        sender = str(item.get("sender_wxid") or "")
        names = {
            str(item.get("display_name") or ""),
            str(item.get("current_display_name") or ""),
            *(str(value) for value in (item.get("aliases") or [])),
        }
        if selector == sender or selector in names:
            exact.append(sender)
    exact = list(dict.fromkeys(value for value in exact if value))
    if not exact:
        raise ValueError("成员未精确匹配到 wxid 或昵称")
    if len(exact) != 1:
        raise ValueError("成员选择器有歧义；请使用精确的 sender wxid")
    return exact[0]


def summary_send_once(
    group: str,
    period_kind: str = "previous-hour",
    *,
    retry_failed: bool = False,
) -> dict[str, Any]:
    normalized = period_kind.strip().lower()
    if normalized not in {"previous-hour", "latest-active-hour", "previous-day"}:
        raise ValueError("周期只能是 previous-hour、latest-active-hour 或 previous-day")
    init_db()
    with get_conn() as conn:
        group_id, group_name = resolve_send_group(conn, group)
        if normalized == "latest-active-hour":
            period = latest_active_hour(
                conn,
                group_id=group_id,
                min_messages=settings.hourly_summary_min_messages,
            )
            if period is None:
                raise ValueError("没有满足最少消息数的已完成活跃小时")
            min_messages = settings.hourly_summary_min_messages
        else:
            periods = latest_mature_summary_periods(
                tz=settings.summary_tz,
                grace_seconds=settings.summary_sync_grace_seconds,
                hourly=normalized == "previous-hour",
                daily=normalized == "previous-day",
            )
            period = periods[0]
            min_messages = (
                settings.hourly_summary_min_messages
                if period.kind == "hourly"
                else settings.daily_summary_min_messages
            )
        if retry_failed:
            reset_failed_summary_period(conn, group_id=group_id, period=period)
        llm = build_llm_client(
            provider=settings.llm_provider,
            api_key=settings.llm_api_key,
            endpoint=settings.llm_endpoint,
            json_mode=settings.llm_json_mode,
        )
        replier = build_replier(force_send=True)
        result = run_summary_group(
            conn,
            group_id=group_id,
            group_name=group_name,
            period=period,
            min_messages=min_messages,
            replier=replier,
            llm=llm,
            require_current_authorization=True,
        )
    if result != "sent":
        raise ValueError(f"摘要投递结果为 {result}；详情见审计页")
    return {"delivery": result, "group": group_name, "period": period.label}


def member_send(
    group: str,
    *,
    member: str = "",
    display_name: str = "",
    random_pick: bool = False,
) -> dict[str, Any]:
    if not settings.member_kb_enabled:
        raise ValueError("成员知识库未启用")
    title = display_name.strip()
    if title and (len(title) > 80 or "\n" in title or "\r" in title):
        raise ValueError("显示名必须是不超过 80 字的单行文本")
    init_db()
    with get_conn() as conn:
        group_id, group_name = resolve_send_group(conn, group)
        if random_pick:
            profile = select_random_completed_profile(conn, group_id)
            if profile is None:
                raise ValueError("当前没有可发布的已完成成员画像")
        else:
            if not member.strip():
                raise ValueError("请填写成员 wxid 或精确昵称")
            sender_wxid = resolve_member(conn, group_id, member.strip())
            profile = get_member_profile(conn, group_id, sender_wxid)
            if profile is None:
                raise ValueError("该成员画像不存在或已删除")
        card = render_member_profile_card(profile)
        replier = build_replier(force_send=True)
        result = deliver_manual_text(
            conn,
            group_id=group_id,
            group_name=group_name,
            text=card,
            replier=replier,
            require_current_authorization=True,
        )
    if result != "sent":
        raise ValueError(f"画像投递结果为 {result}")
    return {
        "delivery": result,
        "group": group_name,
        "member": profile.get("display_name") or profile.get("sender_wxid"),
    }


def broadcast_start(group: str, skip_members: tuple[str, ...] = ()) -> dict[str, Any]:
    if not settings.member_kb_enabled:
        raise ValueError("成员知识库未启用")
    init_db()
    with get_conn() as conn:
        group_id, group_name = resolve_send_group(conn, group)
        status = create_or_resume_broadcast(
            conn,
            group_id=group_id,
            group_name=group_name,
            skip_members=skip_members,
        )
    return status


def broadcast_progress() -> dict[str, Any]:
    init_db()
    with get_conn() as conn:
        return broadcast_status(conn)


def raw_command(command: str, *args: str) -> dict[str, Any]:
    """Run a `raw_wechat.cli` command in-process; returns its JSON payload."""
    from .raw_wechat.cli import main as raw_main

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = raw_main([command, *args])
    output = buffer.getvalue().strip()
    if code:
        detail = output.splitlines()[-1] if output else f"exit {code}"
        raise ValueError(detail)
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise ValueError(f"无法解析 raw 输出：{output[:200]}") from exc
    return payload