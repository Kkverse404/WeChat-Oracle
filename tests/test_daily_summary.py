from datetime import datetime
from pathlib import Path
import time
from zoneinfo import ZoneInfo

from wechat_oracle.config import settings
from wechat_oracle.cli import _explicit_summary_period
from wechat_oracle.daily_summary import (
    latest_active_hour,
    deliver_manual_text,
    recover_pending_deliveries,
    reset_failed_summary_period,
    run_daily_group,
    run_summary_group,
    split_message,
)
from wechat_oracle.db import get_conn, init_db
from wechat_oracle.time_ranges import SummaryPeriod


TZ = ZoneInfo("Asia/Hong_Kong")
NOW = datetime(2026, 8, 11, 0, 5, tzinfo=TZ)


class FakeLLM:
    name = "fake"

    def complete_text(self, **kwargs):
        return "重点：大家完成了测试。"


def test_explicit_summary_period_uses_configured_timezone(monkeypatch) -> None:
    monkeypatch.setattr(settings, "summary_timezone", "Asia/Hong_Kong")
    period = _explicit_summary_period("2026-08-12T08:00", "2026-08-12T09:00")
    assert period.kind == "hourly"
    assert datetime.fromtimestamp(period.start_t, TZ).hour == 8
    assert datetime.fromtimestamp(period.end_t, TZ).hour == 9
    assert "2026-08-12 08:00+0800" in period.label


def test_manual_card_uses_outbox_without_summary_header(tmp_path: Path) -> None:
    db_path = tmp_path / "manual-card.db"
    init_db(db_path)
    replier = FakeReplier()
    with get_conn(db_path) as conn:
        result = deliver_manual_text(
            conn,
            group_id="g1",
            group_name="group one",
            text="#成员画像（Alice）\n\n画像正文",
            replier=replier,
            require_current_authorization=False,
            sleep=lambda _: None,
        )
        status = conn.execute(
            "SELECT sr.status,o.status FROM summary_runs sr JOIN delivery_outbox o ON o.summary_run_id=sr.run_id"
        ).fetchone()
    assert result == "sent"
    assert tuple(status) == ("sent", "sent")
    assert replier.sent[0][2].startswith("#成员画像（Alice）")
    assert "#群聊话题" not in replier.sent[0][2]


def test_manual_card_marker_is_restart_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "manual-marker.db"
    init_db(db_path)
    replier = FakeReplier()
    with get_conn(db_path) as conn:
        first = deliver_manual_text(
            conn, group_id="g1", group_name="group one", text="card",
            replier=replier, require_current_authorization=False,
            delivery_marker=-123, sleep=lambda _: None,
        )
        second = deliver_manual_text(
            conn, group_id="g1", group_name="group one", text="card",
            replier=replier, require_current_authorization=False,
            delivery_marker=-123, sleep=lambda _: None,
        )
        assert conn.execute("SELECT COUNT(*) FROM summary_runs").fetchone()[0] == 1
    assert first == "sent"
    assert second == "duplicate"
    assert len(replier.sent) == 1


class ExplodingLLM:
    def complete_text(self, **kwargs):
        raise AssertionError("an existing ready summary must not be regenerated")


class AttributionRetryLLM:
    name = "fake"

    def __init__(self, corrected: bool = True):
        self.calls = 0
        self.corrected = corrected

    def complete_text(self, **kwargs):
        self.calls += 1
        if self.calls == 1 or not self.corrected:
            return "群友说今天把测试搞定了。"
        return "小明把测试收尾，还顺手给 bug 盖上了小被子 🐛"


class FakeReplier:
    def __init__(self, error=None):
        self.sent = []
        self.error = error

    def send(self, group_name, requester, text):
        self.sent.append((group_name, requester, text))
        if self.error:
            raise self.error

    def disconnect(self):
        pass


def _seed(conn) -> None:
    start = int(datetime(2026, 8, 10, 9, 0, tzinfo=TZ).timestamp())
    for i in range(5):
        conn.execute(
            """
            INSERT INTO messages
                (group_id, group_name, sender_display, t, type, content_text,
                 source, dedupe_key)
            VALUES ('g1', '人心黄黄', '群友', ?, 'text', ?, 'live', ?)
            """,
            (start + i, f"消息{i}", f"daily-{i}"),
        )


def _insert_run(conn, status: str, summary_text: str | None = None) -> int:
    period_start = int(datetime(2026, 8, 10, 0, 0, tzinfo=TZ).timestamp())
    period_end = int(datetime(2026, 8, 11, 0, 0, tzinfo=TZ).timestamp())
    cur = conn.execute(
        """
        INSERT INTO summary_runs
            (group_id, group_name, period_start, period_end, trigger_kind,
             status, summary_text, started_at)
        VALUES ('g1', 'group one', ?, ?, 'daily', ?, ?, 1.0)
        """,
        (period_start, period_end, status, summary_text),
    )
    return int(cur.lastrowid)


def _seed_named_period(conn, period: SummaryPeriod) -> None:
    for index in range(5):
        conn.execute(
            """
            INSERT INTO messages
                (group_id,group_name,sender_wxid,sender_display,t,type,
                 content_text,source,dedupe_key)
            VALUES ('named','命名群','wx-a','小明',?,'text',?,'live',?)
            """,
            (period.start_t + index, f"测试消息{index}", f"named-{index}"),
        )


def test_named_summary_retries_generic_attribution_before_sending(tmp_path: Path) -> None:
    db_path = tmp_path / "attribution.db"
    init_db(db_path)
    period = SummaryPeriod("hourly", 100, 200, "test")
    llm = AttributionRetryLLM()
    replier = FakeReplier()
    with get_conn(db_path) as conn:
        _seed_named_period(conn, period)
        result = run_summary_group(
            conn,
            group_id="named",
            group_name="命名群",
            period=period,
            min_messages=5,
            replier=replier,
            llm=llm,
            sleep=lambda _: None,
        )
    assert result == "sent"
    assert llm.calls == 2
    assert "小明" in replier.sent[0][2]


def test_named_summary_is_not_sent_when_retry_stays_generic(tmp_path: Path) -> None:
    db_path = tmp_path / "attribution-fail.db"
    init_db(db_path)
    period = SummaryPeriod("hourly", 100, 200, "test")
    llm = AttributionRetryLLM(corrected=False)
    replier = FakeReplier()
    with get_conn(db_path) as conn:
        _seed_named_period(conn, period)
        result = run_summary_group(
            conn,
            group_id="named",
            group_name="命名群",
            period=period,
            min_messages=5,
            replier=replier,
            llm=llm,
            sleep=lambda _: None,
        )
        status = conn.execute("SELECT status FROM summary_runs").fetchone()[0]
    assert result == "failed"
    assert status == "failed"
    assert llm.calls == 2
    assert replier.sent == []


def test_latest_active_hour_skips_sparse_newer_bucket(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "active.db"
    init_db(db_path)
    monkeypatch.setattr(settings, "bot_name", "")
    start = int(datetime(2026, 8, 11, 23, 0, tzinfo=TZ).timestamp())
    with get_conn(db_path) as conn:
        for index in range(5):
            conn.execute(
                """
                INSERT INTO messages
                    (group_id,group_name,t,type,content_text,source,dedupe_key)
                VALUES ('g1','group one',?,'text',?,'live',?)
                """,
                (start + index, f"active-{index}", f"active-{index}"),
            )
        conn.execute(
            """
            INSERT INTO messages
                (group_id,group_name,t,type,content_text,source,dedupe_key)
            VALUES ('g1','group one',?,'text','sparse','live','sparse')
            """,
            (start + 3600,),
        )
        period = latest_active_hour(
            conn,
            group_id="g1",
            min_messages=5,
            now=datetime(2026, 8, 12, 9, 0, tzinfo=TZ),
        )
    assert period is not None
    assert (period.start_t, period.end_t) == (start, start + 3600)


def test_failed_unsent_period_requires_explicit_safe_reset(tmp_path: Path) -> None:
    db_path = tmp_path / "failed.db"
    init_db(db_path)
    period = SummaryPeriod("daily", 1, 2, "test")
    with get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO summary_runs
                (group_id,period_start,period_end,trigger_kind,status,started_at)
            VALUES ('g1',1,2,'daily','failed',1)
            """
        )
        assert reset_failed_summary_period(conn, group_id="g1", period=period)
        row = conn.execute(
            "SELECT status, generation_attempt_count FROM summary_runs"
        ).fetchone()
        assert tuple(row) == ("running", 1)


def test_daily_summary_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    monkeypatch.setattr(settings, "daily_summary_min_messages", 5)
    monkeypatch.setattr(settings, "daily_summary_chunk_chars", 800)
    monkeypatch.setattr(settings, "bot_name", "小助理")
    replier = FakeReplier()
    with get_conn(db_path) as conn:
        _seed(conn)
        assert run_daily_group(
            conn, group_id="g1", group_name="人心黄黄", replier=replier,
            llm=FakeLLM(), now=NOW, sleep=lambda _: None,
        ) == "sent"
        assert run_daily_group(
            conn, group_id="g1", group_name="人心黄黄", replier=replier,
            llm=FakeLLM(), now=NOW, sleep=lambda _: None,
        ) == "duplicate"
        assert conn.execute("SELECT status FROM summary_runs").fetchone()[0] == "sent"
        assert conn.execute("SELECT COUNT(*) FROM delivery_outbox").fetchone()[0] == 1
    assert len(replier.sent) == 1
    assert replier.sent[0][2].startswith("#过去一天话题")


def test_ambiguous_send_is_not_retryable(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    monkeypatch.setattr(settings, "daily_summary_min_messages", 5)
    monkeypatch.setattr(settings, "bot_name", "小助理")
    replier = FakeReplier(RuntimeError("window changed"))
    with get_conn(db_path) as conn:
        _seed(conn)
        assert run_daily_group(
            conn, group_id="g1", group_name="人心黄黄", replier=replier,
            llm=FakeLLM(), now=NOW, sleep=lambda _: None,
        ) == "unknown"
        assert run_daily_group(
            conn, group_id="g1", group_name="人心黄黄", replier=replier,
            llm=FakeLLM(), now=NOW, sleep=lambda _: None,
        ) == "duplicate"
        assert conn.execute("SELECT status FROM delivery_outbox").fetchone()[0] == "unknown"


def test_ready_pending_delivery_resumes_without_regeneration(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    monkeypatch.setattr(settings, "daily_summary_chunk_chars", 800)
    replier = FakeReplier()
    with get_conn(db_path) as conn:
        run_id = _insert_run(conn, "ready", "stored summary")
        conn.execute(
            "INSERT INTO delivery_outbox (summary_run_id, status, created_at, updated_at) "
            "VALUES (?, 'pending', 1.0, 1.0)",
            (run_id,),
        )

        assert run_daily_group(
            conn, group_id="g1", group_name="group one", replier=replier,
            llm=ExplodingLLM(), now=NOW, sleep=lambda _: None,
        ) == "sent"
        outbox = conn.execute(
            "SELECT status, attempt_count FROM delivery_outbox WHERE summary_run_id=?",
            (run_id,),
        ).fetchone()
        assert tuple(outbox) == ("sent", 1)
    assert len(replier.sent) == 1
    assert "stored summary" in replier.sent[0][2]


def test_ready_without_outbox_recreates_pending_and_resumes(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    monkeypatch.setattr(settings, "daily_summary_chunk_chars", 800)
    replier = FakeReplier()
    with get_conn(db_path) as conn:
        run_id = _insert_run(conn, "ready", "stored summary")

        assert run_daily_group(
            conn, group_id="g1", group_name="group one", replier=replier,
            llm=ExplodingLLM(), now=NOW, sleep=lambda _: None,
        ) == "sent"
        outbox = conn.execute(
            "SELECT status, attempt_count FROM delivery_outbox WHERE summary_run_id=?",
            (run_id,),
        ).fetchone()
        assert tuple(outbox) == ("sent", 1)
    assert len(replier.sent) == 1


def test_interrupted_sending_becomes_unknown_without_retry(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    replier = FakeReplier()
    with get_conn(db_path) as conn:
        run_id = _insert_run(conn, "ready", "stored summary")
        conn.execute(
            "INSERT INTO delivery_outbox "
            "(summary_run_id, status, attempt_count, created_at, updated_at) "
            "VALUES (?, 'sending', 1, 1.0, 1.0)",
            (run_id,),
        )

        assert run_daily_group(
            conn, group_id="g1", group_name="group one", replier=replier,
            llm=ExplodingLLM(), now=NOW, sleep=lambda _: None,
        ) == "unknown"
        statuses = conn.execute(
            """
            SELECT summary_runs.status, delivery_outbox.status, delivery_outbox.attempt_count
              FROM summary_runs JOIN delivery_outbox
                ON delivery_outbox.summary_run_id=summary_runs.run_id
             WHERE summary_runs.run_id=?
            """,
            (run_id,),
        ).fetchone()
        assert tuple(statuses) == ("unknown", "unknown", 1)
    assert replier.sent == []


def test_unknown_outbox_is_never_retried(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    replier = FakeReplier()
    with get_conn(db_path) as conn:
        run_id = _insert_run(conn, "ready", "stored summary")
        conn.execute(
            "INSERT INTO delivery_outbox "
            "(summary_run_id, status, attempt_count, created_at, updated_at) "
            "VALUES (?, 'unknown', 1, 1.0, 1.0)",
            (run_id,),
        )

        assert run_daily_group(
            conn, group_id="g1", group_name="group one", replier=replier,
            llm=ExplodingLLM(), now=NOW, sleep=lambda _: None,
        ) == "unknown"
        assert run_daily_group(
            conn, group_id="g1", group_name="group one", replier=replier,
            llm=ExplodingLLM(), now=NOW, sleep=lambda _: None,
        ) == "duplicate"
        outbox = conn.execute(
            "SELECT status, attempt_count FROM delivery_outbox WHERE summary_run_id=?",
            (run_id,),
        ).fetchone()
        assert tuple(outbox) == ("unknown", 1)
    assert replier.sent == []


def test_recovery_does_not_send_after_raw_authorization_is_revoked(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "revoked.db"
    init_db(db_path)
    monkeypatch.setattr(settings, "groups", ["g1"])
    monkeypatch.setattr(settings, "reply_allowed_groups", ["group one"])
    monkeypatch.setattr(settings, "raw_wechat_enabled", True)
    monkeypatch.setattr(settings, "raw_wechat_account", "0123456789ab")
    replier = FakeReplier()
    with get_conn(db_path) as conn:
        run_id = _insert_run(conn, "ready", "stored summary")
        conn.execute(
            "INSERT INTO delivery_outbox (summary_run_id, status, created_at, updated_at) "
            "VALUES (?, 'pending', 1, 1)",
            (run_id,),
        )
        conn.execute(
            """
            INSERT INTO raw_group_authorizations
                (account_fingerprint, canonical_group_id, display_name,
                 contact_generation, enabled, created_at, updated_at)
            VALUES (?, 'g1', 'group one', 'v1', 0, 1, 1)
            """,
            (settings.raw_wechat_account,),
        )
        assert recover_pending_deliveries(conn, replier=replier, sleep=lambda _: None) == 0
        assert replier.sent == []
        assert conn.execute("SELECT status FROM delivery_outbox").fetchone()[0] == "pending"


def test_terminal_runs_are_noops(tmp_path: Path) -> None:
    for status in ("sent", "skipped", "failed", "unknown"):
        db_path = tmp_path / f"{status}.db"
        init_db(db_path)
        replier = FakeReplier()
        with get_conn(db_path) as conn:
            _insert_run(conn, status, "stored summary")
            assert run_daily_group(
                conn, group_id="g1", group_name="group one", replier=replier,
                llm=ExplodingLLM(), now=NOW, sleep=lambda _: None,
            ) == "duplicate"
            assert conn.execute("SELECT status FROM summary_runs").fetchone()[0] == status
        assert replier.sent == []


def test_stale_running_generation_is_recovered(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "stale.db"
    init_db(db_path)
    monkeypatch.setattr(settings, "daily_summary_min_messages", 5)
    replier = FakeReplier()
    with get_conn(db_path) as conn:
        _seed(conn)
        _insert_run(conn, "running")
        assert run_daily_group(
            conn, group_id="g1", group_name="group one", replier=replier,
            llm=FakeLLM(), now=NOW, sleep=lambda _: None,
        ) == "sent"
        row = conn.execute(
            "SELECT status, generation_attempt_count FROM summary_runs"
        ).fetchone()
        assert tuple(row) == ("sent", 2)


def test_fresh_running_generation_is_not_stolen(tmp_path: Path) -> None:
    db_path = tmp_path / "fresh.db"
    init_db(db_path)
    with get_conn(db_path) as conn:
        run_id = _insert_run(conn, "running")
        conn.execute(
            "UPDATE summary_runs SET lease_token='owner', lease_until=? WHERE run_id=?",
            (time.time() + 300, run_id),
        )
        assert run_daily_group(
            conn, group_id="g1", group_name="group one", replier=FakeReplier(),
            llm=ExplodingLLM(), now=NOW, sleep=lambda _: None,
        ) == "duplicate"


def test_fresh_concurrent_sender_is_left_in_progress(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    with get_conn(db_path) as seed_conn:
        _seed(seed_conn)

    class ConcurrentReplier(FakeReplier):
        def send(self, group_name, sender_display, text):
            super().send(group_name, sender_display, text)
            with get_conn(db_path) as other:
                assert run_daily_group(
                    other, group_id="g1", group_name="group one", replier=FakeReplier(),
                    llm=ExplodingLLM(), now=NOW, sleep=lambda _: None,
                ) == "in_progress"

    replier = ConcurrentReplier()
    with get_conn(db_path) as conn:
        assert run_daily_group(
            conn, group_id="g1", group_name="group one", replier=replier,
            llm=FakeLLM(), now=NOW, sleep=lambda _: None,
        ) == "sent"
        statuses = conn.execute(
            "SELECT summary_runs.status, delivery_outbox.status "
            "FROM summary_runs JOIN delivery_outbox ON delivery_outbox.summary_run_id=summary_runs.run_id"
        ).fetchone()
        assert tuple(statuses) == ("sent", "sent")
    assert len(replier.sent) == 1


def test_hourly_summary_uses_requested_period_and_header(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "hourly.db"
    init_db(db_path)
    monkeypatch.setattr(settings, "bot_name", "assistant")
    period = SummaryPeriod(
        "hourly",
        int(datetime(2026, 8, 10, 9, 0, tzinfo=TZ).timestamp()),
        int(datetime(2026, 8, 10, 10, 0, tzinfo=TZ).timestamp()),
        "2026-08-10 09:00+0800 - 10:00+0800",
    )
    replier = FakeReplier()
    with get_conn(db_path) as conn:
        _seed(conn)
        assert run_summary_group(
            conn,
            group_id="g1",
            group_name="group one",
            period=period,
            min_messages=5,
            replier=replier,
            llm=FakeLLM(),
            sleep=lambda _: None,
        ) == "sent"
    assert replier.sent[0][2].startswith("#过去一小时话题")


def test_split_message_hard_cap() -> None:
    parts = split_message("甲" * 1701, 800)
    assert [len(part) for part in parts] == [800, 800, 101]
