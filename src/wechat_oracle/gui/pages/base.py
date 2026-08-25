"""GUI page base class and shared page plumbing."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...config import reload_settings, settings
from ...config_store import ENV_PATH, write_env_updates


class BasePage(QWidget):
    """A scrollable page that loads from settings and saves back to `.env`.

    Subclasses build widgets in `_build()`, collect `WO_*` updates in
    `_collect_updates()`, and may validate in `_validate()`. `save()` writes
    through `config_store.write_env_updates` and reloads the shared settings.

    Read-only pages set `savable=False`; they override `refresh()` to reload
    their own widgets from the database.
    """

    def __init__(self, title: str, subtitle: str = "", savable: bool = True):
        super().__init__()
        self._title = title
        self._subtitle = subtitle
        self._savable = savable
        self._body = QVBoxLayout()
        self._body.setAlignment(Qt.AlignTop)
        self._body.setSpacing(8)

        header = QLabel(title)
        header.setStyleSheet("font-size:18px;font-weight:bold;")
        self._body.addWidget(header)
        if subtitle:
            hint = QLabel(subtitle)
            hint.setStyleSheet("color:#888888;")
            hint.setWordWrap(True)
            self._body.addWidget(hint)

        self._build(self._body)

        if savable:
            save_bar = QHBoxLayout()
            save_bar.addStretch(1)
            save_button = QPushButton("保存到 .env")
            save_button.clicked.connect(self._on_save)
            save_bar.addWidget(save_button)
            self._body.addLayout(save_bar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.addLayout(self._body)
        layout.addStretch(1)
        scroll.setWidget(container)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def _build(self, body: QVBoxLayout) -> None:
        raise NotImplementedError

    def _collect_updates(self) -> dict[str, str]:
        raise NotImplementedError

    def _validate(self, updates: dict[str, str]) -> None:
        return None

    def _on_save(self) -> None:
        try:
            updates = self._collect_updates()
            self._validate(updates)
            if not updates:
                QMessageBox.information(self, "无需保存", "没有可保存的变更。")
                return
            write_env_updates(updates, env_path=ENV_PATH)
            reload_settings()
        except ValueError as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        except OSError as exc:
            QMessageBox.warning(self, "保存失败", f"无法写入 .env：{exc}")
            return
        QMessageBox.information(self, "已保存", "配置已写入 .env 并重新加载。")

    def refresh(self) -> None:
        """Reload widget values from current settings / database."""
        pass