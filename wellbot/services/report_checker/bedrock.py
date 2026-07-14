"""report_checker 전용 Bedrock 호출 래퍼.

앱의 bedrock/converse.py(스트리밍) 와 독립적으로, 단일 턴 비스트리밍 JSON
응답을 받는 경량 클라이언트를 자체 구현한다. ThrottlingException 재시도 및
```json 코드펜스 제거를 포함한다.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from functools import lru_cache
from typing import Any

import boto3

from wellbot.services.report_checker.config import get_config

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_client(region: str) -> Any:
    """Bedrock Runtime 클라이언트 싱글턴 (region 별).

    region 인자가 빈 문자열이면 AWS_REGION → AWS_DEFAULT_REGION → us-east-1 폴백.
    """
    resolved = region or os.environ.get(
        "AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    )
    return boto3.client("bedrock-runtime", region_name=resolved)


def call_model(prompt: str, system: str = "", usage=None) -> str:
    """Bedrock Converse 로 단일 턴 호출 후 응답 텍스트 반환.

    JSON 잘림(stopReason == "max_tokens")은 상위에서 파싱 실패로 이어질 수 있어
    경고 로그를 남긴다(원본은 조용히 청크를 버렸음).

    usage: Usage 누적기(선택). 전달되면 응답의 토큰 사용량을 누적한다.
    """
    cfg = get_config()
    client = _get_client(cfg.region)

    kwargs: dict[str, Any] = {
        "modelId": cfg.model_id,
        "messages": [{"role": "user", "content": [{"text": prompt}]}],
        "inferenceConfig": {"maxTokens": cfg.max_tokens},
    }
    if system:
        kwargs["system"] = [{"text": system}]
    # temperature 는 신형 모델에서 폐기됨 → 명시값이 있을 때만 전송.
    if cfg.temperature is not None:
        kwargs["inferenceConfig"]["temperature"] = cfg.temperature

    last_exc: Exception | None = None
    for attempt in range(cfg.max_retries):
        try:
            resp = client.converse(**kwargs)
            stop_reason = resp.get("stopReason")
            if stop_reason == "max_tokens":
                log.warning(
                    "report_checker converse 응답 잘림(max_tokens) — "
                    "max_tokens 상향 필요 가능성 model=%s",
                    cfg.model_id,
                )
            if usage is not None:
                usage.add(resp.get("usage"))
            blocks = resp["output"]["message"]["content"]
            texts = [b["text"] for b in blocks if "text" in b]
            return "".join(texts)
        except client.exceptions.ThrottlingException as e:
            last_exc = e
            if attempt < cfg.max_retries - 1:
                wait = cfg.retry_delay_sec * (attempt + 1)
                log.warning("report_checker throttling → %.1fs 후 재시도", wait)
                time.sleep(wait)
            else:
                raise
        except Exception as e:
            raise RuntimeError(f"Bedrock 오류: {e}") from e

    if last_exc:
        raise last_exc
    return ""


def parse_json_response(raw: str) -> list:
    """모델 응답에서 JSON 배열을 파싱. ```json 코드펜스를 제거한다."""
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)
