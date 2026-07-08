"""Tool-use 스트리밍 루프 + 빈결과/중복 호출 가드 + 폴백 답변."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from wellbot.constants import READ_ATTACHMENT_MAX_TOKENS, TOOL_RESULT_MAX_TOKENS
from wellbot.services.ai.bedrock.converse import (
    adrain_generator,
    build_messages,
    stream_one_turn_iter,
)
from wellbot.services.core.settings import ModelConfig
from wellbot.services.files.chunker import estimate_tokens

log = logging.getLogger(__name__)


def _cap_tool_result_text(sanitized: Any, name: str = "") -> Any:
    """tool 결과 텍스트를 토큰 예산으로 절단.

    루프가 tool_result 를 매 반복 재전송하므로 대용량 결과(kb_search 등)를
    그대로 두면 입력 토큰이 폭증한다. 검색 결과는 점수 내림차순이라 뒤쪽(저관련)을
    잘라 상위 청크를 보존한다.

    read_attachment(전체 문서 읽기)는 성격상 큰 예산(READ_ATTACHMENT_MAX_TOKENS)을
    적용하고, 이미 tool 자체가 예산 내로 잘라 반환하므로 여기선 안전망 역할.
    """
    if not isinstance(sanitized, dict):
        return sanitized
    text = sanitized.get("text")
    if not isinstance(text, str) or not text:
        return sanitized
    cap = READ_ATTACHMENT_MAX_TOKENS if name == "read_attachment" else TOOL_RESULT_MAX_TOKENS
    total = estimate_tokens(text)
    if total <= cap:
        return sanitized
    density = total / max(1, len(text))  # 토큰/문자
    keep_chars = max(1, int(cap / density))
    trimmed = text[:keep_chars].rstrip()
    log.info(
        "tool_result 절단: name=%s est %d > %d 토큰 → %d자 유지",
        name or "?", total, cap, len(trimmed),
    )
    return {**sanitized, "text": trimmed + "\n\n…(관련도 낮은 이후 결과는 생략됨)"}


def _call_signature(name: str, tool_input: dict) -> tuple:
    """중복 호출 판정용 정규화 키 생성.

    query 는 소문자 strip, file_ids·file_names 는 정렬 후 tuple 로 변환해
    순서가 달라도 동일 호출로 인식.
    그 외 입력 키(kb_scope, top_k 등)도 정규화해 포함 —
    다른 파라미터의 호출이 중복으로 오인 차단되는 것을 방지.
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
    known = {"query", "file_ids", "file_names"}
    try:
        extra = json.dumps(
            {k: v for k, v in inp.items() if k not in known},
            sort_keys=True, ensure_ascii=False, default=str,
        )
    except (TypeError, ValueError):
        extra = ""
    return (name, query, file_ids, file_names, extra)


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
        pending_tool_uses: list[dict] = []
        assistant_blocks: list[dict] = []
        stop_reason: str | None = None

        async for event_type, payload in adrain_generator(
            lambda: stream_one_turn_iter(
                bedrock_messages,
                model,
                system_prompt,
                thinking_enabled,
                tool_config,
            )
        ):
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
                reason=end_reason, tool_config=tool_config,
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

            sanitized = _cap_tool_result_text(_strip_tool_result_meta(result_content), name)
            tool_result_blocks.append({
                "toolResult": {
                    "toolUseId": tu.get("toolUseId", ""),
                    "content": [sanitized],
                }
            })
            # kb_search 결과의 출처 문서(_meta.source_docs)를 이벤트로 전파 →
            # ChatState 가 스트리밍 중 누적해 인용 출처로 표시
            source_docs = (meta or {}).get("source_docs", []) if name == "kb_search" else []
            yield (
                "tool_result",
                {"name": name, "text": sanitized.get("text", ""), "source_docs": source_docs},
            )

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
                reason=end_reason, tool_config=tool_config,
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
    tool_config: dict,
):
    """tool 비활성 상태로 한 턴 추가 호출해 폴백 답변 전달.

    reason 에 따라 시스템 프롬프트에 가이던스를 추가해 LLM 이
    지금까지의 toolResult 로 최선의 답변을 생성하도록 유도.

    주의: 누적된 history 에 toolUse/toolResult 블록이 있으면 Bedrock 은
    toolConfig 가 반드시 정의돼 있기를 요구한다. 따라서 도구를 더하더라도
    tool_config 를 그대로 전달하고, '도구를 호출하지 말라'는 가이던스로
    호출을 통제한다 (tool_config=None 으로 보내면 ValidationException 발생).
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

    # Bedrock toolChoice 에는 'none' 이 없어 가이던스로만 도구 호출을 억제하므로,
    # 모델이 텍스트 없이 tool_use 로만 응답할 수 있다. 그 경우 합성 toolResult 로
    # 추가 검색을 차단하고 다시 답변을 유도(소수 재시도) — 그래도 텍스트가 없을 때만
    # 최종 안내 메시지로 폴백. (무한 루프 방지 위해 시도 횟수 제한)
    _MAX_FALLBACK_TURNS = 2
    yielded_any_text = False
    for _ in range(_MAX_FALLBACK_TURNS):
        pending_tool_uses: list[dict] = []
        assistant_blocks: list[dict] = []
        stop_reason: str | None = None
        async for event_type, payload in adrain_generator(
            lambda: stream_one_turn_iter(
                bedrock_messages,
                model,
                augmented_system,
                thinking_enabled,
                tool_config=tool_config,
            )
        ):
            if event_type == "text":
                yielded_any_text = True
                yield ("text", payload)
            elif event_type == "thinking":
                yield ("thinking", payload)
            elif event_type == "usage":
                yield ("usage", payload)
            elif event_type == "tool_use":
                pending_tool_uses.append(payload)
            elif event_type == "assistant_content":
                assistant_blocks = payload
            elif event_type == "stop_reason":
                stop_reason = payload

        if yielded_any_text:
            return

        # 텍스트 없이 tool_use 로만 끝난 경우: 도구 호출을 합성 결과로 막고 한 번 더 유도.
        if stop_reason == "tool_use" and pending_tool_uses:
            if assistant_blocks:
                bedrock_messages.append(
                    {"role": "assistant", "content": assistant_blocks}
                )
            synthetic = {
                "text": (
                    "추가 도구 호출은 차단되었습니다. 더 이상 검색하지 말고, "
                    "지금까지의 결과로 바로 답변하거나 관련 내용을 찾지 못했음을 안내하세요."
                ),
            }
            bedrock_messages.append({
                "role": "user",
                "content": [
                    {"toolResult": {"toolUseId": tu.get("toolUseId", ""), "content": [synthetic]}}
                    for tu in pending_tool_uses
                ],
            })
            continue  # 다음 폴백 시도

        # tool_use 도 텍스트도 없으면(end_turn 등) 더 시도하지 않음
        break

    if not yielded_any_text:
        # 폴백 턴에서도 텍스트가 없으면 최소 안내 메시지 송출
        yield (
            "text",
            "첨부 파일에서 관련 내용을 찾지 못했습니다. 다른 표현으로 다시 질문해 주세요.",
        )
