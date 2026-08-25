"""Shared Qt widgets for the WeChat Oracle GUI."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

_OK_STYLE = "color:#8bdc7f"
_BAD_STYLE = "color:#ff6b6b"
_MUTED_STYLE = "color:#888888"
_SECTION_STYLE = "font-weight:bold;color:#5ccfe6;"


def section_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet(_SECTION_STYLE)
    return label


def status_label(text: str, ok: bool, *, muted: bool = False) -> QLabel:
    label = QLabel(text)
    if muted:
        label.setStyleSheet(_MUTED_STYLE)
    elif ok:
        label.setStyleSheet(_OK_STYLE)
    else:
        label.setStyleSheet(_BAD_STYLE)
    return label


def secret_edit(value: str) -> QLineEdit:
    edit = QLineEdit(value)
    edit.setEchoMode(QLineEdit.Password)
    edit.setPlaceholderText("已配置；留空保持不变")
    return edit


def text_edit(value: str) -> QLineEdit:
    return QLineEdit(value)


def check_box(value: bool) -> QCheckBox:
    box = QCheckBox()
    box.setChecked(value)
    return box


def combo_box(items: list[str], value: str) -> QComboBox:
    box = QComboBox()
    box.addItems(items)
    index = box.findText(value)
    box.setCurrentIndex(index if index >= 0 else 0)
    return box


def int_spin(value: int, minimum: int, maximum: int, suffix: str = "") -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(minimum, maximum)
    spin.setValue(int(value))
    if suffix:
        spin.setSuffix(suffix)
    return spin


def float_spin(
    value: float,
    minimum: float,
    maximum: float,
    step: float,
    decimals: int = 1,
    suffix: str = "",
) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(minimum, maximum)
    spin.setSingleStep(step)
    spin.setDecimals(decimals)
    spin.setValue(float(value))
    if suffix:
        spin.setSuffix(suffix)
    return spin


class FormSection(QWidget):
    """A titled card with a two-column form."""

    def __init__(self, title: str):
        super().__init__()
        self._title = QLabel(title)
        self._title.setStyleSheet("font-weight:bold;color:#5ccfe6;")
        self._form = QFormLayout()
        self._form.setLabelAlignment(Qt.AlignRight)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 12)
        outer.addWidget(self._title)
        outer.addLayout(self._form)

    def add_row(self, label: str, widget: QWidget, help_text: str = "") -> None:
        self._form.addRow(label, widget)
        if help_text:
            hint = QLabel(help_text)
            hint.setStyleSheet("color:#888888;font-size:11px;")
            hint.setWordWrap(True)
            self._form.addRow("", hint)


class GroupListEditor(QWidget):
    """Editable list of exact group selectors (display name or @chatroom)."""

    def __init__(self, values: list[str]):
        super().__init__()
        self._list = QListWidget()
        for value in values:
            self._list.addItem(value)
        add_edit = QLineEdit()
        add_edit.setPlaceholderText("精确群名或 @chatroom id")
        add_button = QPushButton("添加")
        remove_button = QPushButton("移除选中")

        def add_item() -> None:
            text = add_edit.text().strip()
            if text and not any(self._list.item(i).text() == text for i in range(self._list.count())):
                self._list.addItem(text)
                add_edit.clear()

        add_button.clicked.connect(add_item)
        remove_button.clicked.connect(
            lambda: self._list.takeItem(self._list.currentRow())
            if self._list.currentRow() >= 0
            else None
        )

        buttons = QHBoxLayout()
        buttons.addWidget(add_edit)
        buttons.addWidget(add_button)
        buttons.addWidget(remove_button)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._list)
        outer.addLayout(buttons)

    def values(self) -> list[str]:
        return [self._list.item(i).text() for i in range(self._list.count())]


def row_button(text: str, on_click: Callable[[], None]) -> QPushButton:
    button = QPushButton(text)
    button.clicked.connect(on_click)
    return button