"""CPU 바운드 파일 파싱을 별도 프로세스로 오프로드.

파일 파싱(pdfplumber/pandas/python-pptx)은 CPU·GIL 바운드라 스레드로는
병렬화되지 않고 메인 프로세스의 이벤트 루프·GIL 과 경쟁한다. 동시 업로드가
채팅 응답 지연을 유발하는 원인이 된다. ProcessPoolExecutor 로 별도 프로세스에서
파싱하면 GIL 이 분리돼 메인 루프가 영향을 받지 않는다.

안전장치:
  - spawn 컨텍스트 사용: 스레드가 이미 떠 있는 프로세스를 fork 할 때 발생하는
    교착/손상 위험(boto3·openssl 등)을 회피한다.
  - 풀 생성·실행 실패 시 호출측(parse_document)이 현재 스레드 내 파싱으로 폴백 →
    최악의 경우에도 기존 동작과 동일하다.
  - CPU_POOL_MAX_WORKERS <= 0 이면 프로세스풀을 완전히 비활성화한다.

참고: process_attachment 는 FastAPI BackgroundTasks(Starlette 워커 스레드)에서
실행되므로, 여기서 future.result() 로 블로킹해도 메인 이벤트 루프는 막지 않는다.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from threading import Lock
from typing import Any

from wellbot.constants import CPU_POOL_MAX_WORKERS

log = logging.getLogger(__name__)

_pool: ProcessPoolExecutor | None = None
_lock = Lock()


def _get_pool() -> ProcessPoolExecutor | None:
    """프로세스풀 lazy 초기화 (스레드 안전). 비활성 시 None."""
    global _pool
    if CPU_POOL_MAX_WORKERS <= 0:
        return None
    if _pool is not None:
        return _pool
    with _lock:
        if _pool is None:
            _pool = ProcessPoolExecutor(
                max_workers=CPU_POOL_MAX_WORKERS,
                mp_context=mp.get_context("spawn"),
            )
            log.info("파싱 프로세스풀 생성 (max_workers=%d)", CPU_POOL_MAX_WORKERS)
    return _pool


def _parse_document_worker(mode: str | None, path_str: str) -> Any:
    """워커 프로세스에서 실행되는 파싱 함수.

    top-level 함수여야 pickle 가능. file_parser 는 워커 내에서 lazy import 해
    자식 프로세스의 import 표면을 최소화한다.
    """
    from wellbot.services.files import file_parser

    return file_parser.get_parser(mode).parse(Path(path_str))


def parse_document(mode: str | None, path: Path | str) -> Any:
    """파일을 프로세스풀에서 파싱. 풀 비활성/실패 시 현재 스레드에서 파싱.

    Args:
        mode: 파서 모드(None 이면 FILE_PARSER_MODE 기본값 사용)
        path: 파싱 대상 파일 경로

    Returns:
        ParsedDocument
    """
    from wellbot.services.files import file_parser

    path_str = str(path)
    pool = _get_pool()
    if pool is not None:
        try:
            return pool.submit(_parse_document_worker, mode, path_str).result()
        except Exception:
            log.warning(
                "프로세스풀 파싱 실패 — 스레드 내 파싱으로 폴백 path=%s",
                path_str,
                exc_info=True,
            )
    # 폴백: 현재 스레드에서 직접 파싱 (기존 동작과 동일)
    return file_parser.get_parser(mode).parse(Path(path_str))


def shutdown() -> None:
    """프로세스풀 종료 (앱 종료 시)."""
    global _pool
    with _lock:
        if _pool is not None:
            _pool.shutdown(wait=False, cancel_futures=True)
            _pool = None
