"""report_maker 전용 Bedrock 호출 래퍼 (단일 진입점).

legacy 는 invoke_model(Anthropic 네이티브 body)을 5곳 이상에서 제각각 호출하고
예외 처리가 제각각이었다. 여기서 Converse API 기반 단일 헬퍼로 통합한다:
    - call_model : 단일 턴 텍스트
    - call_json  : 텍스트에서 JSON 객체 추출(구조화 응답)
ThrottlingException 지수 백오프 재시도 + max_tokens 잘림 경고 포함.
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
from botocore.config import Config

from wellbot.services.report_maker.config import get_config

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _client(region: str, read_timeout: int) -> Any:
    """Bedrock Runtime 클라이언트 싱글턴.

    region 이 빈 문자열이면 AWS_REGION → AWS_DEFAULT_REGION → us-east-1 폴백.
    """
    resolved = region or os.environ.get(
        "AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    )
    return boto3.client(
        "bedrock-runtime",
        region_name=resolved,
        config=Config(read_timeout=read_timeout),
    )


def _record_usage_safe(action: str, in_tok: int, out_tok: int) -> None:
    """비스트리밍 LLM 호출 토큰을 log_context(emp/conv) 기준으로 chtb_msg_d 에 기록.

    best-effort: 귀속 대화가 없으면 로그만, 실패해도 LLM 호출을 깨지 않는다.
    (스트리밍 생성은 메시지 토큰으로 이미 기록되므로 여기서 이중 기록되지 않는다.)
    """
    if (in_tok or 0) + (out_tok or 0) <= 0:
        return
    try:
        from wellbot.logger import log_context
        from wellbot.services.report_maker import db

        ctx = log_context.current()
        emp = ctx.get("emp_no")
        conv = ctx.get("conversation_id")
        emp = emp if emp and emp != "-" else None
        conv = conv if conv and conv != "-" else None
        if emp and conv:
            db.record_usage(conv, emp, action or "llm", in_tok, out_tok, get_config().model_id)
        else:
            log.info("LLM usage(미귀속) action=%s in=%s out=%s", action or "llm", in_tok, out_tok)
    except Exception:
        log.exception("LLM usage 기록 실패 action=%s", action)


def _converse_content(
    content: list[dict], max_tokens: int, system: str = "",
    *, usage_out: dict | None = None, action: str = "",
) -> tuple[str, str]:
    """Converse 단일 턴 호출(임의 content 블록) → (텍스트, stopReason). 실패 시 ("", "error").

    ThrottlingException 지수 백오프 재시도 + max_tokens 잘림 경고를 한곳에 모은다.
    텍스트/이미지 호출이 모두 이 코어를 거쳐 동일한 에러 처리를 공유한다(예외를 올리지 않음).
    응답 usage(input/output 토큰)를 usage_out 에 채우고, action 라벨로 사용량을 기록한다.
    """
    cfg = get_config()
    client = _client(cfg.region, cfg.read_timeout_sec)
    kwargs: dict[str, Any] = {
        "modelId": cfg.model_id,
        "messages": [{"role": "user", "content": content}],
        "inferenceConfig": {"maxTokens": max_tokens},
    }
    if system:
        kwargs["system"] = [{"text": system}]

    for attempt in range(cfg.max_retries + 1):
        try:
            resp = client.converse(**kwargs)
            stop = resp.get("stopReason", "")
            if stop == "max_tokens":
                log.warning("report_maker 응답 잘림(max_tokens) model=%s", cfg.model_id)
            usage = resp.get("usage") or {}
            in_tok = int(usage.get("inputTokens", 0) or 0)
            out_tok = int(usage.get("outputTokens", 0) or 0)
            if usage_out is not None:
                usage_out["input_tokens"] = in_tok
                usage_out["output_tokens"] = out_tok
            _record_usage_safe(action, in_tok, out_tok)
            blocks = resp["output"]["message"]["content"]
            return "".join(b["text"] for b in blocks if "text" in b), stop
        except client.exceptions.ThrottlingException:
            if attempt < cfg.max_retries:
                wait = cfg.retry_base_delay_sec * (2 ** attempt)
                log.warning("report_maker throttling → %.1fs 후 재시도", wait)
                time.sleep(wait)
            else:
                log.exception("report_maker throttling 재시도 소진")
                return "", "error"
        except Exception:
            log.exception("report_maker Bedrock 호출 실패")
            return "", "error"
    return "", "error"


def _converse_raw(
    prompt: str, max_tokens: int, system: str = "", *, action: str = "",
) -> tuple[str, str]:
    """텍스트 단일 턴 → (텍스트, stopReason)."""
    return _converse_content([{"text": prompt}], max_tokens, system, action=action)


def call_model(prompt: str, max_tokens: int, system: str = "", *, action: str = "") -> str:
    """Converse 단일 턴 호출 → 응답 텍스트. 실패 시 빈 문자열. action=사용량 라벨."""
    return _converse_raw(prompt, max_tokens, system, action=action)[0]


def stream_model(
    prompt: str, max_tokens: int, system: str = "", usage_out: dict | None = None
):
    """Converse 스트리밍(sync generator) — 텍스트 델타를 순차 yield.

    소비측(State)이 시간 배치로 flush 하므로 여기서는 델타 문자열만 낸다.
    - 스트림 시작 전 ThrottlingException 은 지수 백오프 재시도(_converse_content 와 동일).
    - 스트림 시작 후 오류는 로그만 남기고 종료(이미 낸 부분 출력은 유지).
    - 응답 잘림(max_tokens)은 경고만 남기고 받은 데까지 낸다(빈 문자열로 버리지 않음).

    usage_out: 전달하면 스트림 말미의 metadata 이벤트에서 토큰 사용량을
      {"input_tokens": int, "output_tokens": int} 로 채운다(부수효과). 스트림이
      중단되면 채워지지 않을 수 있다(그 경우 호출측은 0 으로 처리).
    """
    cfg = get_config()
    client = _client(cfg.region, cfg.read_timeout_sec)
    kwargs: dict[str, Any] = {
        "modelId": cfg.model_id,
        "messages": [{"role": "user", "content": [{"text": prompt}]}],
        "inferenceConfig": {"maxTokens": max_tokens},
    }
    if system:
        kwargs["system"] = [{"text": system}]

    resp = None
    for attempt in range(cfg.max_retries + 1):
        try:
            resp = client.converse_stream(**kwargs)
            break
        except client.exceptions.ThrottlingException:
            if attempt < cfg.max_retries:
                wait = cfg.retry_base_delay_sec * (2 ** attempt)
                log.warning("report_maker stream throttling → %.1fs 후 재시도", wait)
                time.sleep(wait)
            else:
                log.exception("report_maker stream throttling 재시도 소진")
                return
        except Exception:
            log.exception("report_maker converse_stream 시작 실패")
            return

    try:
        for event in resp["stream"]:
            if "contentBlockDelta" in event:
                txt = event["contentBlockDelta"].get("delta", {}).get("text")
                if txt:
                    yield txt
            elif "messageStop" in event:
                if event["messageStop"].get("stopReason") == "max_tokens":
                    log.warning("report_maker 스트림 응답 잘림(max_tokens) model=%s", cfg.model_id)
            elif "metadata" in event and usage_out is not None:
                usage = event["metadata"].get("usage", {})
                usage_out["input_tokens"] = int(usage.get("inputTokens", 0) or 0)
                usage_out["output_tokens"] = int(usage.get("outputTokens", 0) or 0)
    except Exception:
        log.exception("report_maker 스트림 소비 중 오류(부분 출력 유지)")


def call_vision(
    image_bytes: bytes, image_format: str, prompt: str, max_tokens: int, *, action: str = "vision",
) -> str:
    """이미지 + 지시 프롬프트 → 추출 텍스트. 실패 시 빈 문자열(재시도·에러처리 공유).

    image_format: "jpeg" | "png" | "gif" | "webp" (Converse image block format).
    """
    content = [
        {"image": {"format": image_format, "source": {"bytes": image_bytes}}},
        {"text": prompt},
    ]
    return _converse_content(content, max_tokens, action=action)[0]


def invoke_compat(prompt: str, max_tokens: int, system: str = "", *, action: str = "") -> dict:
    """legacy invoke_model 응답 호환 dict 반환.

    포팅한 프롬프트 조립 코드가 ``resp["content"][0]["text"]`` /
    ``resp.get("stop_reason")`` 형태로 응답을 읽으므로, Converse 결과를 그 형태로
    감싸 원문 프롬프트 로직을 그대로 재사용한다.
    """
    text, stop = _converse_raw(prompt, max_tokens, system, action=action)
    return {"content": [{"text": text}], "stop_reason": stop}


def _extract_json_object(text: str) -> dict:
    """응답 텍스트에서 첫 JSON 객체를 추출. 실패 시 빈 dict."""
    if not text:
        return {}
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group())
    except json.JSONDecodeError:
        log.warning("report_maker JSON 파싱 실패")
        return {}


def call_json(prompt: str, max_tokens: int, system: str = "", *, action: str = "") -> dict:
    """call_model 후 JSON 객체로 파싱. 실패 시 빈 dict."""
    return _extract_json_object(call_model(prompt, max_tokens, system, action=action))
