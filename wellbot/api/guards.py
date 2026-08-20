"""HTTP 엔드포인트 인증·권한 가드.

세션 쿠키(``wellbot_auth``)에서 사용자를 도출하는 보일러플레이트를 한곳에 모은다.
각 라우트가 같은 8줄을 복사하고 있었고, 그중 하나만 조건이 어긋나도 인증 구멍이 된다.

**HTTPException 을 던지므로 API 계층에 둔다** — 도메인 계층(``services/auth``)이
FastAPI 를 알면 CLI·State 에서 그 모듈을 쓸 수 없다. 여기서는 세션 검증 결과를
HTTP 응답으로 번역만 한다.

페이지 on_load 게이트나 UI 노출 여부는 URL·API 직접 호출로 우회되므로,
**엔드포인트의 이 가드가 실제 경계다.**
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status

from wellbot.logger import log_context
from wellbot.services.auth import auth_service

log = logging.getLogger(__name__)

# DB 사용자 역할 (emp_m.USER_ROLE_NM)
ROLE_ADMIN = "ADMIN"


def require_user(wellbot_auth: str | None) -> dict:
    """세션 쿠키에서 사용자 정보를 도출. 실패 시 401.

    반환 dict: emp_no / user_nm / user_role_nm / pstn_dept_cd.
    emp_no 를 로그 컨텍스트에 바인딩하므로 호출자가 따로 할 필요 없다.
    """
    if not wellbot_auth:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="로그인이 필요합니다.",
        )
    user = auth_service.validate_session_token(wellbot_auth)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="세션이 만료되었습니다. 다시 로그인해주세요.",
        )
    log_context.bind(emp_no=user["emp_no"])
    return user


def require_admin(wellbot_auth: str | None) -> dict:
    """DB ADMIN 역할 사용자만 통과. 아니면 403.

    역할은 세션 토큰이 아니라 **요청 시점의 DB(emp_m.USER_ROLE_NM)** 에서 온다
    (validate_session_token 이 매 요청 조회) — 권한을 회수하면 기존 세션도 즉시 막힌다.

    .env 비밀번호로 들어온 SUPER 관리자는 세션 쿠키가 없어 여기를 통과하지 못한다.
    의도된 정책이다: KB 관리는 신원이 DB 에 남는 계정만 하도록 한다.
    """
    user = require_user(wellbot_auth)
    if user.get("user_role_nm") != ROLE_ADMIN:
        log.warning(
            "admin endpoint access denied",
            extra={"emp_no": user["emp_no"], "role": user.get("user_role_nm") or ""},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="관리자 권한이 필요합니다.",
        )
    return user
