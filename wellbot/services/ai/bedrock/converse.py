"""Bedrock ConverseStream 단일 턴 호출 + 동기/비동기 스트리밍 래퍼."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from wellbot.constants import STREAM_MAX_CONCURRENT
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

    # thinking 활성 여부·방식 결정.
    #   adaptive: thinking:{type:adaptive} + output_config.effort (Opus 4.6+/Sonnet 4.6+/신형)
    #             — effort 는 반드시 별도 output_config 에. thinking 안에 넣으면 ValidationException.
    #             — Opus 4.7/4.8 은 manual(budget_tokens) 전송 시 400 이므로 반드시 이 경로.
    #   manual:   thinking:{type:enabled, budget_tokens} (레거시: Sonnet 4.5/Opus 4.5)
    thinking_active = False
    if model.thinking and thinking_enabled:
        if model.thinking_mode == "adaptive":
            adaptive_fields: dict[str, Any] = {"thinking": {"type": "adaptive"}}
            if model.effort:
                adaptive_fields["output_config"] = {"effort": model.effort}
            kwargs["additionalModelRequestFields"] = adaptive_fields
            thinking_active = True
        elif model.thinking_budget > 0:
            kwargs["additionalModelRequestFields"] = {
                "thinking": {
                    "type": "enabled",
                    "budget_tokens": model.thinking_budget,
                }
            }
            thinking_active = True

    # temperature/topP 는 두 경우에 전송하지 않는다.
    #   1) thinking 활성 시 — Bedrock 이 sampling 지정을 허용하지 않음
    #   2) supports_temperature=False (Opus 4.7/4.8 등) — 신형 모델이 sampling
    #      파라미터를 폐기해 전송 시 ValidationException
    #      ('temperature' is deprecated for this model) 발생
    if not thinking_active and model.supports_temperature:
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
                # tool use 재호출 시 reasoning 블록 보존을 위해 누적
                # (thinking + tool use 조합에서 후속 turn 에 reasoningContent
                #  블록을 signature 와 함께 그대로 되돌려줘야 함)
                blk = blocks.get(idx)
                if blk is None or (blk.get("type") == "text" and not blk.get("text")):
                    blk = {"type": "reasoning", "text": "", "signature": ""}
                    blocks[idx] = blk
                if isinstance(rc, dict):
                    rtxt = rc.get("text", "")
                    if rtxt:
                        if blk.get("type") == "reasoning":
                            blk["text"] += rtxt
                        yield ("thinking", rtxt)
                    if rc.get("signature") and blk.get("type") == "reasoning":
                        blk["signature"] += rc["signature"]
                    if rc.get("redactedContent") and blk.get("type") == "reasoning":
                        blk["redacted"] = (
                            blk.get("redacted", b"") + rc["redactedContent"]
                        )
                else:
                    rtxt = str(rc)
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
        if blk.get("type") == "reasoning":
            # thinking 활성 + tool use 시 reasoning 블록을 보존하지 않으면
            # 다음 turn converse 호출에서 ValidationException 발생
            if blk.get("redacted"):
                assistant_blocks.append(
                    {"reasoningContent": {"redactedContent": blk["redacted"]}}
                )
            elif blk.get("text"):
                assistant_blocks.append(
                    {
                        "reasoningContent": {
                            "reasoningText": {
                                "text": blk["text"],
                                "signature": blk.get("signature", ""),
                            }
                        }
                    }
                )
        elif blk.get("type") == "text" and blk.get("text"):
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


# 스트리밍 producer 스레드 전용 풀 — 동시 스트림 상한(= 풀 크기).
_stream_executor: ThreadPoolExecutor | None = None
_stream_executor_lock = threading.Lock()

# adrain_generator 큐 항목 종류 구분용 센티넬
_ITEM = object()
_ERROR = object()
_DONE = object()


def _get_stream_executor() -> ThreadPoolExecutor:
    """스트리밍 producer 전용 스레드풀 (lazy, 스레드 안전)."""
    global _stream_executor
    if _stream_executor is not None:
        return _stream_executor
    with _stream_executor_lock:
        if _stream_executor is None:
            _stream_executor = ThreadPoolExecutor(
                max_workers=STREAM_MAX_CONCURRENT,
                thread_name_prefix="bedrock-stream",
            )
    return _stream_executor


async def adrain_generator(make_gen):
    """동기 이벤트 제너레이터를 producer 스레드 1개로 소비해 async 이터레이터로 변환.

    기존 방식(이벤트당 asyncio.to_thread(safe_next))은 토큰마다 스레드풀 태스크를
    제출해 다중 동시 스트림에서 스레드 경합을 유발했다. 여기서는 스트림(턴) 하나당
    스레드 1개만 점유하고, 이벤트를 loop.call_soon_threadsafe 로 asyncio.Queue 에
    밀어 넣는다. 동시 스트림 수는 전용 executor 크기(STREAM_MAX_CONCURRENT)로 상한되며
    초과분은 슬롯이 빌 때까지 대기한다(backpressure).

    Args:
        make_gen: 인자 없이 호출하면 동기 제너레이터를 반환하는 callable
                  (producer 스레드 안에서 호출됨).

    Yields:
        make_gen() 제너레이터가 산출하는 각 항목.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    stop_flag = threading.Event()

    def _producer() -> None:
        try:
            gen = make_gen()
        except Exception as exc:  # noqa: BLE001 - 소비측에 전파
            loop.call_soon_threadsafe(queue.put_nowait, (_ERROR, exc))
            loop.call_soon_threadsafe(queue.put_nowait, (_DONE, None))
            return
        try:
            for item in gen:
                if stop_flag.is_set():
                    break
                loop.call_soon_threadsafe(queue.put_nowait, (_ITEM, item))
        except Exception as exc:  # noqa: BLE001 - 소비측에 전파
            loop.call_soon_threadsafe(queue.put_nowait, (_ERROR, exc))
        finally:
            try:
                gen.close()  # botocore 스트림 등 자원 정리
            except Exception:
                pass
            loop.call_soon_threadsafe(queue.put_nowait, (_DONE, None))

    fut = loop.run_in_executor(_get_stream_executor(), _producer)
    try:
        while True:
            kind, payload = await queue.get()
            if kind is _ITEM:
                yield payload
            elif kind is _ERROR:
                raise payload
            else:  # _DONE
                break
    finally:
        # 소비 조기 중단(취소) 시 producer 가 다음 이벤트에서 멈추도록 신호.
        stop_flag.set()
        try:
            await fut
        except Exception:
            pass


async def astream_chat(
    messages: list[dict[str, Any]],
    model: ModelConfig,
    system_prompt: str = "",
    *,
    thinking_enabled: bool = True,
):
    """stream_chat 의 비동기 래퍼.

    동기 스트림을 producer 스레드 1개(adrain_generator)로 소비해 토큰당 스레드 홉
    없이 이벤트 루프 블로킹을 방지한다.

    Yields:
        ("thinking", text)  - reasoning delta
        ("text", text)      - 응답 텍스트 delta
        ("usage", dict)     - 토큰 사용량
    """
    async for value in adrain_generator(
        lambda: stream_chat(
            messages, model, system_prompt, thinking_enabled=thinking_enabled
        )
    ):
        yield value


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
