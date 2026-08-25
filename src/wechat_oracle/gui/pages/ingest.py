"""采集与群管理页面：数据源、raw 授权向导、群清单。"""

from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ...config import settings
from ..actions import raw_command
from ..widgets import FormSection, GroupListEditor, check_box, combo_box, int_spin, secret_edit, text_edit
from ..workers import run_async
from .base import BasePage


class IngestPage(BasePage):
    def __init__(self):
        super().__init__("采集与群管理", "数据来源与群授权。保存后写入 .env。")

    def _build(self, body: QVBoxLayout) -> None:
        source = FormSection("数据源")
        self.ingest_backend = combo_box(["weflow", "wx4py"], settings.ingest_backend)
        source.add_row("采集后端", self.ingest_backend, "weflow=官方 HTTP/SSE；wx4py=可见 UI 回退。")
        self.weflow_url = text_edit(settings.weflow_base_url)
        source.add_row("WeFlow 地址", self.weflow_url)
        self.weflow_token = secret_edit(settings.weflow_token)
        source.add_row("WeFlow Token", self.weflow_token)
        body.addWidget(source)

        raw = FormSection("本机原库只读同步（默认关闭）")
        self.raw_enabled = check_box(settings.raw_wechat_enabled)
        raw.add_row("启用", self.raw_enabled)
        self.raw_account = QComboBox()
        self.raw_account.setEditable(True)
        if settings.raw_wechat_account:
            self.raw_account.addItem(settings.raw_wechat_account)
        raw.add_row("账号指纹", self.raw_account, "留空时点「扫描账号」自动发现。")
        self.raw_install_root = text_edit(str(settings.raw_wechat_install_root))
        raw.add_row("安装目录", self.raw_install_root)
        self.raw_workspace = text_edit(str(settings.raw_wechat_workspace))
        raw.add_row("工作目录", self.raw_workspace)
        self.raw_interval = int_spin(settings.raw_wechat_sync_interval_seconds, 30, 3600, " 秒")
        raw.add_row("同步间隔", self.raw_interval)
        self.raw_fallback = check_box(settings.raw_wechat_reply_fallback_enabled)
        raw.add_row("回复回退", self.raw_fallback, "UI 不可用时，新鲜精确 @ 走限时回退。")
        self.raw_fallback_age = int_spin(
            settings.raw_wechat_reply_fallback_max_age_seconds, 30, 900, " 秒"
        )
        raw.add_row("回退时效", self.raw_fallback_age)

        wizard_bar = QHBoxLayout()
        scan_button = QPushButton("扫描账号")
        scan_button.clicked.connect(self._on_scan)
        groups_button = QPushButton("列出群并勾选授权…")
        groups_button.clicked.connect(self._on_pick_groups)
        wizard_bar.addWidget(scan_button)
        wizard_bar.addWidget(groups_button)
        wizard_bar.addStretch(1)
        raw.add_row("", wizard_bar)
        self.raw_status_label = QLabel("")
        self.raw_status_label.setWordWrap(True)
        self.raw_status_label.setStyleSheet("color:#888888;")
        raw.add_row("", self.raw_status_label)
        body.addWidget(raw)

        groups = FormSection("群清单（WO_GROUPS）")
        self.groups_editor = GroupListEditor(list(settings.groups))
        groups.add_row("", self.groups_editor)
        body.addWidget(groups)

    def _collect_updates(self) -> dict[str, str]:
        account = self.raw_account.currentText().strip()
        if "（" in account:
            account = account.split("（", 1)[0].strip()
        return {
            "WO_INGEST_BACKEND": self.ingest_backend.currentText(),
            "WO_WEFLOW_BASE_URL": self.weflow_url.text().strip(),
            "WO_WEFLOW_TOKEN": self.weflow_token.text().strip(),
            "WO_RAW_WECHAT_ENABLED": "true" if self.raw_enabled.isChecked() else "false",
            "WO_RAW_WECHAT_ACCOUNT": account,
            "WO_RAW_WECHAT_INSTALL_ROOT": self.raw_install_root.text().strip(),
            "WO_RAW_WECHAT_WORKSPACE": self.raw_workspace.text().strip(),
            "WO_RAW_WECHAT_SYNC_INTERVAL_SECONDS": str(self.raw_interval.value()),
            "WO_RAW_WECHAT_REPLY_FALLBACK_ENABLED": "true" if self.raw_fallback.isChecked() else "false",
            "WO_RAW_WECHAT_REPLY_FALLBACK_MAX_AGE_SECONDS": str(self.raw_fallback_age.value()),
            "WO_GROUPS": json.dumps(self.groups_editor.values(), ensure_ascii=False),
        }

    def _validate(self, updates: dict[str, str]) -> None:
        backend = updates["WO_INGEST_BACKEND"]
        if backend not in {"weflow", "wx4py"}:
            raise ValueError("采集后端只能是 weflow 或 wx4py")

    # --- raw 授权向导 ---
    def _selected_account(self) -> str:
        account = self.raw_account.currentText().strip()
        if "（" in account:
            account = account.split("（", 1)[0].strip()
        return account

    def _on_scan(self) -> None:
        run_async(self, lambda: raw_command("scan"), label="扫描账号", on_done=self._apply_scan)

    def _apply_scan(self, payload) -> None:
        payload = payload or {}
        accounts = list(payload.get("accounts") or [])
        processes = int(payload.get("weixin_process_count") or 0)
        if not accounts:
            self.raw_status_label.setText(
                f"未发现任何本地账号分片（微信进程 {processes} 个）。"
                "请确认微信已登录且版本受支持。"
            )
            return
        current = self._selected_account()
        block = self.raw_account.blockSignals(True)
        self.raw_account.clear()
        for item in sorted(accounts, key=lambda a: -int(a.get("latest_activity_ns") or 0)):
            fingerprint = str(item.get("account_fingerprint"))
            self.raw_account.addItem(f"{fingerprint}（{item.get('shards')} shards）")
        if current:
            index = self.raw_account.findText(current)
            if index >= 0:
                self.raw_account.setCurrentIndex(index)
        self.raw_account.blockSignals(False)
        best = max(accounts, key=lambda a: int(a.get("latest_activity_ns") or 0))
        self.raw_status_label.setText(
            f"发现 {len(accounts)} 个账号，微信进程 {processes} 个；最近活跃："
            f"{str(best.get('account_fingerprint'))[:16]}…"
        )

    def _on_pick_groups(self) -> None:
        account = self._selected_account()

        def load():
            authorized = {
                str(row.get("canonical_group_id"))
                for row in (raw_command("status").get("authorizations") or [])
                if int(row.get("enabled") or 0)
            }
            args = ("groups", *(("--account", account) if account else ()))
            payload = raw_command(*args)
            return authorized, list(payload.get("groups") or [])

        run_async(self, load, label="列出群", on_done=self._show_group_dialog)

    def _show_group_dialog(self, result) -> None:
        authorized, groups = result or ([], [])
        if not groups:
            QMessageBox.information(self, "raw groups", "没有发现可选择的群聊会话。")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("选择要授权的群（勾选=授权，取消=撤销）")
        layout = QVBoxLayout(dialog)
        listing = QListWidget(dialog)
        for item in groups:
            group_id = str(item.get("canonical_group_id") or "")
            name = str(item.get("display_name") or "")
            row = QListWidgetItem(f"{name}  [{group_id}]")
            row.setCheckState(
                Qt.CheckState.Checked if group_id in authorized else Qt.CheckState.Unchecked
            )
            row.setData(Qt.ItemDataRole.UserRole, group_id)
            listing.addItem(row)
        layout.addWidget(listing)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.Accepted:
            return

        wanted: list[tuple[str, str, bool]] = []
        for index in range(listing.count()):
            row = listing.item(index)
            checked = row.checkState() == Qt.CheckState.Checked
            group_id = str(row.data(Qt.ItemDataRole.UserRole))
            name = row.text().split("  [")[0]
            wanted.append((group_id, name, checked))

        account = self._selected_account()

        def apply():
            changes = {"authorized": [], "revoked": []}
            for group_id, _name, checked in wanted:
                already = group_id in authorized
                if checked and not already:
                    args = ("authorize", "--canonical-id", group_id)
                    raw_command(*(args + (("--account", account) if account else ())))
                    changes["authorized"].append(group_id)
                elif not checked and already:
                    args = ("revoke", "--canonical-id", group_id)
                    raw_command(*(args + (("--account", account) if account else ())))
                    changes["revoked"].append(group_id)
            status = raw_command("status")
            return {"changes": changes, "authorizations": list(status.get("authorizations") or [])}

        run_async(self, apply, label="应用授权", on_done=self._apply_authorizations)

    def _apply_authorizations(self, result) -> None:
        result = result or {}
        changes = result.get("changes") or {}
        enabled_names = [
            str(row.get("display_name"))
            for row in (result.get("authorizations") or [])
            if int(row.get("enabled") or 0)
        ]
        parts = []
        if changes.get("authorized"):
            parts.append(f"新增授权 {len(changes['authorized'])} 个群")
        if changes.get("revoked"):
            parts.append(f"撤销 {len(changes['revoked'])} 个群")
        summary = "；".join(parts) or "授权无变化"
        if enabled_names:
            known = {name.strip() for name in self.groups_editor.values()}
            additions = [name for name in enabled_names if name not in known]
            if additions:
                merged = self.groups_editor.values() + additions
                self.groups_editor._list.clear()
                for value in merged:
                    self.groups_editor._list.addItem(value)
                summary += f"；已把 {len(additions)} 个新授权群加入群清单（记得保存）"
        self.raw_status_label.setText(summary)