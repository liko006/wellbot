"""클라이언트(브라우저) 오류 수집 엔드포인트.

window.onerror / unhandledrejection 등 **브라우저에서만 발생해 서버 로그에 안 잡히는**
실패(웹/Reflex/websocket/JS)를 비콘으로 받아 구조화 로그에 남긴다.

api_app 의 `_bind_log_context` 미들웨어가 request_id 를 자동 부여하므로 여기선 emp_no
만 보강한다(상관관계). 로깅은 기존 `wellbot` 네임스페이스 로거를 그대로 사용 →
dev=콘솔 / prod=wellbot.log(JSON) 에 동일 포맷으로 적재.

이 엔드포인트는 **실패 비콘 전용** — 성공 로그는 남기지 않는다.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Cookie
from pydantic import BaseModel

from wellbot.logger import log_context
from wellbot.services.auth import auth_service

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["client-log"])


class ClientLogEntry(BaseModel):
    kind: str = ""        # "error" | "unhandledrejection" | ...
    message: str = ""
    detail: str = ""      # stack trace 등
    url: str = ""         # 발생 페이지 URL


@router.post("/client_log")
async def client_log(
    entry: ClientLogEntry,
    wellbot_auth: str | None = Cookie(default=None),
) -> dict:
    """브라우저 오류 비콘을 구조화 로그로 기록.

    인증은 선택(비로그인 단계 오류도 수집). 쿠키가 유효하면 emp_no 를 로그 컨텍스트에
    보강해 사용자 단위로 상관 추적. message 가 비면 무시.
    """
    if wellbot_auth:
        try:
            user = auth_service.validate_session_token(wellbot_auth)
            if user:
                log_context.bind(emp_no=user.get("emp_no"))
        except Exception:
            pass

    if not entry.message:
        return {"ok": True}

    log.warning(
        "client error [%s] %s",
        entry.kind or "error",
        entry.message[:500],
        extra={
            "client_kind": entry.kind or "error",
            "client_detail": (entry.detail or "")[:2000],
            "client_url": (entry.url or "")[:500],
        },
    )
    return {"ok": True}
