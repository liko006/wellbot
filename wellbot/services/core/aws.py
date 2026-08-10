"""boto3 클라이언트 공통 설정 (커넥션 풀·타임아웃·재시도).

컨벤션 §17: boto3 client 생성, timeout, retry, connection pool 설정을 공통 factory 에서
관리한다. 클라이언트 생성 자체는 각 서비스가(리전 폴백 규칙이 서로 달라) 유지하되,
**Config 는 반드시 이 모듈에서 받아 쓴다** — 값이 흩어지면 어떤 호출이 어떤 상한을
쓰는지 알 수 없고, 이번처럼 "풀이 동시성보다 작아 대기가 생기는" 문제가 조용히 재발한다.

기본값은 `wellbot.constants` 의 `AWS_*` (환경변수로 조정 가능).

사용:
    boto3.client("bedrock-runtime", region_name=r, config=stream_config())  # 스트리밍
    boto3.client("s3", region_name=r, config=standard_config())             # 그 외 전부
"""

from __future__ import annotations

from botocore.config import Config

from wellbot.constants import (
    AWS_CONNECT_TIMEOUT_SEC,
    AWS_MAX_ATTEMPTS,
    AWS_MAX_POOL_CONNECTIONS,
    AWS_READ_TIMEOUT_SEC,
    AWS_STREAM_READ_TIMEOUT_SEC,
)


def standard_config(read_timeout: int | None = None) -> Config:
    """비스트리밍 호출용 공통 Config.

    Args:
        read_timeout: 응답 대기 상한(초). 미지정 시 ``AWS_READ_TIMEOUT_SEC``.
            호출 특성상 더 오래 기다려야 하는 경우(대용량 문서 1회 호출 등)만 지정한다.
    """
    return Config(
        max_pool_connections=AWS_MAX_POOL_CONNECTIONS,
        connect_timeout=AWS_CONNECT_TIMEOUT_SEC,
        read_timeout=read_timeout or AWS_READ_TIMEOUT_SEC,
        retries={"max_attempts": AWS_MAX_ATTEMPTS, "mode": "standard"},
    )


def stream_config() -> Config:
    """스트리밍(ConverseStream) 전용 Config.

    read_timeout 이 **이벤트 간 무전송 허용 시간**으로 동작하므로 길게 잡는다
    (thinking 구간에서 토큰이 한동안 오지 않는다). 스트림이 이미 시작된 뒤에는
    botocore 가 재시도하지 않으므로 재시도 설정은 연결 단계에만 의미가 있다.
    """
    return Config(
        max_pool_connections=AWS_MAX_POOL_CONNECTIONS,
        connect_timeout=AWS_CONNECT_TIMEOUT_SEC,
        read_timeout=AWS_STREAM_READ_TIMEOUT_SEC,
        retries={"max_attempts": AWS_MAX_ATTEMPTS, "mode": "standard"},
    )
