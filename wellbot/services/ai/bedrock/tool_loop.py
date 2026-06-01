"""Tool-use 스트리밍 루프 + 빈결과/중복 호출 가드 + 폴백 답변."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from wellbot.services.ai.bedrock.converse import (
    build_messages,
    safe_next,
    stream_one_turn_iter,
)
from wellbot.services.core.settings import ModelConfig

log = logging.getLogger(__name__)


def _call_signature(name: str, tool_input: dict) -> tuple:
    """중복 호출 판정용 정규화 키 생성.

    query 는 소문자 strip, file_ids·file_names 는 정렬 후 tuple 로 변환해
    순서가 달라도 동일 호출로 인식.
    """
    inp = tool_input or {}
    query = (inp.get("query") or "").strip().lower()
    file_ids = tuple(sorted(int(x) for x in (inp.get("file_ids") or [])
                            if isinstance(x, (int, str)) and str(x).strip().lstrip("-").isdigit()))
    file_names = tuple(sorted(
        (n or "").strip().lower()
        for n in (inp.get("file_names") or [])
        if isinstance(n, str) and n.strip()
    ))
    return (name, query, file_ids, file_names)


def _strip_tool_result_meta(result_content: dict) -> dict:
    """Bedrock toolResult.content 에 _meta 등 비표준 키 제거."""
    return {k: v for k, v in result_content.items() if not k.startswith("_")}


async def astream_chat_with_tools(
    messages: list[dict[str, Any]],
    model: ModelConfig,
    system_prompt: str,
    *,
    thinking_enabled: bool,
    tool_config: dict,
    tool_executor_fn,  # callable(name, input) -> dict (toolResult.content[0])
    max_iterations: int,
    empty_result_limit: int | None = None,
    duplicate_query_limit: int | None = None,
):
    """tool use 루프 내장 스트리밍 래퍼.

    LLM 이 tool_use 로 종료하면:
      1. 누적된 toolUse block 을 assistant 메시지로 대화에 추가
      2. 각 toolUse 에 대해 tool_executor_fn 실행
      3. 결과를 user 메시지 (toolResult blocks) 로 추가
      4. 다시 LLM 호출 (최대 max_iterations 회)
    end_turn 또는 다른 이유로 종료하면 루프 종료.

    가드:
      - duplicate_query_limit: 동일 (name, query, file_ids, file_names) 호출이
        발생하면 LLM 우회하고 즉시 toolResult 주입
      - empty_result_limit: 결과 0건이 연속 N회 발생하면 루프 강제 종료 후
        도구 비활성 폴백 답변 턴 1회 진행
      - max_iterations 도달: 폴백 답변 턴 진입

    Args:
        messages: ChatState 메시지 목록
        model: 모델 설정
        system_prompt: 시스템 프롬프트
        thinking_enabled: extended thinking 활성 여부
        tool_config: Bedrock toolConfig dict
        tool_executor_fn: callable(name, input) → toolResult.content[0] dict
        max_iterations: 최대 tool-use 반복 횟수
        empty_result_limit: 연속 빈 결과 허용 횟수. None 이면 상수 기본값 사용
        duplicate_query_limit: 동일 호출 허용 횟수. None 이면 상수 기본값 사용

    Yields:
        ("thinking", str)               - reasoning delta
        ("text", str)                   - 응답 텍스트 delta
        ("usage", dict)                 - 토큰 사용량
        ("tool_use", {name, input})     - tool 호출 정보
        ("tool_result", {name, text})   - tool 실행 결과
    """
    from wellbot.constants import (
        TOOL_USE_DUPLICATE_QUERY_LIMIT,
        TOOL_USE_EMPTY_RESULT_LIMIT,
    )

    if empty_result_limit is None:
        empty_result_limit = TOOL_USE_EMPTY_RESULT_LIMIT
    if duplicate_query_limit is None:
        duplicate_query_limit = TOOL_USE_DUPLICATE_QUERY_LIMIT

    bedrock_messages = build_messages(messages)
    seen_calls: dict[tuple, int] = {}
    empty_streak = 0
    end_reason = "end_turn"

    for iteration in range(max_iterations + 1):
        gen = stream_one_turn_iter(
            bedrock_messages,
            model,
            system_prompt,
            thinking_enabled,
            tool_config,
        )
        pending_tool_uses: list[dict] = []
        assistant_blocks: list[dict] = []
        stop_reason: str | None = None

        while True:
            has_value, value = await asyncio.to_thread(safe_next, gen)
            if not has_value:
                break
            event_type, payload = value  # type: ignore[misc]

            if event_type == "text":
                yield ("text", payload)
            elif event_type == "thinking":
                yield ("thinking", payload)
            elif event_type == "usage":
                yield ("usage", payload)
            elif event_type == "tool_use":
                pending_tool_uses.append(payload)
                yield (
                    "tool_use",
                    {"name": payload.get("name"), "input": payload.get("input")},
                )
            elif event_type == "assistant_content":
                assistant_blocks = payload
            elif event_type == "stop_reason":
                stop_reason = payload

        if stop_reason != "tool_use" or not pending_tool_uses:
            end_reason = stop_reason or "end_turn"
            log.info(
                "tool_loop end: smry_iter=%d reason=%s empty_streak=%d seen=%d",
                iteration, end_reason, empty_streak, len(seen_calls),
            )
            return

        # max_iterations 도달 → tool 비활성 폴백 턴
        if iteration >= max_iterations:
            end_reason = "max_iter"
            log.info(
                "tool_loop forced fallback: reason=%s empty_streak=%d seen=%d",
                end_reason, empty_streak, len(seen_calls),
            )
            # 마지막 tool_use 응답을 history 에 추가하지 않고 폴백 진입 (한도 초과 상태이므로)
            async for ev in _emit_no_tool_fallback(
                bedrock_messages, model, system_prompt, thinking_enabled,
                reason=end_reason,
            ):
                yield ev
            return

        if assistant_blocks:
            bedrock_messages.append(
                {"role": "assistant", "content": assistant_blocks}
            )

        tool_result_blocks: list[dict] = []
        for tu in pending_tool_uses:
            name = tu.get("name", "") or ""
            tool_input = tu.get("input", {}) or {}
            sig = _call_signature(name, tool_input)
            seen_calls[sig] = seen_calls.get(sig, 0) + 1

            if seen_calls[sig] > duplicate_query_limit:
                # duplicate_query_limit 초과 - LLM 우회, 합성 결과로 즉시 응답
                synthetic = {
                    "text": (
                        "동일한 쿼리/필터로 이미 검색한 호출입니다. "
                        "결과가 변하지 않으므로 재호출을 중단하고, "
                        "지금까지의 검색 결과로 사용자 질문에 답변하거나 "
                        "'관련 내용을 찾지 못함'을 안내하세요."
                    ),
                }
                tool_result_blocks.append({
                    "toolResult": {
                        "toolUseId": tu.get("toolUseId", ""),
                        "content": [synthetic],
                    }
                })
                yield ("tool_result", {"name": name, "text": synthetic["text"]})
                log.info("tool_loop duplicate blocked: sig=%s count=%d",
                         sig, seen_calls[sig])
                continue

            result_content = await asyncio.to_thread(
                tool_executor_fn, name, tool_input
            )
            meta = result_content.get("_meta") if isinstance(result_content, dict) else None
            result_count = (meta or {}).get("result_count", 0) if meta else 0
            if result_count == 0:
                empty_streak += 1
            else:
                empty_streak = 0

            sanitized = _strip_tool_result_meta(result_content)
            tool_result_blocks.append({
                "toolResult": {
                    "toolUseId": tu.get("toolUseId", ""),
                    "content": [sanitized],
                }
            })
            yield ("tool_result", {"name": name, "text": sanitized.get("text", "")})

            log.info(
                "tool_loop call: iter=%d name=%s sig=%s result_count=%d "
                "empty_streak=%d duplicate_count=%d",
                iteration, name, sig, result_count, empty_streak,
                seen_calls[sig],
            )

        bedrock_messages.append({"role": "user", "content": tool_result_blocks})

        # empty_result_limit 초과 → tool 비활성 폴백 턴
        if empty_streak >= empty_result_limit:
            end_reason = "empty_limit"
            log.info(
                "tool_loop forced fallback: reason=%s empty_streak=%d",
                end_reason, empty_streak,
            )
            async for ev in _emit_no_tool_fallback(
                bedrock_messages, model, system_prompt, thinking_enabled,
                reason=end_reason,
            ):
                yield ev
            return


async def _emit_no_tool_fallback(
    bedrock_messages: list[dict],
    model: ModelConfig,
    system_prompt: str,
    thinking_enabled: bool,
    *,
    reason: str,
):
    """tool 비활성 상태로 한 턴 추가 호출해 폴백 답변 전달.

    reason 에 따라 시스템 프롬프트에 가이던스를 추가해 LLM 이
    지금까지의 toolResult 로 최선의 답변을 생성하도록 유도.
    """
    if reason == "max_iter":
        guidance = (
            "추가 검색이 차단되었습니다 (호출 한도 도달). "
            "지금까지 누적된 toolResult 만 근거로 사용자 질문에 최대한 답하세요. "
            "근거가 부족하면 '첨부 파일에서 관련 내용을 찾지 못했다'고 명시하세요. "
            "이번 응답에서는 도구를 호출하지 마세요."
        )
    else:
        guidance = (
            "추가 검색이 차단되었습니다 (반복된 빈 결과). "
            "보유한 toolResult 또는 일반 지식으로 사용자 질문에 답하세요. "
            "관련 내용을 찾지 못했으면 그 사실을 솔직히 안내하세요. "
            "이번 응답에서는 도구를 호출하지 마세요."
        )

    augmented_system = (
        (system_prompt + "\n\n" if system_prompt else "") + guidance
    )

    gen = stream_one_turn_iter(
        bedrock_messages,
        model,
        augmented_system,
        thinking_enabled,
        tool_config=None,
    )

    yielded_any_text = False
    while True:
        has_value, value = await asyncio.to_thread(safe_next, gen)
        if not has_value:
            break
        event_type, payload = value  # type: ignore[misc]
        if event_type == "text":
            yielded_any_text = True
            yield ("text", payload)
        elif event_type == "thinking":
            yield ("thinking", payload)
        elif event_type == "usage":
            yield ("usage", payload)
        # tool 비활성 폴백 턴에서 tool_use·assistant_content·stop_reason 는 불필요

    if not yielded_any_text:
        # 폴백 턴에서도 텍스트가 없으면 최소 안내 메시지 송출
        yield (
            "text",
            "첨부 파일에서 관련 내용을 찾지 못했습니다. 다른 표현으로 다시 질문해 주세요.",
        )
