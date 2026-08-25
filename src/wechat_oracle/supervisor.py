"""Shared child-process supervisor for `run` (TUI) and the desktop GUI.

Owns the production topology — ingest (`live`/`ui-live`), `dispatcher`, and
the optional `raw-sync` watcher — so both frontends drive identical process
semantics: spawn through `_self_command`, UTF-8 child pipes, per-process log
reader threads, optional-process auto-restart after a 30s delay, and Windows
process-tree termination on stop.

The supervisor never interprets log content; it forwards raw `(name, line)`
tuples to `emit_log`. Frontends decide how to render them (TUI compacts into
cards; the GUI appends to a stream panel).
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from typing import Callable, Optional

from .config import settings

LogEmitter = Callable[[str, str], None]
OPTIONAL_PROCESSES = frozenset({"raw-sync"})
RESTART_DELAY_SECONDS = 30.0


def self_command(*args: str) -> list[str]:
    """Launch this CLI from source or from the frozen Windows executable."""
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return ["uv", "run", "wechat-oracle", *args]


def child_env() -> dict[str, str]:
    env = dict(os.environ)
    # The parent decodes child stdout as UTF-8. On Windows, Python child
    # processes otherwise default to the active ANSI code page (often
    # cp936/GBK), which corrupts Chinese when read through the pipe.
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def terminate_process_tree(proc: subprocess.Popen[str], *, force: bool = False) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            cmd = ["taskkill", "/PID", str(proc.pid), "/T"]
            if force:
                cmd.append("/F")
            # uv/console-script wrappers keep the real Python worker as a child
            # or grandchild on Windows. Non-forced taskkill often leaves that
            # tree alive, so a follow-up forced kill is required below anyway.
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            proc.terminate()
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass


def build_commands() -> dict[str, list[str]]:
    ingest_command = "ui-live" if settings.ingest_backend == "wx4py" else "live"
    commands = {
        "live": self_command("ingest", ingest_command),
        "dispatcher": self_command("dispatcher"),
    }
    if settings.raw_wechat_enabled:
        commands["raw-sync"] = self_command("raw", "run")
    return commands


class ProcessSupervisor:
    def __init__(
        self,
        *,
        emit_log: Optional[LogEmitter] = None,
        capture_output: bool = True,
        commands_provider: Optional[Callable[[], dict[str, list[str]]]] = None,
    ):
        self._emit = emit_log or (lambda name, text: None)
        self._capture_output = capture_output
        self._commands = commands_provider or build_commands
        self._procs: dict[str, subprocess.Popen[str]] = {}
        self._lock = threading.Lock()
        self._manual_restarts: set[str] = set()
        self._restart_after: dict[str, float] = {}
        self._stopping = False

    @property
    def running(self) -> bool:
        with self._lock:
            return any(proc.poll() is None for proc in self._procs.values())

    def start_all(self) -> None:
        for name in self._commands():
            self.start(name)

    def start(self, name: str) -> None:
        command = self._commands().get(name)
        if command is None:
            return
        with self._lock:
            existing = self._procs.get(name)
            if existing is not None and existing.poll() is None:
                return
        self._log(name, f"starting {' '.join(command)}")
        if self._capture_output:
            proc = subprocess.Popen(
                command,
                env=child_env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            threading.Thread(
                target=self._read_output,
                args=(name, proc),
                name=f"wechat-oracle-{name}-log",
                daemon=True,
            ).start()
        else:
            # Plain mode: children inherit the console so operators see their
            # output directly and Ctrl+C reaches the whole group.
            proc = subprocess.Popen(command, env=child_env())
        with self._lock:
            self._procs[name] = proc

    def stop(self, name: str) -> None:
        with self._lock:
            proc = self._procs.pop(name, None)
        if proc is not None:
            _await_exit(proc)

    def restart(self, name: str) -> None:
        with self._lock:
            proc = self._procs.get(name)
            self._manual_restarts.add(name)
        if proc is not None:
            self._log(name, "restarting")
            _await_exit(proc)
        self.start(name)
        with self._lock:
            self._manual_restarts.discard(name)
        self._log(name, "restarted")

    def stop_all(self) -> None:
        self._stopping = True
        with self._lock:
            procs = list(self._procs.values())
            self._procs.clear()
        for proc in procs:
            terminate_process_tree(proc)
        deadline = time.time() + (2.0 if os.name == "nt" else 10.0)
        for proc in procs:
            while proc.poll() is None and time.time() < deadline:
                time.sleep(0.1)
            if proc.poll() is None:
                terminate_process_tree(proc, force=True)

    def status_rows(self) -> list[tuple[str, int | None, int | None]]:
        with self._lock:
            return [(name, proc.pid, proc.poll()) for name, proc in self._procs.items()]

    def names(self) -> list[str]:
        with self._lock:
            return list(self._procs)

    def watch_forever(
        self,
        *,
        interval: float = 1.0,
        on_critical_exit: Callable[[str, int], None] | None = None,
    ) -> None:
        """Auto-restart optional processes and report critical exits.

        Runs until `stop_all()`; intended for a daemon thread.
        """
        while not self._stopping:
            critical = self.poll_once()
            if critical is not None:
                if on_critical_exit is not None:
                    on_critical_exit(*critical)
                return
            time.sleep(interval)

    def poll_once(self) -> tuple[str, int] | None:
        """Handle one supervision pass; returns (name, code) on critical exit."""
        with self._lock:
            current = list(self._procs.items())
            restarting = set(self._manual_restarts)
        for name, proc in current:
            code = proc.poll()
            if code is None:
                continue
            with self._lock:
                if name in restarting or self._procs.get(name) is not proc:
                    continue
                if name in OPTIONAL_PROCESSES:
                    due = self._restart_after.setdefault(
                        name, time.time() + RESTART_DELAY_SECONDS
                    )
                    if time.time() >= due:
                        self._restart_after.pop(name, None)
                    else:
                        continue
            if name in OPTIONAL_PROCESSES:
                self._log(name, f"restarting after exit {code}")
                self.start(name)
                continue
            self._log(name, f"exited with code {code}; stopping remaining processes")
            return (name, code)
        return None

    def _read_output(self, name: str, proc: subprocess.Popen[str]) -> None:
        if proc.stdout is None:
            return
        for line in proc.stdout:
            text = line.rstrip()
            if text:
                self._emit(name, text)
        try:
            proc.stdout.close()
        except Exception:
            pass

    def _log(self, name: str, text: str) -> None:
        try:
            self._emit(name, text)
        except Exception:
            pass


def _await_exit(
    proc: subprocess.Popen[str],
    *,
    grace_seconds: float | None = None,
) -> None:
    terminate_process_tree(proc)
    deadline = time.time() + (
        grace_seconds if grace_seconds is not None else (2.0 if os.name == "nt" else 10.0)
    )
    while proc.poll() is None and time.time() < deadline:
        time.sleep(0.1)
    if proc.poll() is None:
        terminate_process_tree(proc, force=True)


__all__ = [
    "ProcessSupervisor",
    "build_commands",
    "child_env",
    "self_command",
    "terminate_process_tree",
]
