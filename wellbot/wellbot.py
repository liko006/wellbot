"""Wellbot 앱 엔트리포인트.

Reflex 앱 초기화 및 페이지 라우트 등록.
파일 업로드 등 멀티파트 HTTP 엔드포인트는 별도 FastAPI 앱을
api_transformer 로 마운트.
"""

# 서비스 모듈이 환경변수를 lazy 검증하므로, .env 로딩은 다른 wellbot 모듈 import 전에 수행
from wellbot.env import init_env

init_env()

# 다른 wellbot 모듈이 로그를 남기기 전에 로깅 구성 필요
from wellbot.logger import setup_logging

setup_logging()

import reflex as rx

from wellbot.api import api_app
from wellbot.pages import (
    admin_page,
    ai_services_page,
    index_page,
    login_page,
    register_page,
    report_checker_page,
)
from wellbot.state import AdminState, AuthState, ChatState
from wellbot.styles import GLOBAL_STYLE, THEME

app = rx.App(
    theme=THEME,
    style=GLOBAL_STYLE,
    api_transformer=api_app,
)
app.add_page(index_page, route="/", title="WellBot", on_load=[AuthState.check_auth, ChatState.on_load])
app.add_page(login_page, route="/login", title="WellBot - 로그인", on_load=AuthState.check_login_page)
app.add_page(register_page, route="/register", title="WellBot - 회원가입", on_load=AuthState.load_dept_list)
app.add_page(admin_page, route="/admin", title="WellBot - 관리", on_load=AdminState.on_admin_load)
app.add_page(ai_services_page, route="/ai-services", title="WellBot - AI 서비스", on_load=AuthState.check_auth)
app.add_page(report_checker_page, route="/ai-services/report-checker", title="WellBot - 보고서 오류 검출", on_load=AuthState.check_auth)
