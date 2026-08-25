"""设置页面：全局、并发、高级预算、关于。"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QMessageBox, QPushButton, QVBoxLayout

from ...config import settings
from ..widgets import FormSection, check_box, combo_box, float_spin, int_spin, text_edit
from .base import BasePage


class SettingsPage(BasePage):
    def __init__(self):
        super().__init__("设置", "全局路径、调度并发、高级 agent 预算。")

    def _build(self, body: QVBoxLayout) -> None:
        global_cfg = FormSection("全局")
        self.data_dir = text_edit(str(settings.data_dir))
        global_cfg.add_row("数据目录", self.data_dir)
        self.db_path = text_edit(str(settings.db_path))
        global_cfg.add_row("数据库", self.db_path)
        self.log_level = combo_box(["TRACE", "DEBUG", "INFO", "WARNING", "ERROR"], settings.log_level.upper())
        global_cfg.add_row("日志级别", self.log_level)
        body.addWidget(global_cfg)

        concurrency = FormSection("调度与并发")
        self.poll_interval = float_spin(settings.dispatcher_poll_interval, 0.5, 60.0, 0.5, 1, " 秒")
        concurrency.add_row("dispatcher 轮询", self.poll_interval)
        self.worker_threads = int_spin(settings.dispatcher_worker_threads, 1, 16)
        concurrency.add_row("dispatcher 线程", self.worker_threads)
        self.kb_interval = int_spin(settings.member_kb_interval_seconds, 300, 86400, " 秒")
        concurrency.add_row("member-kb 间隔", self.kb_interval)
        self.kb_chunk = int_spin(settings.member_kb_chunk_chars, 4_000, 200_000)
        concurrency.add_row("member-kb 分块字符", self.kb_chunk)
        self.kb_max_concurrency = int_spin(settings.member_kb_max_concurrency, 1, 2)
        concurrency.add_row("member-kb 并发", self.kb_max_concurrency)
        self.kb_retries = int_spin(settings.member_kb_retries, 1, 3)
        concurrency.add_row("member-kb 重试", self.kb_retries)
        body.addWidget(concurrency)

        advanced = FormSection("高级 agent 预算")
        self.reflection = check_box(settings.agent_reflection_enabled)
        advanced.add_row("同步 Phase B 反思", self.reflection)
        self.reflect_steps = int_spin(settings.agent_reflect_max_steps, 1, 10)
        advanced.add_row("Phase B 步数", self.reflect_steps)
        self.memory_chars = int_spin(settings.agent_memory_max_chars, 1_000, 200_000)
        advanced.add_row("群记忆上限字符", self.memory_chars)
        self.tool_calls_run = int_spin(settings.agent_max_tool_calls_per_run, 1, 100)
        advanced.add_row("Phase A 工具预算", self.tool_calls_run)
        self.tool_calls_step = int_spin(settings.agent_max_tool_calls_per_step, 1, 20)
        advanced.add_row("Phase A 单步工具", self.tool_calls_step)
        self.image_reads = int_spin(settings.agent_max_image_reads_per_run, 0, 20)
        advanced.add_row("读图预算", self.image_reads)
        self.voice_reads = int_spin(settings.agent_max_voice_reads_per_run, 0, 20)
        advanced.add_row("读语音预算", self.voice_reads)
        body.addWidget(advanced)

        about = FormSection("关于")
        version = QPushButton("运行 doctor 自检")
        version.clicked.connect(self._run_doctor)
        about.add_row("", version)
        body.addWidget(about)

    def _run_doctor(self) -> None:
        from ... import cli as cli_module

        import io
        import contextlib

        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                cli_module.doctor()
            QMessageBox.information(self, "doctor", buf.getvalue())
        except Exception as exc:
            QMessageBox.warning(self, "doctor", f"{type(exc).__name__}: {exc}")

    def _collect_updates(self) -> dict[str, str]:
        return {
            "WO_DATA_DIR": self.data_dir.text().strip(),
            "WO_DB_PATH": self.db_path.text().strip(),
            "WO_LOG_LEVEL": self.log_level.currentText(),
            "WO_DISPATCHER_POLL_INTERVAL": f"{self.poll_interval.value():g}",
            "WO_DISPATCHER_WORKER_THREADS": str(self.worker_threads.value()),
            "WO_MEMBER_KB_INTERVAL_SECONDS": str(self.kb_interval.value()),
            "WO_MEMBER_KB_CHUNK_CHARS": str(self.kb_chunk.value()),
            "WO_MEMBER_KB_MAX_CONCURRENCY": str(self.kb_max_concurrency.value()),
            "WO_MEMBER_KB_RETRIES": str(self.kb_retries.value()),
            "WO_AGENT_REFLECTION_ENABLED": "true" if self.reflection.isChecked() else "false",
            "WO_AGENT_REFLECT_MAX_STEPS": str(self.reflect_steps.value()),
            "WO_AGENT_MEMORY_MAX_CHARS": str(self.memory_chars.value()),
            "WO_AGENT_MAX_TOOL_CALLS_PER_RUN": str(self.tool_calls_run.value()),
            "WO_AGENT_MAX_TOOL_CALLS_PER_STEP": str(self.tool_calls_step.value()),
            "WO_AGENT_MAX_IMAGE_READS_PER_RUN": str(self.image_reads.value()),
            "WO_AGENT_MAX_VOICE_READS_PER_RUN": str(self.voice_reads.value()),
        }

    def _validate(self, updates: dict[str, str]) -> None:
        if not updates["WO_DB_PATH"]:
            raise ValueError("数据库路径不能为空")