"""Qt bridge around `ProcessSupervisor` plus event-log heartbeat helpers."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from ..supervisor import ProcessSupervisor


class SupervisorBridge(QObject):
    """Owns the shared supervisor and marshals its callbacks onto Qt signals.

    Supervisor reader threads emit through Qt's queued-connection mechanism,
    so slots connected to these signals always run on the GUI thread.
    """

    log_received = Signal(str, str)
    critical_exited = Signal(str, int)

    def __init__(self) -> None:
        super().__init__()
        self._supervisor = ProcessSupervisor(
            emit_log=lambda name, text: self.log_received.emit(name, text),
            capture_output=True,
        )
        self._watch_started = False
        self._watch_lock = threading.Lock()

    @property
    def supervisor(self) -> ProcessSupervisor:
        return self._supervisor

    def ensure_watch_thread(self) -> None:
        with self._watch_lock:
            if self._watch_started:
                return
            self._watch_started = True
        threading.Thread(
            target=self._supervisor.watch_forever,
            kwargs={"on_critical_exit": lambda name, code: self.critical_exited.emit(name, code)},
            name="wechat-oracle-gui-watch",
            daemon=True,
        ).start()

    def start_all(self) -> None:
        self.ensure_watch_thread()
        self._supervisor.start_all()

    def status_rows(self) -> list[tuple[str, int | None, int | None]]:
        return self._supervisor.status_rows()

    # Convenience passthroughs used by buttons.
    def start(self, name: str) -> None:
        self.ensure_watch_thread()
        self._supervisor.start(name)

    def stop(self, name: str) -> None:
        self._supervisor.stop(name)

    def restart(self, name: str) -> None:
        self._supervisor.restart(name)

    def stop_all(self) -> None:
        self._supervisor.stop_all()


def read_recent_events(data_dir: Path, limit: int = 80) -> list[dict[str, Any]]:
    """Tail `data/events.jsonl`; returns newest-last parsed entries."""
    path = data_dir / "events.jsonl"
    if not path.is_file():
        return []
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            block = min(size, 256 * 1024)
            f.seek(max(0, size - block))
            lines = f.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []
    events: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            events.append(entry)
    return events


def heartbeat_summary(data_dir: Path, *, now: float | None = None) -> dict[str, Any]:
    """Compact activity summary for the dashboard lights."""
    events = read_recent_events(data_dir)
    now = time.time() if now is None else now
    latest_ts = 0.0
    latest_event = ""
    counts: dict[str, int] = {}
    recent_window = now - 300
    recent_count = 0
    for entry in events:
        ts = _parse_ts(str(entry.get("ts", "")))
        name = str(entry.get("event", ""))
        if ts > latest_ts:
            latest_ts, latest_event = ts, name
        if name:
            counts[name] = counts.get(name, 0) + 1
            if ts >= recent_window:
                recent_count += 1
    return {
        "latest_event": latest_event,
        "latest_age_seconds": (now - latest_ts) if latest_ts else None,
        "recent_5min": recent_count,
        "total_seen": len(events),
    }


def _parse_ts(value: str) -> float:
    from datetime import datetime

    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return 0.0