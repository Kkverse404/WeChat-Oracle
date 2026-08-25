"""模型管理页面：LLM、视觉模型、Agent 后端。"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QMessageBox, QVBoxLayout

from ...config import settings
from ...llm import OpenAICompatLLM
from ..widgets import FormSection, check_box, combo_box, int_spin, secret_edit, text_edit
from .base import BasePage


class ModelsPage(BasePage):
    def __init__(self):
        super().__init__(
            "模型管理",
            "对话 LLM、视觉模型与 Agent 后端。保存后写入 .env；密钥只写不回显。",
        )

    def _build(self, body: QVBoxLayout) -> None:
        llm = FormSection("对话 LLM")
        self.llm_provider = combo_box(["openai-compatible"], settings.llm_provider)
        llm.add_row("Provider", self.llm_provider)
        self.llm_endpoint = text_edit(settings.llm_endpoint)
        llm.add_row("API 地址", self.llm_endpoint)
        self.llm_model = text_edit(settings.llm_model)
        llm.add_row("模型", self.llm_model)
        self.llm_key = secret_edit(settings.llm_api_key)
        llm.add_row("API Key", self.llm_key)
        self.json_mode = combo_box(["native", "prompt"], settings.llm_json_mode)
        llm.add_row("JSON 模式", self.json_mode)
        self.max_tokens = int_spin(settings.llm_max_tokens, 100, 100_000)
        llm.add_row("最大输出 tokens", self.max_tokens)

        test_llm = QHBoxLayout()
        test_llm_button = _make_test_button(
            "测试 LLM 连接",
            lambda: self._test_llm(),
        )
        test_llm.addWidget(test_llm_button)
        llm.add_row("", test_llm)
        body.addWidget(llm)

        vision = FormSection("视觉模型")
        self.vision_provider = combo_box(["openai-compatible"], settings.vision_provider)
        vision.add_row("Provider", self.vision_provider)
        self.vision_endpoint = text_edit(settings.vision_endpoint)
        vision.add_row("API 地址", self.vision_endpoint)
        self.vision_model = text_edit(settings.vision_model)
        vision.add_row("模型", self.vision_model)
        self.vision_key = secret_edit(settings.vision_api_key)
        vision.add_row("API Key", self.vision_key)
        self.vision_max_images = int_spin(settings.vision_max_images, 1, 20)
        vision.add_row("最多图片/次", self.vision_max_images)
        self.vision_max_tokens = int_spin(settings.vision_max_tokens or 800, 100, 20_000)
        vision.add_row("最大输出 tokens", self.vision_max_tokens)
        body.addWidget(vision)

        backend = FormSection("Agent 后端")
        self.agent_backend = combo_box(["native", "openclaw"], settings.agent_backend)
        backend.add_row("后端", self.agent_backend)
        self.openclaw_url = text_edit(settings.openclaw_gateway_url)
        backend.add_row("OpenClaw 地址", self.openclaw_url)
        self.openclaw_token = secret_edit(settings.openclaw_token)
        backend.add_row("OpenClaw Token", self.openclaw_token)
        self.openclaw_agent = text_edit(settings.openclaw_agent_id)
        backend.add_row("Agent ID", self.openclaw_agent)
        self.openclaw_timeout = int_spin(int(settings.openclaw_timeout_seconds), 10, 3600, " 秒")
        backend.add_row("超时", self.openclaw_timeout)
        body.addWidget(backend)

    def _test_llm(self) -> None:
        try:
            client = OpenAICompatLLM(
                api_key=self.llm_key.text().strip() or settings.llm_api_key,
                endpoint=self.llm_endpoint.text().strip() or settings.llm_endpoint,
                json_mode=self.json_mode.currentText(),
            )
            client.complete_text(
                model=self.llm_model.text().strip() or settings.llm_model,
                system="You are a connectivity probe.",
                user="Reply with exactly: OK",
                max_tokens=16,
            )
            QMessageBox.information(self, "测试通过", "LLM 连接正常，模型返回了内容。")
        except Exception as exc:
            QMessageBox.warning(self, "测试失败", f"{type(exc).__name__}: {exc}")

    def _collect_updates(self) -> dict[str, str]:
        updates = {
            "WO_LLM_PROVIDER": self.llm_provider.currentText(),
            "WO_LLM_ENDPOINT": self.llm_endpoint.text().strip(),
            "WO_LLM_MODEL": self.llm_model.text().strip(),
            "WO_LLM_JSON_MODE": self.json_mode.currentText(),
            "WO_LLM_MAX_TOKENS": str(self.max_tokens.value()),
            "WO_VISION_PROVIDER": self.vision_provider.currentText(),
            "WO_VISION_ENDPOINT": self.vision_endpoint.text().strip(),
            "WO_VISION_MODEL": self.vision_model.text().strip(),
            "WO_VISION_MAX_IMAGES": str(self.vision_max_images.value()),
            "WO_VISION_MAX_TOKENS": str(self.vision_max_tokens.value()),
            "WO_AGENT_BACKEND": self.agent_backend.currentText(),
            "WO_OPENCLAW_GATEWAY_URL": self.openclaw_url.text().strip(),
            "WO_OPENCLAW_TOKEN": self.openclaw_token.text().strip(),
            "WO_OPENCLAW_AGENT_ID": self.openclaw_agent.text().strip(),
            "WO_OPENCLAW_TIMEOUT_SECONDS": str(self.openclaw_timeout.value()),
        }
        key = self.llm_key.text().strip()
        if key:
            updates["WO_LLM_API_KEY"] = key
        vision_key = self.vision_key.text().strip()
        if vision_key:
            updates["WO_VISION_API_KEY"] = vision_key
        return updates

    def _validate(self, updates: dict[str, str]) -> None:
        if not updates["WO_LLM_ENDPOINT"]:
            raise ValueError("LLM API 地址不能为空")
        if not updates["WO_LLM_MODEL"]:
            raise ValueError("LLM 模型不能为空")
        if updates["WO_AGENT_BACKEND"] not in {"native", "openclaw"}:
            raise ValueError("Agent 后端只能是 native 或 openclaw")
        if updates["WO_AGENT_BACKEND"] == "openclaw" and not updates["WO_OPENCLAW_AGENT_ID"]:
            raise ValueError("OpenClaw Agent ID 不能为空")


def _make_test_button(text: str, handler) -> object:
    from PySide6.QtWidgets import QPushButton

    button = QPushButton(text)
    button.clicked.connect(handler)
    return button