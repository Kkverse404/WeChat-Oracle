"""WeChat Oracle desktop GUI.

This package renders the same SQLite + `.env` state the CLI and TUI operate
on, organized around five entry pages plus a home dashboard. The GUI never
owns child processes; it reads the shared database read-only and writes
configuration through `config_store.write_env_updates`.
"""

from .app import run_gui

__all__ = ["run_gui"]