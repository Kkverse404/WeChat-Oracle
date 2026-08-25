"""首页 Dashboard：进程启停控制、真实状态灯、心跳与日志流。"""

from __future__ import annotations

import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ...config import settings
from ...db import get_conn
from ..runtime import SupervisorBridge, heartbeat_summary
from ..widgets import status_label
from ..workers import run_async
from .base import BasePage

_PROCESS_LABELS = {
    "live": "采集（ingest）",
    "dispatcher": "dispatcher",
    "raw-sync": "raw 本机同步",
}


class DashboardPage(BasePage):
    def __init__(self, bridge: SupervisorBridge | None = None):
        self._bridge = bridge
        self._status_table: QTableWidget | None = None
        self._groups_table: QTableWidget | None = None
        self._audit_table: QTableWidget | None = None
        self._log_view: QPlainTextEdit | None = None
        self._light_labels: dict[str, QLabel] = {}
        super().__init__("首页", "进程控制、真实状态、各群数据、最近发送审计。", savable=False)

    def _build(self, body: QVBoxLayout) -> None:
        control = QGridLayout()
        start_all = QPushButton("全部启动")
        stop_all = QPushButton("全部停止")
        start_all.clicked.connect(self._on_start_all)
        stop_all.clicked.connect(self._on_stop_all)
        control.addWidget(start_all, 0, 0)
        control.addWidget(stop_all, 0, 1)
        for column, name in enumerate(("live", "dispatcher"), start=2):
            button = QPushButton(f"重启 {_PROCESS_LABELS[name]}")
            button.clicked.connect(lambda _=False, n=name: run_async(
                self, lambda n=name: self._bridge.restart(n) if self._bridge else None,
                label=f"重启 {name}",
            ))
            control.addWidget(button, 0, column)
        body.addLayout(control)

        body.addWidget(_section("进程状态（真实）"))
        grid = QGridLayout()
        row = 0
        for name in ("live", "dispatcher", "raw-sync"):
            label = QLabel("未启动")
            self._light_labels[name] = label
            grid.addWidget(QLabel(f"{_PROCESS_LABELS[name]}："), row, 0)
            grid.addWidget(label, row, 1)
            row += 1
        heartbeat_label = QLabel("无活动记录")
        self._light_labels["heartbeat"] = heartbeat_label
        grid.addWidget(QLabel("事件心跳："), row, 0)
        grid.addWidget(heartbeat_label, row, 1)
        body.addLayout(grid)

        refresh_button = QPushButton("刷新")
        refresh_button.clicked.connect(self.refresh)
        body.addWidget(refresh_button)

        if self._bridge is not None:
            body.addWidget(_section("运行日志"))
            self._log_view = QPlainTextEdit()
            self._log_view.setReadOnly(True)
            self._log_view.setMaximumHeight(220)
            self._log_view.setMaximumBlockCount(800)
            self._log_view.setPlaceholderText("启动后，子进程输出会滚动显示在这里。")
            body.addWidget(self._log_view)
            self._bridge.log_received.connect(self._on_log_line)

        body.addWidget(_section("各群数据"))
        self._groups_table = QTableWidget(0, 3)
        self._groups_table.setHorizontalHeaderLabels(["群", "消息数", "member-kb 完成度"])
        self._groups_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._groups_table.horizontalHeader().setStretchLastSection(True)
        body.addWidget(self._groups_table)

        body.addWidget(_section("最近发送审计"))
        self._audit_table = QTableWidget(0, 4)
        self._audit_table.setHorizontalHeaderLabels(["类型", "群", "时间", "状态"])
        self._audit_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._audit_table.horizontalHeader().setStretchLastSection(True)
        body.addWidget(self._audit_table)

        # 进程行变化不会主动推信号；用低频定时器刷新灯。
        from PySide6.QtCore import QTimer

        timer = QTimer(self)
        timer.setInterval(3000)
        timer.timeout.connect(self._refresh_lights)
        timer.start()
        self._refresh_lights()

    # --- 控制动作 ---
    def _on_start_all(self) -> None:
        if self._bridge is None:
            return
        run_async(self, self._bridge.start_all, label="启动")

    def _on_stop_all(self) -> None:
        if self._bridge is None:
            return
        run_async(self, self._bridge.stop_all, label="停止")

    def _on_log_line(self, name: str, text: str) -> None:
        if self._log_view is None:
            return
        stamp = _short_stamp()
        prefix = {"live": "INGEST", "dispatcher": "DISPATCH", "supervisor": "SYSTEM"}.get(name, name.upper())
        self._log_view.appendPlainText(f"{stamp} {prefix} | {text}")

    def _refresh_lights(self) -> None:
        rows: dict[str, tuple[int | None, int | None]] = {}
        if self._bridge is not None:
            rows = {name: (pid, code) for name, pid, code in self._bridge.status_rows()}
        for name, label in self._light_labels.items():
            if name == "heartbeat":
                continue
            pid, code = rows.get(name, (None, None))
            alive = pid is not None and code is None
            expected = name != "raw-sync" or bool(settings.raw_wechat_enabled)
            if not expected:
                label.setText("未启用")
                label.setStyleSheet("color:#888888;")
            elif alive:
                label.setText(f"运行中 (pid {pid})")
                label.setStyleSheet("color:#8bdc7f;")
            else:
                label.setText("已停止")
                label.setStyleSheet("color:#ff6b6b;")
        hb = heartbeat_summary(settings.data_dir)
        label = self._light_labels.get("heartbeat")
        if label is not None:
            age = hb["latest_age_seconds"]
            if age is None:
                label.setText("无活动记录")
                label.setStyleSheet("color:#888888;")
            else:
                text = f"{hb['latest_event'] or '-'} · {int(age)}s 前 · 近5分钟 {hb['recent_5min']} 条"
                style = "color:#8bdc7f;" if hb["recent_5min"] else "color:#f0c674;"
                label.setText(text)
                label.setStyleSheet(style)

    # --- 数据加载 ---
    def _load_groups(self) -> None:
        if self._groups_table is None:
            return
        counts: dict[str, int] = {}
        complete: dict[str, int] = {}
        total: dict[str, int] = {}
        try:
            with get_conn() as conn:
                for row in conn.execute(
                    "SELECT group_id, COUNT(*) n FROM messages GROUP BY group_id ORDER BY n DESC"
                ):
                    counts[str(row["group_id"])] = int(row["n"])
                for row in conn.execute(
                    "SELECT group_id, full_history_complete, COUNT(*) n FROM member_update_state "
                    "GROUP BY group_id, full_history_complete"
                ):
                    key = str(row["group_id"])
                    if int(row["full_history_complete"]):
                        complete[key] = complete.get(key, 0) + int(row["n"])
                    total[key] = total.get(key, 0) + int(row["n"])
        except sqlite3.Error:
            pass
        self._groups_table.setRowCount(len(counts))
        for index, (group_id, count) in enumerate(counts.items()):
            done = complete.get(group_id, 0)
            allc = total.get(group_id, 0)
            percent = f"{done}/{allc}" if allc else "—"
            self._groups_table.setItem(index, 0, QTableWidgetItem(group_id))
            self._groups_table.setItem(index, 1, QTableWidgetItem(str(count)))
            self._groups_table.setItem(index, 2, QTableWidgetItem(percent))

    def _load_audit(self) -> None:
        if self._audit_table is None:
            return
        rows: list[tuple[str, str, str, str]] = []
        try:
            with get_conn() as conn:
                for row in conn.execute(
                    """
                    SELECT '摘要' kind, group_name, datetime(finished_at, 'unixepoch', 'localtime') at, status
                      FROM summary_runs
                     ORDER BY finished_at DESC LIMIT 6
                    """
                ):
                    rows.append((str(row["kind"]), str(row["group_name"]), str(row["at"]), str(row["status"])))
                for row in conn.execute(
                    """
                    SELECT '画像' kind, group_name, datetime(updated_at, 'unixepoch', 'localtime') at, status
                      FROM member_profile_broadcasts
                     ORDER BY updated_at DESC LIMIT 3
                    """
                ):
                    rows.append((str(row["kind"]), str(row["group_name"]), str(row["at"]), str(row["status"])))
        except sqlite3.Error:
            pass
        self._audit_table.setRowCount(len(rows))
        for index, values in enumerate(rows):
            for col, value in enumerate(values):
                self._audit_table.setItem(index, col, QTableWidgetItem(value))

    def refresh(self) -> None:
        self._load_groups()
        self._load_audit()


def _section(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet("font-weight:bold;color:#5ccfe6;")
    return label


def _short_stamp() -> str:
    from datetime import datetime

    return datetime.now().strftime("%H:%M:%S")