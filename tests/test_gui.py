from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402


@pytest.fixture()
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def gui_env(qapp, monkeypatch):
    from wechat_oracle.config import settings
    from wechat_oracle.gui.runtime import SupervisorBridge

    monkeypatch.setattr(settings, "groups", ["测试群"])
    monkeypatch.setattr(settings, "bot_name", "Chris.")
    monkeypatch.setattr(
        "wechat_oracle.gui.actions.list_send_groups", lambda: [("123@chatroom", "测试群")]
    )
    monkeypatch.setattr(
        "wechat_oracle.gui.actions.broadcast_progress",
        lambda: {"campaign": None, "counts": {}},
    )
    yield SupervisorBridge()


def test_gui_pages_build_and_collect(gui_env) -> None:
    from wechat_oracle.gui.app import MainWindow
    from wechat_oracle.gui.pages import (
        IngestPage,
        ModelsPage,
        ReplyPage,
        SettingsPage,
    )

    window = MainWindow()
    assert window._stack.count() == 6

    ingest = IngestPage()
    updates = ingest._collect_updates()
    assert updates["WO_GROUPS"] == '["测试群"]'
    assert updates["WO_INGEST_BACKEND"] in {"weflow", "wx4py"}

    reply = ReplyPage()
    assert isinstance(reply, QWidget)

    models = ModelsPage()
    assert models._collect_updates()["WO_LLM_ENDPOINT"]

    settings_page = SettingsPage()
    assert settings_page._collect_updates()["WO_DB_PATH"]


def test_supervisor_bridge_starts_idle(gui_env) -> None:
    assert gui_env.status_rows() == []


def test_write_env_updates_is_atomic(tmp_path) -> None:
    env = tmp_path / ".env"
    env.write_text("WO_LLM_MODEL=old\n# keep me\n", encoding="utf-8")
    from wechat_oracle.config_store import write_env_updates

    write_env_updates({"WO_LLM_MODEL": "new", "WO_GROUPS": '["g"]'}, env_path=env)
    text = env.read_text(encoding="utf-8")
    assert "WO_LLM_MODEL=new" in text
    assert "# keep me" in text
    assert 'WO_GROUPS=["g"]' in text

    with pytest.raises(ValueError):
        write_env_updates({"WO_LLM_MODEL": "bad\nvalue"}, env_path=env)