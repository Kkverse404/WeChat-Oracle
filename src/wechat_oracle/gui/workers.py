"""Reusable background workers so GUI actions never block the UI thread."""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Signal


class _Worker(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[[], Any], parent: QObject | None = None):
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:
        try:
            result = self._fn()
        except Exception as exc:  # surface to UI, never crash the thread
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.done.emit(result)


def run_async(
    parent: QObject,
    fn: Callable[[], Any],
    *,
    on_done: Callable[[Any], None] | None = None,
    on_error: Callable[[str], None] | None = None,
    label: str = "操作",
) -> _Worker:
    """Run `fn` off-thread; route results back through queued signals."""

    def default_error(message: str) -> None:
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.warning(parent, f"{label}失败", message)

    worker = _Worker(fn, parent)
    worker.done.connect(on_done) if on_done else worker.done.connect(lambda _: None)
    worker.failed.connect(on_error or default_error)
    worker.finished.connect(worker.deleteLater)
    worker.start()
    return worker