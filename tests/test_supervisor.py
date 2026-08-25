from __future__ import annotations

import sys
import time

from wechat_oracle.supervisor import ProcessSupervisor


def _sleep_command(seconds: int = 30) -> list[str]:
    return [sys.executable, "-c", f"import time; time.sleep({seconds})"]


def _exit_command(code: int = 3) -> list[str]:
    return [sys.executable, "-c", f"raise SystemExit({code})"]


def test_lifecycle_start_status_stop() -> None:
    supervisor = ProcessSupervisor(
        commands_provider=lambda: {"sleeper": _sleep_command()},
    )
    try:
        supervisor.start_all()
        deadline = time.time() + 10
        while not supervisor.running and time.time() < deadline:
            time.sleep(0.05)
        rows = dict((name, (pid, code)) for name, pid, code in supervisor.status_rows())
        assert rows["sleeper"][0] is not None
        assert rows["sleeper"][1] is None
    finally:
        supervisor.stop_all()
    assert supervisor.status_rows() == []


def test_poll_once_reports_critical_exit_and_stops_optional_restart() -> None:
    logs: list[tuple[str, str]] = []
    supervisor = ProcessSupervisor(
        emit_log=lambda name, text: logs.append((name, text)),
        commands_provider=lambda: {"critical": _exit_command(3)},
    )
    supervisor.start_all()
    deadline = time.time() + 10
    critical = None
    while time.time() < deadline:
        critical = supervisor.poll_once()
        if critical is not None:
            break
        time.sleep(0.05)
    assert critical == ("critical", 3)
    assert any(name == "critical" and "exited with code 3" in text for name, text in logs)
    supervisor.stop_all()


def test_stop_is_idempotent_when_nothing_started() -> None:
    supervisor = ProcessSupervisor(commands_provider=lambda: {})
    supervisor.stop_all()
    assert supervisor.poll_once() is None