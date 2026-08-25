"""Main GUI window: sidebar navigation + stacked pages."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .pages import (
    DashboardPage,
    IngestPage,
    KnowledgePage,
    ModelsPage,
    ReplyPage,
    SettingsPage,
)
from .runtime import SupervisorBridge

NAV_ITEMS = [
    "首页",
    "采集与群管理",
    "自动回复管理",
    "模型管理",
    "知识库管理",
    "设置",
]

_QSS = """
QMainWindow, QWidget { background: #1e262e; color: #d8e0e6; }
QListWidget {
    background: #171e25; border: none; padding-top: 12px;
    font-size: 15px; outline: none;
}
QListWidget::item {
    padding: 17px 16px; margin: 2px 8px; border-radius: 6px;
    color: #b8c4cc;
}
QListWidget::item:selected { background: #233241; color: #ffffff; }
QListWidget::item:hover { background: #1f2b36; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {
    background: #141a21; border: 1px solid #2c3a48; border-radius: 4px;
    padding: 4px 6px; selection-background-color: #2c5c74;
}
QPushButton {
    background: #233241; border: 1px solid #2c5c74; border-radius: 4px;
    padding: 6px 14px;
}
QPushButton:hover { background: #2c5c74; }
QTableWidget { background: #141a21; border: 1px solid #2c3a48; gridline-color: #233241; }
QHeaderView::section { background: #171e25; border: none; padding: 4px; }
QScrollArea { border: none; }
"""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("WeChat Oracle")
        self.resize(1180, 760)
        self._bridge = SupervisorBridge()

        self._nav = QListWidget()
        self._nav.setFixedWidth(180)
        self._stack = QStackedWidget()

        pages = [
            DashboardPage(self._bridge),
            IngestPage(),
            ReplyPage(),
            ModelsPage(),
            KnowledgePage(),
            SettingsPage(),
        ]
        for label, page in zip(NAV_ITEMS, pages):
            self._stack.addWidget(page)
            self._nav.addItem(label)

        self._nav.currentRowChanged.connect(self._switch)

        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        title = QLabel("WeChat Oracle")
        title.setStyleSheet("font-size:16px;font-weight:bold;color:#5ccfe6;padding:12px 14px;")
        left.addWidget(title)
        left.addWidget(self._nav)
        left.addStretch(1)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(left)
        layout.addWidget(self._stack, 1)
        self.setCentralWidget(central)
        self.setStyleSheet(_QSS)

        self._nav.setCurrentRow(0)
        self._bridge.critical_exited.connect(self._on_critical_exit)

    def _switch(self, row: int) -> None:
        page = self._stack.widget(row)
        refresh = getattr(page, "refresh", None)
        if callable(refresh):
            refresh()
        self._stack.setCurrentIndex(row)

    def _on_critical_exit(self, name: str, code: int) -> None:
        QMessageBox.warning(
            self,
            "进程退出",
            f"{name} 已退出（code {code}）；其余进程已停止。可点击「全部启动」重新拉起。",
        )

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        rows = self._bridge.status_rows()
        running = [name for name, _pid, code in rows if code is None]
        if running:
            answer = QMessageBox.question(
                self,
                "退出确认",
                "以下进程仍在运行：{}\n退出并停止它们？".format("、".join(running)),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            self._bridge.stop_all()
        event.accept()


def run_gui() -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("WeChat Oracle")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


__all__ = ["MainWindow", "run_gui"]