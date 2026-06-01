"""Bedrock Runtime boto3 클라이언트 싱글턴."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

import boto3


@lru_cache(maxsize=1)
def get_client() -> Any:
    """Bedrock Runtime 클라이언트 싱글턴.

    AWS_REGION 미설정 시 AWS_DEFAULT_REGION, 그것도 없으면 us-east-1 폴백.
    lru_cache 로 프로세스 생존 기간 단일 인스턴스 보장.
    """
    region = os.environ.get(
        "AWS_REGION",
        os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
    )
    return boto3.client("bedrock-runtime", region_name=region)
