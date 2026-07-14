"""Wellbot 페이지 (Reflex 컴포넌트 트리)."""

from .admin import admin_page
from .ai_services import ai_services_page
from .index import index_page
from .login import login_page
from .register import register_page
from .report_checker import report_checker_page

__all__ = [
    "admin_page",
    "ai_services_page",
    "index_page",
    "login_page",
    "register_page",
    "report_checker_page",
]
