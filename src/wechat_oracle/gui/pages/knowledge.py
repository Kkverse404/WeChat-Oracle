"""知识库管理页面：群记忆、成员建档进度。"""

from __future__ import annotations

import sqlite3

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ...agent.memory import get_group_memory, get_persona_drift
from ...config import settings
from ...db import get_conn, transaction
from ...member_knowledge import list_member_profiles
from ..widgets import FormSection
from .base import BasePage


class KnowledgePage(BasePage):
    def __init__(self):
        super().__init__(
            "知识库管理",
            "群记忆（group_memory / persona_drift）与分群成员知识进度。此页读取数据库展示。",
        )
        self._group_selector: QComboBox | None = None
        self._memory_edit: QPlainTextEdit | None = None
        self._profile_table: QTableWidget | None = None

    def _build(self, body: QVBoxLayout) -> None:
        memory = FormSection("群记忆（只读预览）")
        self._group_selector = QComboBox()
        self._group_selector.currentTextChanged.connect(lambda _: self._load_memory())
        memory.add_row("群", self._group_selector)
        self._memory_edit = QPlainTextEdit()
        self._memory_edit.setReadOnly(True)
        self._memory_edit.setMaximumHeight(220)
        memory.add_row("", self._memory_edit)
        body.addWidget(memory)

        profiles = FormSection("成员建档进度")
        self._profile_table = QTableWidget(0, 4)
        self._profile_table.setHorizontalHeaderLabels(["wxid", "显示名", "摘要", "结论"])
        self._profile_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._profile_table.horizontalHeader().setStretchLastSection(True)
        profiles.add_row("", self._profile_table)
        body.addWidget(profiles)

        bar = QHBoxLayout()
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh)
        bar.addWidget(refresh)
        bar.addStretch(1)
        body.addLayout(bar)

        self.refresh()

    def _group_ids(self) -> list[str]:
        try:
            with get_conn() as conn:
                rows = conn.execute(
                    "SELECT DISTINCT group_id FROM messages ORDER BY group_id"
                ).fetchall()
                return [str(row["group_id"]) for row in rows]
        except sqlite3.Error:
            return []

    def _load_memory(self) -> None:
        if self._group_selector is None or self._memory_edit is None:
            return
        group_id = self._group_selector.currentText()
        if not group_id:
            self._memory_edit.setPlainText("")
            return
        try:
            with get_conn() as conn:
                memory_text = get_group_memory(conn, group_id) or ""
                drift_text = get_persona_drift(conn, group_id) or ""
        except sqlite3.Error:
            self._memory_edit.setPlainText("（数据库不可读）")
            return
        block = memory_text.strip()
        if drift_text.strip():
            block = f"{block}\n\n[persona_drift]\n{drift_text.strip()}" if block else f"[persona_drift]\n{drift_text.strip()}"
        self._memory_edit.setPlainText(block or "（空）")

    def _load_profiles(self) -> None:
        if self._profile_table is None:
            return
        rows: list[tuple[str, str, str, str]] = []
        try:
            with get_conn() as conn:
                for group_id in self._group_ids():
                    for profile in list_member_profiles(conn, group_id):
                        summary = str(profile.get("summary_text") or "").replace("\n", " ")[:60]
                        claims = str(profile.get("claims_count") or 0)
                        rows.append(
                            (
                                str(profile.get("sender_wxid") or ""),
                                str(profile.get("display_name") or ""),
                                summary,
                                f"{claims} 条结论",
                            )
                        )
        except (sqlite3.Error, Exception):
            pass
        self._profile_table.setRowCount(len(rows))
        for index, values in enumerate(rows):
            for col, value in enumerate(values):
                self._profile_table.setItem(index, col, QTableWidgetItem(value))

    def _collect_updates(self) -> dict[str, str]:
        return {}

    def refresh(self) -> None:
        if self._group_selector is not None:
            current = self._group_selector.currentText()
            self._group_selector.blockSignals(True)
            self._group_selector.clear()
            self._group_selector.addItems(self._group_ids())
            index = self._group_selector.findText(current)
            self._group_selector.setCurrentIndex(index if index >= 0 else 0)
            self._group_selector.blockSignals(False)
        self._load_memory()
        self._load_profiles()