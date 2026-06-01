"""소요시간 로깅 헬퍼.

"시작 → 완료 + elapsed_ms" 패턴을 컨텍스트 매니저와 데코레이터로 추상화.
모든 로그에는 log_context (emp/conv/req) 가 자동 주입되고,
소요시간은 elapsed_ms extra 필드로 기록되어 JSON 로그에서 쿼리·집계 가능.

사용:
    from wellbot.logger import log_timing

    with log_timing("chat", model=model_name):
        ...   # 완료 시 "chat done" + elapsed_ms 기록, 예외 시 "chat failed" 기록

    @timed("title-generate")
    def generate_title(...):
        ...
"""

from __future__ import annotations

import functools
import logging
import time
from contextlib import contextmanager
from typing import Any, Callable, Generator, TypeVar

# 호출부가 logger 를 넘기지 않으면 사용하는 기본 로거
_DEFAULT_LOGGER = logging.getLogger("wellbot.timing")

F = TypeVar("F", bound=Callable[..., Any])


@contextmanager
def log_timing(
    operation: str,
    *,
    logger: logging.Logger | None = None,
    level: int = logging.INFO,
    log_start: bool = False,
    **fields: Any,
) -> Generator[dict[str, Any], None, None]:
    """블록 실행 시간 로깅.

    Args:
        operation: 작업 이름 (로그 메시지 접두사)
        logger: 사용할 로거. 미지정 시 wellbot.timing
        level: 완료 로그 레벨 (기본 INFO)
        log_start: True 면 시작 시점에도 "<op> start" 기록
        **fields: 로그에 함께 남길 extra 필드 (예: model="claude")

    Yields:
        실행 중 동적으로 필드를 채워 넣을 수 있는 dict (예: ctx["tokens"] = 123).
        여기 담긴 값은 완료 로그의 extra 에 병합.
    """
    log = logger or _DEFAULT_LOGGER
    dynamic: dict[str, Any] = {}
    if log_start:
        log.log(level, "%s start", operation, extra={**fields})

    start = time.perf_counter()
    try:
        yield dynamic
    except Exception:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        log.warning(
            "%s failed",
            operation,
            extra={**fields, **dynamic, "elapsed_ms": elapsed_ms},
            exc_info=True,
        )
        raise
    else:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        log.log(
            level,
            "%s done",
            operation,
            extra={**fields, **dynamic, "elapsed_ms": elapsed_ms},
        )


def timed(
    operation: str | None = None,
    *,
    logger: logging.Logger | None = None,
    level: int = logging.INFO,
) -> Callable[[F], F]:
    """함수 실행 시간 로깅 데코레이터.

    operation 미지정 시 함수 이름 사용.
    동기 함수 전용 — async 함수는 log_timing 컨텍스트 매니저를 직접 사용.
    """

    def decorator(func: F) -> F:
        op = operation or func.__name__

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with log_timing(op, logger=logger, level=level):
                return func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator
