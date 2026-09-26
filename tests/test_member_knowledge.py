from __future__ import annotations

import json
import threading
from types import SimpleNamespace

from wechat_oracle.db import get_conn, init_db
from wechat_oracle.member_knowledge import (
    UNKNOWN_MEMBER_ID,
    delete_member_profile,
    get_member_profile,
    list_member_messages,
    render_member_profile_card,
    run_member_update,
    select_random_completed_profile,
    MemberKnowledgeScheduler,
    update_member_profile_section,
)


def _message(conn, group: str, sender: str | None, t: int, body: str, key: str, display: str = "") -> int:
    cur = conn.execute(
        """
        INSERT INTO messages(group_id,sender_wxid,sender_display,t,type,content_text,source,dedupe_key)
        VALUES(?,?,?,?,?,?,?,?)
        """,
        (group, sender, display or sender, t, "text", body, "live", key),
    )
    return int(cur.lastrowid)


class FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def complete_json(self, *, model, system, user, temperature=0.0):
        self.calls += 1
        return json.dumps(self.payload, ensure_ascii=False)


class ChunkLLM:
    def __init__(self, *, fail_on_call: int | None = None):
        self.calls = 0
        self.fail_on_call = fail_on_call

    def complete_json(self, *, model, system, user, temperature=0.0):
        self.calls += 1
        if self.fail_on_call == self.calls:
            raise RuntimeError("provider unavailable")
        messages = json.loads(user.split("MESSAGES=", 1)[1])
        last_id = messages[-1]["msg_id"]
        return json.dumps(
            {
                "profile": {"recent_focus": f"through-{last_id}"},
                "claims": [{
                    "section": "recent_focus",
                    "claim_text": f"active through message {last_id}",
                    "basis": "observed",
                    "confidence": 0.8,
                    "sensitive": False,
                    "evidence_ids": [last_id],
                }],
            },
            ensure_ascii=False,
        )


class PositionalTypeErrorLLM:
    def __init__(self):
        self.calls = 0

    def complete_json(self, system, user):
        self.calls += 1
        raise TypeError("provider failed internally")


class CorrectingLLM:
    def __init__(self, msg_id: int):
        self.msg_id = msg_id
        self.users: list[str] = []

    def complete_json(self, *, model, system, user, temperature=0.0):
        self.users.append(user)
        if len(self.users) == 1:
            return '{"profile":{"interests":"database"}}'
        return json.dumps({
            "profile": {"interests": "database"},
            "summary_text": "Alice likes databases.",
            "claims": [{
                "section": "interests",
                "claim_text": "likes databases",
                "basis": "self_reported",
                "confidence": 0.9,
                "sensitive": False,
                "evidence_ids": [self.msg_id],
            }],
        })


class FencedJsonLLM:
    def __init__(self, msg_id: int):
        self.msg_id = msg_id

    def complete_json(self, *, model, system, user, temperature=0.0):
        return "```json\n" + json.dumps({
            "claims": [{
                "section": "skills",
                "claim_text": "writes SQL",
                "basis": "observed",
                "confidence": 0.8,
                "sensitive": False,
                "evidence_ids": [self.msg_id],
            }],
        }) + "\n```"


class EmptyLLM:
    def __init__(self):
        self.calls = 0

    def complete_json(self, *, model, system, user, temperature=0.0):
        self.calls += 1
        return "{}"


class BlockingLLM:
    def __init__(self, started: threading.Event, release: threading.Event):
        self.started = started
        self.release = release

    def complete_json(self, *, model, system, user, temperature=0.0):
        self.started.set()
        assert self.release.wait(5)
        messages = json.loads(user.split("MESSAGES=", 1)[1])
        msg_id = messages[-1]["msg_id"]
        return json.dumps({
            "claims": [{
                "section": "recent_focus",
                "claim_text": f"active through message {msg_id}",
                "basis": "observed",
                "confidence": 0.8,
                "sensitive": False,
                "evidence_ids": [msg_id],
            }],
        })


class BasisCorrectingLLM:
    def __init__(self, msg_id: int):
        self.msg_id = msg_id
        self.users: list[str] = []

    def complete_json(self, *, model, system, user, temperature=0.0):
        self.users.append(user)
        basis = "观察" if len(self.users) == 1 else "observed"
        return json.dumps({
            "claims": [{
                "section": "communication_style",
                "claim_text": "uses concise phrases",
                "basis": basis,
                "confidence": 0.7,
                "sensitive": False,
                "evidence_ids": [self.msg_id],
            }],
        }, ensure_ascii=False)


def test_identified_member_cannot_complete_with_empty_profile(tmp_path):
    path = tmp_path / "members.db"
    init_db(path)
    with get_conn(path) as conn:
        _message(conn, "g1", "alice", 1, "I enjoy databases", "empty-profile")
        llm = EmptyLLM()

        result = run_member_update(conn, "g1", "alice", llm, retries=2, sleep=lambda _: None)

        assert result["status"] == "failed"
        assert llm.calls == 2
        state = conn.execute(
            "SELECT cursor_msg_id, full_history_complete, last_error FROM member_update_state "
            "WHERE group_id='g1' AND sender_wxid='alice'"
        ).fetchone()
        assert dict(state) == {
            "cursor_msg_id": 0,
            "full_history_complete": 0,
            "last_error": "_ValidationError: empty_identified_profile",
        }


def test_invalid_basis_retry_names_exact_allowed_tokens(tmp_path):
    path = tmp_path / "members.db"
    init_db(path)
    with get_conn(path) as conn:
        msg_id = _message(conn, "g1", "alice", 1, "short message", "basis-retry")
        llm = BasisCorrectingLLM(msg_id)

        result = run_member_update(conn, "g1", "alice", llm, retries=2, sleep=lambda _: None)

        assert result["status"] == "succeeded"
        assert len(llm.users) == 2
        assert "self_reported, observed, inferred" in llm.users[1]


def test_group_scoped_unknown_and_alias_history(tmp_path):
    path = tmp_path / "members.db"
    init_db(path)
    with get_conn(path) as conn:
        first = _message(conn, "g1", None, 1, "hello", "a", "Old Name")
        _message(conn, "g1", None, 2, "rename", "b", "New Name")
        _message(conn, "g2", None, 3, "other group", "c", "New Name")
        assert [m["msg_id"] for m in list_member_messages(conn, "g1", None)] == [2, first]
        llm = FakeLLM(
            {
                "claims": [
                    {
                        "section": "identity",
                        "claim_text": "uses two names",
                        "basis": "observed",
                        "confidence": 0.8,
                        "sensitive": False,
                        "evidence_ids": [first],
                    }
                ]
            }
        )
        result = run_member_update(conn, "g1", UNKNOWN_MEMBER_ID, llm, retries=1)
        assert result["status"] == "succeeded"
        profile = get_member_profile(conn, "g1", None)
        assert profile is not None
        assert set(profile["aliases"]) == {"Old Name", "New Name"}
        assert get_member_profile(conn, "g2", None) is None


def test_invalid_evidence_does_not_advance_cursor(tmp_path):
    path = tmp_path / "members.db"
    init_db(path)
    with get_conn(path) as conn:
        msg_id = _message(conn, "g", "u", 1, "hello", "a", "Alice")
        llm = FakeLLM(
            {
                "claims": [
                    {
                        "section": "interests",
                        "claim_text": "bad evidence",
                        "basis": "observed",
                        "confidence": 0.4,
                        "sensitive": False,
                        "evidence_ids": [msg_id + 100],
                    }
                ]
            }
        )
        result = run_member_update(conn, "g", "u", llm, retries=1)
        assert result["status"] == "failed"
        state = conn.execute(
            "SELECT cursor_msg_id FROM member_update_state WHERE group_id='g' AND sender_wxid='u'"
        ).fetchone()
        assert state["cursor_msg_id"] == 0
        assert conn.execute("SELECT COUNT(*) FROM member_claims").fetchone()[0] == 0


def test_profile_section_without_evidence_claim_does_not_advance_cursor(tmp_path):
    path = tmp_path / "members.db"
    init_db(path)
    with get_conn(path) as conn:
        _message(conn, "g", "u", 1, "hello", "a", "Alice")
        result = run_member_update(
            conn,
            "g",
            "u",
            FakeLLM({"profile": {"interests": "untraceable"}}),
            retries=1,
        )
        assert result["status"] == "failed"
        state = conn.execute(
            "SELECT cursor_msg_id FROM member_update_state WHERE group_id='g' AND sender_wxid='u'"
        ).fetchone()
        assert state["cursor_msg_id"] == 0


def test_validation_retry_gives_actionable_feedback_and_recovers(tmp_path):
    path = tmp_path / "members.db"
    init_db(path)
    with get_conn(path) as conn:
        msg_id = _message(conn, "g", "u", 1, "I like databases", "a", "Alice")
        llm = CorrectingLLM(msg_id)
        result = run_member_update(conn, "g", "u", llm, retries=2, sleep=lambda _: None)
        assert result["status"] == "succeeded"
        assert "VALIDATION_RETRY=" in llm.users[1]
        assert "updated profile section requires" in llm.users[1]
        assert conn.execute("SELECT cursor_msg_id FROM member_update_state").fetchone()[0] == msg_id


def test_single_fenced_json_object_is_accepted(tmp_path):
    path = tmp_path / "members.db"
    init_db(path)
    with get_conn(path) as conn:
        msg_id = _message(conn, "g", "u", 1, "SQL", "a", "Alice")
        result = run_member_update(conn, "g", "u", FencedJsonLLM(msg_id), retries=1)
        assert result["status"] == "succeeded"


def test_random_profile_requires_completed_non_unknown_derived_content(tmp_path):
    path = tmp_path / "members.db"
    init_db(path)
    with get_conn(path) as conn:
        msg_id = _message(conn, "g", "u", 1, "I like SQL", "a", "Alice")
        payload = {
            "summary_text": "Alice likes SQL.",
            "claims": [{
                "section": "interests", "claim_text": "likes SQL",
                "basis": "self_reported", "confidence": 0.9,
                "sensitive": False, "evidence_ids": [msg_id],
            }],
        }
        assert run_member_update(conn, "g", "u", FakeLLM(payload), retries=1)["status"] == "succeeded"
        profile = select_random_completed_profile(conn, "g")
        assert profile is not None
        card = render_member_profile_card(profile)
        assert card.startswith("#成员画像（Alice）")
        assert "Alice likes SQL." in card
        assert "本人自述 · 90%" in card
        assert "I like SQL" not in card


def test_compat_llm_type_error_does_not_duplicate_a_request(tmp_path):
    path = tmp_path / "members.db"
    init_db(path)
    with get_conn(path) as conn:
        _message(conn, "g", "u", 1, "hello", "a", "Alice")
        llm = PositionalTypeErrorLLM()
        result = run_member_update(conn, "g", "u", llm, retries=1)
        assert result["status"] == "failed"
        assert llm.calls == 1


def test_locked_section_and_delete_keep_raw_messages(tmp_path):
    path = tmp_path / "members.db"
    init_db(path)
    with get_conn(path) as conn:
        msg_id = _message(conn, "g", "u", 1, "music", "a", "Alice")
        update_member_profile_section(conn, "g", "u", "interests", "manual", locked=True)
        llm = FakeLLM(
            {
                "profile": {"interests": "model edit", "skills": "writing"},
                "claims": [
                    {
                        "section": "skills",
                        "claim_text": "writes",
                        "basis": "observed",
                        "confidence": 0.9,
                        "sensitive": False,
                        "evidence_ids": [msg_id],
                    }
                ],
            }
        )
        assert run_member_update(conn, "g", "u", llm, retries=1)["status"] == "succeeded"
        profile = get_member_profile(conn, "g", "u")
        assert profile["interests"] == "manual"
        assert profile["skills"] == "writing"
        assert delete_member_profile(conn, "g", "u") is True
        assert get_member_profile(conn, "g", "u") is None
        assert conn.execute("SELECT COUNT(*) FROM messages WHERE msg_id=?", (msg_id,)).fetchone()[0] == 1
        assert conn.execute("SELECT status FROM member_claims").fetchone()[0] == "deleted"


def test_same_name_and_same_wxid_remain_group_scoped(tmp_path):
    path = tmp_path / "members.db"
    init_db(path)
    with get_conn(path) as conn:
        _message(conn, "g1", "wx-a", 1, "a", "a", "同名")
        _message(conn, "g1", "wx-b", 2, "b", "b", "同名")
        _message(conn, "g2", "wx-a", 3, "c", "c", "另一个群昵称")
        from wechat_oracle.member_knowledge import list_member_profiles

        g1 = list_member_profiles(conn, "g1")
        g2 = list_member_profiles(conn, "g2")
        assert {row["sender_wxid"] for row in g1} == {"wx-a", "wx-b"}
        assert {row["sender_wxid"] for row in g2} == {"wx-a"}
        update_member_profile_section(conn, "g1", "wx-a", "identity", "群一身份")
        assert get_member_profile(conn, "g1", "wx-a")["identity"] == "群一身份"
        assert get_member_profile(conn, "g2", "wx-a")["identity"] is None


def test_partial_chunk_failure_resumes_without_replaying_committed_chunk(tmp_path):
    path = tmp_path / "members.db"
    init_db(path)
    with get_conn(path) as conn:
        first = _message(conn, "g", "u", 1, "first", "a", "Alice")
        second = _message(conn, "g", "u", 2, "second", "b", "Alice")
        failed = run_member_update(
            conn, "g", "u", ChunkLLM(fail_on_call=2), chunk_chars=1, retries=1
        )
        assert failed["status"] == "failed"
        assert failed["cursor_after"] == first

        resumed_llm = ChunkLLM()
        resumed = run_member_update(
            conn, "g", "u", resumed_llm, chunk_chars=1, retries=1
        )
        assert resumed["status"] == "succeeded"
        assert resumed["cursor_before"] == first
        assert resumed["cursor_after"] == second
        assert resumed_llm.calls == 1

        no_new = ChunkLLM()
        assert run_member_update(conn, "g", "u", no_new)["status"] == "skipped"
        assert no_new.calls == 0


def test_superseded_claim_is_retained_with_replacement_link(tmp_path):
    path = tmp_path / "members.db"
    init_db(path)
    with get_conn(path) as conn:
        first = _message(conn, "g", "u", 1, "喜欢茶", "a", "Alice")
        initial = FakeLLM({
            "claims": [{
                "section": "interests", "claim_text": "喜欢茶", "basis": "self_reported",
                "confidence": 0.9, "sensitive": False, "evidence_ids": [first],
            }]
        })
        assert run_member_update(conn, "g", "u", initial, retries=1)["status"] == "succeeded"
        old_id = conn.execute("SELECT claim_id FROM member_claims").fetchone()[0]

        second = _message(conn, "g", "u", 2, "现在更喜欢咖啡", "b", "Alice")
        replacement = FakeLLM({
            "claims": [{
                "section": "interests", "claim_text": "更喜欢咖啡", "basis": "self_reported",
                "confidence": 0.95, "sensitive": False, "evidence_ids": [second],
                "supersedes": [old_id],
            }]
        })
        assert run_member_update(conn, "g", "u", replacement, retries=1)["status"] == "succeeded"
        rows = conn.execute(
            "SELECT claim_id,status,superseded_by FROM member_claims ORDER BY claim_id"
        ).fetchall()
        assert rows[0]["status"] == "superseded"
        assert rows[0]["superseded_by"] == rows[1]["claim_id"]
        assert rows[1]["status"] == "current"


def test_scheduler_bootstrap_includes_current_hour(tmp_path):
    path = tmp_path / "members.db"
    init_db(path)
    with get_conn(path) as conn:
        _message(conn, "g", "u", 43_199, "before boundary", "a", "Alice")
        latest = _message(conn, "g", "u", 43_200, "new hour", "b", "Alice")

    payload = {
        "profile": {"recent_focus": "updated"},
        "claims": [{
            "section": "recent_focus", "claim_text": "updated focus",
            "basis": "observed", "confidence": 0.8, "sensitive": False,
            "evidence_ids": [latest],
        }],
    }
    config = SimpleNamespace(
        db_path=path,
        groups=["g"],
        summary_sync_grace_seconds=300,
        member_kb_chunk_chars=24_000,
        member_kb_retries=1,
        member_kb_max_concurrency=1,
    )
    scheduler = MemberKnowledgeScheduler(
        db_path=path,
        llm_factory=lambda: FakeLLM(payload),
        settings_like=config,
        max_workers=1,
    )
    try:
        assert scheduler.maybe_submit(43_499)["submitted"] == 1
    finally:
        scheduler.close()
    with get_conn(path) as conn:
        state = conn.execute(
            "SELECT cursor_msg_id,full_history_complete FROM member_update_state WHERE group_id='g' AND sender_wxid='u'"
        ).fetchone()
        assert state["cursor_msg_id"] == latest
        assert state["full_history_complete"] == 1


def test_scheduler_queues_only_available_worker_capacity(tmp_path):
    path = tmp_path / "members.db"
    init_db(path)
    with get_conn(path) as conn:
        _message(conn, "g", "u1", 1, "one", "capacity-1", "Alice")
        _message(conn, "g", "u2", 2, "two", "capacity-2", "Bob")
        _message(conn, "g", "u3", 3, "three", "capacity-3", "Carol")

    started = threading.Event()
    release = threading.Event()
    config = SimpleNamespace(
        db_path=path,
        groups=["g"],
        summary_sync_grace_seconds=0,
        member_kb_chunk_chars=24_000,
        member_kb_retries=1,
        member_kb_max_concurrency=1,
    )
    scheduler = MemberKnowledgeScheduler(
        db_path=path,
        llm_factory=lambda: BlockingLLM(started, release),
        settings_like=config,
        max_workers=1,
    )
    try:
        assert scheduler.maybe_submit(3_600)["submitted"] == 1
        assert started.wait(2)
        assert scheduler.maybe_submit(3_600)["submitted"] == 0
        assert sum(not job["done"] for job in scheduler.status()["jobs"]) == 1
    finally:
        release.set()
        scheduler.close()


def test_scheduler_waits_for_hourly_grace_and_excludes_new_hour(tmp_path):
    path = tmp_path / "members.db"
    init_db(path)
    with get_conn(path) as conn:
        mature = _message(conn, "g", "u", 43_199, "before boundary", "a", "Alice")
        assert run_member_update(
            conn,
            "g",
            "u",
            FakeLLM({
                "profile": {"recent_focus": "baseline"},
                "claims": [{
                    "section": "recent_focus", "claim_text": "baseline focus",
                    "basis": "observed", "confidence": 0.8, "sensitive": False,
                    "evidence_ids": [mature],
                }],
            }),
            retries=1,
        )["status"] == "succeeded"
        new_hour = _message(conn, "g", "u", 43_200, "new hour", "b", "Alice")

    config = SimpleNamespace(
        db_path=path,
        groups=["g"],
        summary_sync_grace_seconds=300,
        member_kb_chunk_chars=24_000,
        member_kb_retries=1,
        member_kb_max_concurrency=1,
    )
    scheduler = MemberKnowledgeScheduler(
        db_path=path,
        llm_factory=lambda: FakeLLM({
            "profile": {"recent_focus": "updated"},
            "claims": [{
                "section": "recent_focus", "claim_text": "new focus",
                "basis": "observed", "confidence": 0.8, "sensitive": False,
                "evidence_ids": [new_hour],
            }],
        }),
        settings_like=config,
        max_workers=1,
    )
    try:
        assert scheduler.maybe_submit(43_499)["submitted"] == 0
        assert scheduler.maybe_submit(43_500)["submitted"] == 0
        assert scheduler.maybe_submit(47_100)["submitted"] == 1
    finally:
        scheduler.close()
    with get_conn(path) as conn:
        state = conn.execute(
            "SELECT cursor_msg_id FROM member_update_state WHERE group_id='g' AND sender_wxid='u'"
        ).fetchone()
        assert mature < state["cursor_msg_id"] == new_hour


def test_scheduler_never_profiles_an_unauthorized_raw_group(tmp_path):
    path = tmp_path / "members.db"
    init_db(path)
    with get_conn(path) as conn:
        allowed_msg = _message(conn, "allowed@chatroom", "u1", 100, "allowed", "a", "Alice")
        _message(conn, "blocked@chatroom", "u2", 100, "blocked", "b", "Bob")
        conn.execute(
            """
            INSERT INTO raw_group_authorizations
                (account_fingerprint,canonical_group_id,display_name,
                 contact_generation,enabled,created_at,updated_at)
            VALUES ('0123456789ab','allowed@chatroom','允许群','v1',1,1,1)
            """
        )
    config = SimpleNamespace(
        db_path=path,
        groups=["允许群", "blocked@chatroom"],
        raw_wechat_enabled=True,
        raw_wechat_account="0123456789ab",
        summary_sync_grace_seconds=0,
        member_kb_chunk_chars=24_000,
        member_kb_retries=1,
        member_kb_max_concurrency=1,
    )
    scheduler = MemberKnowledgeScheduler(
        db_path=path,
        llm_factory=lambda: FakeLLM({
            "profile": {"identity": "ok"},
            "claims": [{
                "section": "identity", "claim_text": "authorized member",
                "basis": "observed", "confidence": 0.8, "sensitive": False,
                "evidence_ids": [allowed_msg],
            }],
        }),
        settings_like=config,
        max_workers=1,
    )
    try:
        assert scheduler.maybe_submit(3_600)["submitted"] == 1
    finally:
        scheduler.close()
    with get_conn(path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM member_profiles WHERE group_id='allowed@chatroom'"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM member_profiles WHERE group_id='blocked@chatroom'"
        ).fetchone()[0] == 0
