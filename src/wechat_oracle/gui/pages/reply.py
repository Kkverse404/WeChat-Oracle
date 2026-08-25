"""自动回复管理页面：策略与调度、定时摘要发送、成员画像发送（tab 结构）。"""

from __future__ import annotations

import json

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QLineEdit,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...config import settings
from ..actions import broadcast_progress, broadcast_start, list_send_groups, member_send, summary_send_once
from ..widgets import FormSection, GroupListEditor, check_box, combo_box, float_spin, int_spin, text_edit
from ..workers import run_async
from .base import BasePage


class ReplyStrategyPage(BasePage):
    def __init__(self):
        super().__init__(
            "回复策略与调度",
            "保存后写入 .env；运行中的 dispatcher 需重启后生效。",
        )

    def _build(self, body: QVBoxLayout) -> None:
        main = FormSection("回复")
        self.reply_enabled = check_box(settings.reply)
        main.add_row("启用回复", self.reply_enabled)
        self.reply_backend = combo_box(["uia-direct", "wx4py", "stdout"], settings.reply_backend)
        main.add_row("发送后端", self.reply_backend, "uia-direct=无鼠标 UIA；stdout=仅测试。")
        self.fail_closed = check_box(settings.reply_fail_closed)
        main.add_row("歧义时拒发", self.fail_closed)
        self.bot_name = text_edit(settings.bot_name)
        main.add_row("Bot 群昵称", self.bot_name)
        self.bot_wxid = text_edit(settings.bot_wxid or "")
        main.add_row("Bot wxid", self.bot_wxid)
        body.addWidget(main)

        trigger = FormSection("触发策略")
        self.mention_policy = combo_box(["always", "explicit", "never"], settings.reply_mention_policy)
        trigger.add_row("@ 策略", self.mention_policy)
        self.proactive_mode = combo_box(["off", "reactive", "proactive"], settings.agent_proactive_mode)
        trigger.add_row("主动模式", self.proactive_mode)
        self.base_probability = float_spin(settings.agent_base_probability, 0.0, 1.0, 0.05, 3)
        trigger.add_row("触发概率", self.base_probability)
        self.cooldown = int_spin(settings.agent_cooldown_seconds, 0, 3600, " 秒")
        trigger.add_row("冷却", self.cooldown)
        body.addWidget(trigger)

        allow = FormSection("发送白名单（精确显示名）")
        self.allow_editor = GroupListEditor(list(settings.reply_allowed_groups))
        allow.add_row("", self.allow_editor)
        body.addWidget(allow)

        behavior = FormSection("回复行为")
        self.max_steps = int_spin(settings.agent_max_steps, 1, 20)
        behavior.add_row("Phase A 步数", self.max_steps)
        self.recent_context = int_spin(settings.agent_recent_context_chat, 1, 500)
        behavior.add_row("最近消息窗口", self.recent_context)
        self.continuation = check_box(settings.agent_continuation_enabled)
        behavior.add_row("追问", self.continuation)
        self.max_followups = int_spin(settings.agent_continuation_max_followups, 0, 10)
        behavior.add_row("最多追问", self.max_followups)
        self.delay_seconds = int_spin(settings.agent_continuation_delay_seconds, 5, 3600, " 秒")
        behavior.add_row("追问延迟", self.delay_seconds)
        body.addWidget(behavior)

        summary = FormSection("定时摘要调度")
        self.hourly_enabled = check_box(settings.hourly_summary_enabled)
        summary.add_row("每小时", self.hourly_enabled)
        self.hourly_min = int_spin(settings.hourly_summary_min_messages, 1, 1000)
        summary.add_row("最少消息数", self.hourly_min)
        self.daily_enabled = check_box(settings.daily_summary_enabled)
        summary.add_row("每日", self.daily_enabled)
        self.daily_min = int_spin(settings.daily_summary_min_messages, 1, 1000)
        summary.add_row("最少消息数", self.daily_min)
        self.timezone = text_edit(settings.summary_timezone)
        summary.add_row("时区", self.timezone)
        self.grace = int_spin(settings.summary_sync_grace_seconds, 0, 3600, " 秒")
        summary.add_row("宽限", self.grace)
        body.addWidget(summary)

        member = FormSection("成员知识库与 lurk")
        self.member_kb = check_box(settings.member_kb_enabled)
        member.add_row("member-kb 启用", self.member_kb)
        self.member_interval = int_spin(settings.member_kb_interval_seconds, 300, 86400, " 秒")
        member.add_row("建档间隔", self.member_interval)
        self.lurk_enabled = check_box(settings.agent_lurk_enabled)
        member.add_row("lurk 启用", self.lurk_enabled)
        self.lurk_interval = int_spin(settings.agent_lurk_interval_seconds, 60, 86400, " 秒")
        member.add_row("lurk 扫描间隔", self.lurk_interval)
        body.addWidget(member)

    def _collect_updates(self) -> dict[str, str]:
        return {
            "WO_REPLY": "true" if self.reply_enabled.isChecked() else "false",
            "WO_REPLY_BACKEND": self.reply_backend.currentText(),
            "WO_REPLY_FAIL_CLOSED": "true" if self.fail_closed.isChecked() else "false",
            "WO_BOT_NAME": self.bot_name.text().strip(),
            "WO_BOT_WXID": self.bot_wxid.text().strip(),
            "WO_REPLY_MENTION_POLICY": self.mention_policy.currentText(),
            "WO_AGENT_PROACTIVE_MODE": self.proactive_mode.currentText(),
            "WO_AGENT_BASE_PROBABILITY": f"{self.base_probability.value():g}",
            "WO_AGENT_COOLDOWN_SECONDS": str(self.cooldown.value()),
            "WO_REPLY_ALLOWED_GROUPS": json.dumps(self.allow_editor.values(), ensure_ascii=False),
            "WO_AGENT_MAX_STEPS": str(self.max_steps.value()),
            "WO_AGENT_RECENT_CONTEXT_CHAT": str(self.recent_context.value()),
            "WO_AGENT_CONTINUATION_ENABLED": "true" if self.continuation.isChecked() else "false",
            "WO_AGENT_CONTINUATION_MAX_FOLLOWUPS": str(self.max_followups.value()),
            "WO_AGENT_CONTINUATION_DELAY_SECONDS": str(self.delay_seconds.value()),
            "WO_HOURLY_SUMMARY_ENABLED": "true" if self.hourly_enabled.isChecked() else "false",
            "WO_HOURLY_SUMMARY_MIN_MESSAGES": str(self.hourly_min.value()),
            "WO_DAILY_SUMMARY_ENABLED": "true" if self.daily_enabled.isChecked() else "false",
            "WO_DAILY_SUMMARY_MIN_MESSAGES": str(self.daily_min.value()),
            "WO_SUMMARY_TIMEZONE": self.timezone.text().strip(),
            "WO_SUMMARY_SYNC_GRACE_SECONDS": str(self.grace.value()),
            "WO_MEMBER_KB_ENABLED": "true" if self.member_kb.isChecked() else "false",
            "WO_MEMBER_KB_INTERVAL_SECONDS": str(self.member_interval.value()),
            "WO_AGENT_LURK_ENABLED": "true" if self.lurk_enabled.isChecked() else "false",
            "WO_AGENT_LURK_INTERVAL_SECONDS": str(self.lurk_interval.value()),
        }

    def _validate(self, updates: dict[str, str]) -> None:
        if not updates["WO_BOT_NAME"]:
            raise ValueError("Bot 群昵称不能为空（回复依赖它识别 @）。")
        if updates["WO_REPLY_BACKEND"] not in {"uia-direct", "wx4py", "stdout"}:
            raise ValueError("发送后端只能是 uia-direct、wx4py 或 stdout")
        if updates["WO_REPLY_MENTION_POLICY"] not in {"always", "explicit", "never"}:
            raise ValueError("@ 策略只能是 always、explicit 或 never")


def _group_combo() -> QComboBox:
    combo = QComboBox()
    _reload_group_combo(combo)
    return combo


def _reload_group_combo(combo: QComboBox) -> None:
    current = combo.currentText()
    combo.clear()
    try:
        from ..actions import list_send_groups

        for group_id, name in list_send_groups():
            combo.addItem(f"{name} [{group_id}]", group_id)
    except Exception:
        pass
    if current:
        index = combo.findText(current)
        if index >= 0:
            combo.setCurrentIndex(index)


class SummaryActionsPage(QWidget):
    """一次性真实摘要发送（复用 CLI send-once 语义与幂等 outbox）。"""

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        form = FormSection("一次性摘要发送")
        self.group = _group_combo()
        form.add_row("群", self.group)
        self.period = combo_box(
            ["previous-hour", "latest-active-hour", "previous-day"],
            "previous-hour",
        )
        form.add_row("周期", self.period)
        self.retry_failed = QCheckBox("重试无投递记录的 failed/skipped")
        form.add_row("", self.retry_failed)

        from PySide6.QtWidgets import QHBoxLayout, QPushButton

        bar = QHBoxLayout()
        reload_button = QPushButton("刷新群列表")
        reload_button.clicked.connect(lambda: _reload_group_combo(self.group))
        send_button = QPushButton("生成并发送…")
        send_button.clicked.connect(self._on_send)
        bar.addWidget(reload_button)
        bar.addWidget(send_button)
        bar.addStretch(1)
        form.add_row("", bar)
        layout.addWidget(form)
        layout.addStretch(1)

    def _selected_group(self) -> str:
        return str(self.group.currentData() or "")

    def _on_send(self) -> None:
        group_id = self._selected_group()
        if not group_id:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.warning(self, "无法发送", "没有可用的已授权群。")
            return
        period_kind = self.period.currentText()
        from PySide6.QtWidgets import QMessageBox

        answer = QMessageBox.question(
            self,
            "确认真实发送",
            f"向群 {group_id} 发送一条真实摘要？\n周期：{period_kind}\n\n"
            "该操作通过 UIA 写入微信群，且受 outbox 幂等保护。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        def job():
            return summary_send_once(
                group_id,
                period_kind,
                retry_failed=self.retry_failed.isChecked(),
            )

        run_async(self, job, label="摘要发送")

    def refresh(self) -> None:
        _reload_group_combo(self.group)


class MemberSendPage(QWidget):
    """成员画像发送：随机 / 指定 / 全员广播 + 广播进度。"""

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)

        single = FormSection("单张画像发送")
        self.group = _group_combo()
        single.add_row("群", self.group)
        self.member = QLineEdit()
        self.member.setPlaceholderText("成员 wxid 或精确昵称（随机发送时留空）")
        single.add_row("成员", self.member)
        self.display_name = QLineEdit()
        self.display_name.setPlaceholderText("可选：卡片标题用已核验的当前群昵称")
        single.add_row("标题显示名", self.display_name)

        from PySide6.QtWidgets import QHBoxLayout, QPushButton

        bar = QHBoxLayout()
        random_button = QPushButton("随机发送…")
        random_button.clicked.connect(lambda: self._on_send(random_pick=True))
        send_button = QPushButton("指定发送…")
        send_button.clicked.connect(lambda: self._on_send(random_pick=False))
        reload_button = QPushButton("刷新群列表")
        reload_button.clicked.connect(lambda: _reload_group_combo(self.group))
        for button in (random_button, send_button, reload_button):
            bar.addWidget(button)
        bar.addStretch(1)
        single.add_row("", bar)
        layout.addWidget(single)

        campaign = FormSection("全员广播")
        campaign_bar = QHBoxLayout()
        broadcast_button = QPushButton("发起全员广播…")
        broadcast_button.clicked.connect(self._on_broadcast)
        progress_button = QPushButton("刷新进度")
        progress_button.clicked.connect(self.refresh)
        campaign_bar.addWidget(broadcast_button)
        campaign_bar.addWidget(progress_button)
        campaign_bar.addStretch(1)
        campaign.add_row("", campaign_bar)

        self.progress_table = QTableWidget(0, 5)
        self.progress_table.setHorizontalHeaderLabels(["campaign", "状态", "计数", "", ""])
        self.progress_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.progress_table.horizontalHeader().setStretchLastSection(True)
        campaign.add_row("", self.progress_table)
        layout.addWidget(campaign)
        layout.addStretch(1)

    def _selected_group(self) -> str:
        return str(self.group.currentData() or "")

    def _confirm(self, title: str, text: str) -> bool:
        from PySide6.QtWidgets import QMessageBox

        return QMessageBox.question(
            self, title, text, QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        ) == QMessageBox.Yes

    def _on_send(self, *, random_pick: bool) -> None:
        from PySide6.QtWidgets import QMessageBox

        group_id = self._selected_group()
        if not group_id:
            QMessageBox.warning(self, "无法发送", "没有可用的已授权群。")
            return
        if random_pick:
            question = f"向群 {group_id} 随机选择一名已完成建档成员并发送真实画像卡片？"
        else:
            question = (
                f"向群 {group_id} 的「{self.member.text().strip()}」发送真实画像卡片？"
            )
        if not self._confirm("确认真实发送", question + "\n\n该操作通过 UIA 写入微信群。"):
            return
        member_value = "" if random_pick else self.member.text().strip()

        def job():
            return member_send(
                group_id,
                member=member_value,
                display_name=self.display_name.text(),
                random_pick=random_pick,
            )

        run_async(self, job, label="画像发送")

    def _on_broadcast(self) -> None:
        group_id = self._selected_group()
        if not group_id:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.warning(self, "无法发起", "没有可用的已授权群。")
            return
        if not self._confirm(
            "确认全员广播",
            f"为群 {group_id} 创建/恢复持久化广播任务？\n\n"
            "运行中的产品会逐人发送画像，成功项不会重复；定时摘要窗口会主动让路。",
        ):
            return
        run_async(self, lambda: broadcast_start(group_id), label="发起广播")

    def refresh(self) -> None:
        _reload_group_combo(self.group)
        try:
            status = broadcast_progress()
        except Exception:
            status = {}
        table = self.progress_table
        campaign = status.get("campaign")
        if not campaign:
            table.setRowCount(1)
            table.setItem(0, 0, QTableWidgetItem("—"))
            table.setItem(0, 1, QTableWidgetItem("暂无广播任务"))
            for col in (2, 3, 4):
                table.setItem(0, col, QTableWidgetItem(""))
            return
        counts = ", ".join(f"{k}={v}" for k, v in sorted(status.get("counts", {}).items())) or "-"
        rows = [
            (str(campaign), str(status.get("status")), counts, str(status.get("group_name") or ""), ""),
        ]
        table.setRowCount(len(rows))
        for index, values in enumerate(rows):
            for col, value in enumerate(values):
                table.setItem(index, col, QTableWidgetItem(value))


class ReplyPage(QWidget):
    """自动回复管理容器：策略 tab + 定时摘要 tab + 成员画像 tab。"""

    def __init__(self):
        super().__init__()
        tabs = QTabWidget()
        tabs.addTab(ReplyStrategyPage(), "策略与调度")
        tabs.addTab(SummaryActionsPage(), "定时摘要发送")
        tabs.addTab(MemberSendPage(), "成员画像发送")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(tabs)
