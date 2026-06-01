"""로그 상관관계(correlation) 컨텍스트.

멀티유저 챗봇 운영에서 "어떤 사용자의 어떤 대화·메시지·요청에서 발생한
로그인가"를 추적하기 위해, contextvars 로 요청 단위 컨텍스트 보관.

- conversation_id : 대화 세션 (DB: CHTB_TLK_SMRY_ID)
- message_id      : 개별 메시지/턴 (DB: CHTB_TLK_ID). conv 만으로는
                    세션 전체를 뒤져야 하므로 턴 단위 로그 필터링을 위해 함께 기록.
- request_id      : 매 이벤트(턴) 단위 단명 ID

ContextFilter 가 모든 LogRecord 에 이 값을 자동 주입하므로
각 로깅 호출부에서 emp_no/conversation_id 를 인자로 넘길 필요 없음.

사용:
    # API 미들웨어 / State 이벤트 핸들러 진입부
    from wellbot.logger import log_context
    log_context.bind(emp_no="12345", conversation_id="abc")

    # 요청 종료 시 (또는 with 블록으로 자동 정리)
    with log_context.scope(request_id="req-1", emp_no="12345"):
        ...
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Generator

# 미설정 시 포매터에서 "-" 로 표시
_EMPTY = "-"

_emp_no: ContextVar[str] = ContextVar("emp_no", default=_EMPTY)
_conversation_id: ContextVar[str] = ContextVar("conversation_id", default=_EMPTY)
_message_id: ContextVar[str] = ContextVar("message_id", default=_EMPTY)
_request_id: ContextVar[str] = ContextVar("request_id", default=_EMPTY)

_FIELDS = ("emp_no", "conversation_id", "message_id", "request_id")
_VARS = {
    "emp_no": _emp_no,
    "conversation_id": _conversation_id,
    "message_id": _message_id,
    "request_id": _request_id,
}


def new_request_id() -> str:
    """짧은 요청 ID 생성 (uuid4 앞 8자)"""
    return uuid.uuid4().hex[:8]


def bind(**fields: str | None) -> dict[str, Token]:
    """컨텍스트 값 설정. 복원용 토큰 반환.

    None 인 값은 무시. 반환된 토큰 dict 는 reset() 에 넘겨 복원.
    """
    tokens: dict[str, Token] = {}
    for key, value in fields.items():
        if value is None:
            continue
        var = _VARS.get(key)
        if var is not None:
            tokens[key] = var.set(str(value))
    return tokens


def reset(tokens: dict[str, Token]) -> None:
    """bind() 로 받은 토큰으로 이전 컨텍스트 복원"""
    for key, token in tokens.items():
        var = _VARS.get(key)
        if var is not None:
            var.reset(token)


def clear() -> None:
    """모든 컨텍스트 값을 기본값으로 초기화"""
    for var in _VARS.values():
        var.set(_EMPTY)


def current() -> dict[str, str]:
    """현재 컨텍스트 스냅샷 반환 (LogRecord 주입용)"""
    return {field: _VARS[field].get() for field in _FIELDS}


@contextmanager
def scope(**fields: str | None) -> Generator[None, None, None]:
    """with 블록 동안만 컨텍스트 적용 후 자동 복원.

    request_id 미명시 시 자동 생성.
    """
    if "request_id" not in fields or fields.get("request_id") is None:
        fields["request_id"] = new_request_id()
    tokens = bind(**fields)
    try:
        yield
    finally:
        reset(tokens)
