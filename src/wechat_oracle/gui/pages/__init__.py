"""GUI 页面包。"""

from .base import BasePage
from .dashboard import DashboardPage
from .ingest import IngestPage
from .knowledge import KnowledgePage
from .models import ModelsPage
from .reply import ReplyPage
from .settings import SettingsPage

__all__ = [
    "BasePage",
    "DashboardPage",
    "IngestPage",
    "KnowledgePage",
    "ModelsPage",
    "ReplyPage",
    "SettingsPage",
]