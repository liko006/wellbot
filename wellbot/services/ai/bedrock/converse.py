"""Bedrock ConverseStream 단일 턴 호출 + 동기/비동기 스트리밍 래퍼."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Generator
from typing import Any

from wellbot.services.ai.bedrock.client import get_client
from wellbot.services.core.settings import ModelConfig

log = logging.getLogger(__name__)


def build_messages(messages: list[dict[str, Any]]) -> list[dict]:
    """ChatState 메시지를 Bedrock Converse 형식으로 변환.

    각 메시지의 content 는 문자열이지만, image_blocks 필드가 있으면
    해당 content 배열에 {"image": {...}} block 을 함께 포함시킴.

    image_blocks 스키마:
        [{"format": "png|jpeg|gif|webp", "bytes": <bytes>}]
    """
    result: list[dict] = []
    for msg in messages:
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue

        content_blocks: list[dict] = []

        # Anthropic 권장: 이미지 블록은 텍스트보다 앞에 배치
        for img in msg.get("image_blocks", []) or []:
            fmt = img.get("format")
            data = img.get("bytes")
            if not fmt or not data:
                continue
            content_blocks.append({
                "image": {
                    "format": fmt,
                    "source": {"bytes": data},
                }
            })

        text = msg.get("content", "") or ""
        # Bedrock Converse 는 content block 이 최소 1개 필요
        if text or not content_blocks:
            content_blocks.append({"text": text or " "})

        result.append({"role": role, "content": content_blocks})
    return result


def stream_one_turn(
    bedrock_messages: list[dict],
    model: ModelConfig,
    system_prompt: str,
    *,
    thinking_enabled: bool,
    tool_config: dict | None,
) -> Generator[tuple[str, Any], None, None]:
    """단일 Converse 호출 (LLM 한 턴).

    Args:
        bedrock_messages: Bedrock Converse 형식 메시지 목록
        model: 모델 설정 (model_id, max_tokens, thinking 등)
        system_prompt: 시스템 프롬프트
        thinking_enabled: extended thinking 활성 여부
        tool_config: Bedrock toolConfig dict. None 이면 tool use 비활성

    Yields:
        ("thinking", text)              - reasoning delta
        ("text", text)                  - 응답 텍스트 delta
        ("tool_use", {id, name, input}) - 완성된 tool use 요청
        ("assistant_content", blocks)   - 어시스턴트 응답 블록 목록 (재호출용 messages append 용)
        ("stop_reason", reason)         - "end_turn" | "tool_use" | ...
        ("usage", dict)                 - 토큰 사용량
    """
    client = get_client()

    kwargs: dict[str, Any] = {
        "modelId": model.model_id,
        "messages": bedrock_messages,
        "inferenceConfig": {
            "maxTokens": model.max_tokens,
        },
    }
    if system_prompt:
        kwargs["system"] = [{"text": system_prompt}]
    if tool_config:
        kwargs["toolConfig"] = tool_config

    if model.thinking and model.thinking_budget > 0 and thinking_enabled:
        kwargs["additionalModelRequestFields"] = {
            "thinking": {
                "type": "enabled",
                "budget_tokens": model.thinking_budget,
            }
        }
    else:
        kwargs["inferenceConfig"]["temperature"] = model.temperature
    if model.top_p is not None:
        kwargs["inferenceConfig"]["topP"] = model.top_p

    call_start = time.perf_counter()
    has_tools = bool(tool_config)
    try:
        response = client.converse_stream(**kwargs)
    except Exception:
        log.exception(
            "bedrock converse_stream 호출 실패 model=%s", model.model_id,
            extra={"model_id": model.model_id, "tools": has_tools},
        )
        raise

    # contentBlockIndex → 해당 블록의 누적 정보
    blocks: dict[int, dict] = {}
    text_accum: list[str] = []
    stop_reason: str | None = None
    first_byte_ms: int | None = None

    for event in response["stream"]:
        if first_byte_ms is None:
            first_byte_ms = int((time.perf_counter() - call_start) * 1000)
        if "contentBlockStart" in event:
            idx = event["contentBlockStart"].get("contentBlockIndex", 0)
            start = event["contentBlockStart"].get("start", {}) or {}
            if "toolUse" in start:
                tu = start["toolUse"]
                blocks[idx] = {
                    "type": "tool_use",
                    "toolUseId": tu.get("toolUseId", ""),
                    "name": tu.get("name", ""),
                    "input_buffer": "",
                }
            else:
                blocks[idx] = {"type": "text", "text": ""}

        elif "contentBlockDelta" in event:
            idx = event["contentBlockDelta"].get("contentBlockIndex", 0)
            delta = event["contentBlockDelta"].get("delta", {})

            if "text" in delta:
                txt = delta["text"]
                text_accum.append(txt)
                blk = blocks.setdefault(idx, {"type": "text", "text": ""})
                if blk.get("type") == "text":
                    blk["text"] += txt
                yield ("text", txt)

            elif "reasoningContent" in delta:
                rc = delta["reasoningContent"]
                rtxt = rc.get("text", "") if isinstance(rc, dict) else str(rc)
                if rtxt:
                    yield ("thinking", rtxt)

            elif "toolUse" in delta:
                tu_delta = delta["toolUse"]
                blk = blocks.get(idx)
                if blk and blk.get("type") == "tool_use":
                    partial = tu_delta.get("input", "")
                    if partial:
                        blk["input_buffer"] += partial

        elif "contentBlockStop" in event:
            idx = event["contentBlockStop"].get("contentBlockIndex", 0)
            blk = blocks.get(idx)
            if blk and blk.get("type") == "tool_use":
                from wellbot.services.chat.tool_executor import parse_tool_input

                parsed = parse_tool_input(blk.get("input_buffer", "") or "{}")
                blk["input"] = parsed
                yield (
                    "tool_use",
                    {
                        "toolUseId": blk.get("toolUseId", ""),
                        "name": blk.get("name", ""),
                        "input": parsed,
                    },
                )

        elif "messageStop" in event:
            stop_reason = event["messageStop"].get("stopReason")

        elif "metadata" in event:
            usage = event["metadata"].get("usage", {})
            if usage:
                yield ("usage", usage)

    # tool_loop 재호출을 위해 응답 블록을 순서대로 재구성
    ordered_indices = sorted(blocks.keys())
    assistant_blocks: list[dict] = []
    for i in ordered_indices:
        blk = blocks[i]
        if blk.get("type") == "text" and blk.get("text"):
            assistant_blocks.append({"text": blk["text"]})
        elif blk.get("type") == "tool_use":
            assistant_blocks.append(
                {
                    "toolUse": {
                        "toolUseId": blk.get("toolUseId", ""),
                        "name": blk.get("name", ""),
                        "input": blk.get("input", {}),
                    }
                }
            )
    if assistant_blocks:
        yield ("assistant_content", assistant_blocks)

    log.info(
        "bedrock converse done",
        extra={
            "model_id": model.model_id,
            "tools": has_tools,
            "stop_reason": stop_reason or "end_turn",
            "ttfb_ms": first_byte_ms,
            "elapsed_ms": int((time.perf_counter() - call_start) * 1000),
        },
    )
    yield ("stop_reason", stop_reason or "end_turn")


def stream_chat(
    messages: list[dict[str, Any]],
    model: ModelConfig,
    system_prompt: str = "",
    *,
    thinking_enabled: bool = True,
) -> Generator[tuple[str, Any], None, None]:
    """Bedrock ConverseStream 호출 - tool use 없는 단순 스트리밍.

    Yields:
        ("thinking", text)  - reasoning delta
        ("text", text)      - 응답 텍스트 delta
        ("usage", dict)     - 토큰 사용량
    """
    bedrock_messages = build_messages(messages)
    for event_type, payload in stream_one_turn(
        bedrock_messages,
        model,
        system_prompt,
        thinking_enabled=thinking_enabled,
        tool_config=None,
    ):
        if event_type in ("text", "thinking", "usage"):
            yield (event_type, payload)
        # tool use 없는 모드에서 assistant_content·stop_reason·tool_use 는 불필요


def safe_next(
    gen: Generator[tuple[str, str], None, None],
) -> tuple[bool, tuple[str, str] | None]:
    """StopIteration 을 안전하게 처리하는 next() 래퍼.

    asyncio.to_thread 에서 제너레이터를 한 스텝씩 소비할 때 사용.
    """
    try:
        return (True, next(gen))
    except StopIteration:
        return (False, None)


async def astream_chat(
    messages: list[dict[str, Any]],
    model: ModelConfig,
    system_prompt: str = "",
    *,
    thinking_enabled: bool = True,
):
    """stream_chat 의 비동기 래퍼.

    asyncio.to_thread 로 동기 스트리밍을 감싸 이벤트 루프 블로킹 방지.

    Yields:
        ("thinking", text)  - reasoning delta
        ("text", text)      - 응답 텍스트 delta
        ("usage", dict)     - 토큰 사용량
    """
    gen = stream_chat(messages, model, system_prompt, thinking_enabled=thinking_enabled)
    while True:
        has_value, value = await asyncio.to_thread(safe_next, gen)
        if not has_value:
            break
        yield value  # type: ignore[misc]


def stream_one_turn_iter(
    bedrock_messages: list[dict],
    model: ModelConfig,
    system_prompt: str,
    thinking_enabled: bool,
    tool_config: dict | None,
):
    """stream_one_turn 을 generator 로 래핑 (asyncio.to_thread 전달용)."""
    yield from stream_one_turn(
        bedrock_messages,
        model,
        system_prompt,
        thinking_enabled=thinking_enabled,
        tool_config=tool_config,
    )
