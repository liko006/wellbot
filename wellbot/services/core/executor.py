"""이벤트 루프 기본 스레드풀(executor) 확대.

Reflex 백엔드는 단일 이벤트 루프에서 동작한다. `asyncio.to_thread`(및
Bedrock 토큰 스트리밍의 내부 to_thread)는 루프의 기본 executor 를 사용하는데,
기본값은 min(32, cpu+4) = vCPU 4 기준 8개로 다중 동시 사용자에 과소하다.
블로킹 I/O(DB·Bedrock·S3)는 대기 중 GIL 을 놓으므로 코어 수보다 크게 잡아도
안전하다. 서버 부팅(또는 첫 이벤트 처리) 시 1회 확대 설치한다.
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from wellbot.constants import IO_EXECUTOR_MAX_WORKERS

log = logging.getLogger(__name__)

_installed = False


def ensure_io_executor(max_workers: int | None = None) -> None:
    """실행 중인 이벤트 루프의 기본 executor 를 확대(멱등).

    반드시 이벤트 루프 안에서 호출해야 한다(get_running_loop 사용).
    루프 밖에서 호출되면 조용히 무시하고, 다음 루프 컨텍스트에서 재시도한다.
    """
    global _installed
    if _installed:
        return
    workers = max_workers or IO_EXECUTOR_MAX_WORKERS
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.set_default_executor(
        ThreadPoolExecutor(max_workers=workers, thread_name_prefix="wellbot-io")
    )
    _installed = True
    log.info("기본 IO executor 확대 설치 (max_workers=%d)", workers)
