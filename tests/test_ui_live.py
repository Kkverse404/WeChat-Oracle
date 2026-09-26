from datetime import datetime

from wechat_oracle.config import settings
from wechat_oracle.ingest.ui_live import _history_timestamp, run_ui_live, ui_group_id


def test_ui_group_id_is_stable_and_namespaced() -> None:
    assert ui_group_id("人心黄黄") == ui_group_id("人心黄黄")
    assert ui_group_id("人心黄黄").startswith("ui:")
    assert ui_group_id("另一个群") != ui_group_id("人心黄黄")


def test_history_timestamp_understands_yesterday() -> None:
    now = datetime(2026, 8, 11, 15, 30)
    value = _history_timestamp("昨天 23:15", now)
    assert datetime.fromtimestamp(value) == datetime(2026, 8, 10, 23, 15)


def test_raw_sync_skips_blocking_visible_history(monkeypatch) -> None:
    calls: list[str] = []

    class FakeClient:
        def connect(self):
            calls.append("connect")

        def disconnect(self):
            calls.append("disconnect")

    class FakeListener:
        def __init__(self, *_args, **_kwargs):
            calls.append("listener")

        def start(self, *, block):
            assert block is True
            calls.append("start")

    monkeypatch.setattr(settings, "groups", ["测试群"])
    monkeypatch.setattr(settings, "raw_wechat_enabled", True)
    monkeypatch.setattr("wechat_oracle.ingest.ui_live.init_db", lambda: None)
    monkeypatch.setattr("wx4py.WeChatClient", FakeClient)
    monkeypatch.setattr(
        "wx4py.features.messaging.listener.WeChatGroupListener", FakeListener
    )
    monkeypatch.setattr(
        "wechat_oracle.ingest.ui_live._backfill_visible_history",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not backfill")),
    )

    run_ui_live()

    assert calls == ["connect", "listener", "start", "disconnect"]
