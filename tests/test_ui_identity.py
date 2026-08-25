from pathlib import Path

from wechat_oracle.db import get_conn, init_db
from wechat_oracle.ingest.ui_identity import (
    load_ui_roster,
    match_roster_name,
    save_ui_roster,
    sender_label_texts,
    stable_ui_member_id,
)


def test_ui_member_id_is_group_scoped_and_stable() -> None:
    assert stable_ui_member_id("g1", "Alice") == stable_ui_member_id("g1", " Alice ")
    assert stable_ui_member_id("g1", "yea") == stable_ui_member_id("g1", "\x7fyea")
    assert stable_ui_member_id("g1", "Alice") != stable_ui_member_id("g2", "Alice")
    assert stable_ui_member_id("g1", "Alice").startswith("ui-member:")


def test_roster_match_requires_one_confident_candidate() -> None:
    assert match_roster_name(["Young.Da", "hello"], ["Young.Da", "可乐"])[0] == "Young.Da"
    assert match_roster_name(["unrelated content"], ["Young.Da", "可乐"])[0] is None
    assert match_roster_name(["Alic"], ["Alice", "Alicf"])[0] is None


def test_roster_sync_deactivates_missing_names(tmp_path: Path) -> None:
    db_path = tmp_path / "roster.db"
    init_db(db_path)
    with get_conn(db_path) as conn:
        save_ui_roster(conn, "g", ["Alice", "Bob"])
        assert load_ui_roster(conn, "g") == ["Alice", "Bob"]
        assert conn.execute(
            "SELECT COUNT(*) FROM member_profiles WHERE group_id='g'"
        ).fetchone()[0] == 2
        save_ui_roster(conn, "g", ["Bob", "Carol"])
        assert load_ui_roster(conn, "g") == ["Bob", "Carol"]


def test_only_top_ocr_row_can_identify_sender() -> None:
    result = [
        ([[0, 2], [40, 2], [40, 12], [0, 12]], "Alice", 0.99),
        ([[0, 30], [80, 30], [80, 45], [0, 45]], "Bob 说得对", 0.99),
    ]
    assert sender_label_texts(result) == ["Alice"]
    assert match_roster_name(sender_label_texts(result), ["Alice", "Bob"])[0] == "Alice"
    assert sender_label_texts(result[1:]) == []
