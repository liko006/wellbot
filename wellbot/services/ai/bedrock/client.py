"""Bedrock Runtime boto3 클라이언트 싱글턴."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

import boto3

from wellbot.services.core.aws import stream_config


@lru_cache(maxsize=1)
def get_client() -> Any:
    """Bedrock Runtime 클라이언트 싱글턴.

    AWS_REGION 미설정 시 AWS_DEFAULT_REGION, 그것도 없으면 us-east-1 폴백.
    lru_cache 로 프로세스 생존 기간 단일 인스턴스 보장.

    채팅 스트리밍이 이 클라이언트를 공유하므로 Config 는 stream_config()
    (풀 크기 ≥ STREAM_MAX_CONCURRENT, 이벤트 간 무전송 허용 시간 확대).
    """
    region = os.environ.get(
        "AWS_REGION",
        os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
    )
    return boto3.client("bedrock-runtime", region_name=region, config=stream_config())
