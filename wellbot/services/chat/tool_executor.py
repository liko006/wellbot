"""Bedrock Converse tool use 핸들러.

LLM 이 호출할 수 있는 도구(tool) 의 스펙을 정의하고 실제 실행을 담당.

구조:
    - search_attachment: 첨부파일 의미 검색
"""

from __future__ import annotations

import json
import logging
from typing import Any

from wellbot.constants import SEARCH_TOP_K
from wellbot.services.ai import embedding_service

log = logging.getLogger(__name__)


# ── Tool Spec (Bedrock Converse toolConfig) ──

SEARCH_ATTACHMENT_TOOL: dict = {
    "toolSpec": {
        "name": "search_attachment",
        "description": (
            "현재 대화에 첨부된 문서들에서 관련 내용을 의미 기반으로 검색합니다. "
            "사용자의 질문이 첨부 파일과 관련될 가능성이 있으면 이 도구를 호출하세요. "
            "여러 파일에서 정보가 필요하면 한 번의 호출에 file_ids 를 모두 포함하여 "
            "일괄 검색하세요. 파일별로 분할 호출하지 마세요. "
            "검색 결과가 비어있으면 같은 의미의 쿼리를 변형해 재시도하지 말고, "
            "사용자에게 못 찾았다고 안내하거나 보유한 일반 지식으로 답변하세요."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "검색할 내용을 설명하는 자연어 쿼리.",
                    },
                    "file_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": (
                            "검색 대상 파일의 정수 ID 배열. system prompt 에 "
                            "표시된 [#NNN] 의 NNN 숫자를 그대로 사용. "
                            "권장 경로 - 정확 매칭. 비어있거나 생략하면 "
                            "대화의 모든 첨부 파일을 대상으로 검색."
                        ),
                    },
                    "file_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "fallback. file_ids 를 모를 때만 사용. 부분/유사 "
                            "매칭 허용. 가능하면 file_ids 를 우선 사용하세요."
                        ),
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "반환할 상위 청크 개수. 기본 5, 최대 10.",
                    },
                },
                "required": ["query"],
            }
        },
    }
}


def build_tool_config() -> dict:
    """Bedrock Converse 의 toolConfig 파라미터 전체 반환"""
    return {
        "tools": [SEARCH_ATTACHMENT_TOOL],
        "toolChoice": {"auto": {}},  # auto: LLM 이 자율적으로 사용 여부 결정
    }


# ── Tool 실행 ──


def _format_search_result(
    results: list[dict],
    *,
    fallback_note: str | None = None,
    missing_files: list[str] | None = None,
) -> str:
    """검색 결과를 LLM 이 이해하기 쉬운 구조화 텍스트로 변환."""
    if not results:
        lines = [
            "검색 결과 0건. 첨부 파일에 해당 내용이 존재하지 않을 가능성이 높습니다. "
            "동일 의도의 쿼리로 재시도하지 말고, 사용자에게 '관련 내용을 찾지 못함'을 "
            "안내하거나 보유한 일반 지식으로 답변하세요."
        ]
        if missing_files:
            lines.append("")
            lines.append(
                "참고: 아래 파일은 인덱스가 아직 준비되지 않아 검색 대상에서 "
                "제외되었습니다 - " + ", ".join(missing_files)
            )
        return "\n".join(lines)

    lines: list[str] = [f"총 {len(results)}개의 관련 청크를 찾았습니다.", ""]
    for i, r in enumerate(results, start=1):
        lines.append(
            f"[{i}] {r['file_name']} (청크 #{r['seq']}, score={r['score']:.3f})"
        )
        lines.append(r["text"])
        lines.append("")
    if fallback_note:
        lines.append(f"(참고: {fallback_note})")
    if missing_files:
        lines.append(
            "(참고: 인덱스 미준비 파일 - " + ", ".join(missing_files) + ")"
        )
    return "\n".join(lines).strip()


def execute_tool(
    tool_name: str,
    tool_input: dict[str, Any],
    smry_id: str,
) -> dict:
    """LLM 이 호출한 도구를 실제 실행하고 결과 반환.

    Returns:
        Bedrock Converse toolResult.content 블록에 넣을 dict.
        성공: {"text": "...", "_meta": {...}}
        실패: {"text": "...", "status": "error"}

        _meta 는 호출자(루프 가드)가 활용할 수 있는 부가 정보 -
        {result_count: int, fallback: str | None}
    """
    try:
        if tool_name == "search_attachment":
            return _run_search_attachment(tool_input, smry_id)
        log.warning("알 수 없는 tool 호출: %s", tool_name)
        return {"text": f"알 수 없는 도구입니다: {tool_name}", "status": "error"}
    except Exception as exc:
        log.exception("tool 실행 실패: %s", exc)
        return {"text": f"도구 실행 중 오류가 발생했습니다: {exc}", "status": "error"}


def _run_search_attachment(tool_input: dict[str, Any], smry_id: str) -> dict:
    """search_attachment 실제 실행"""
    query = (tool_input.get("query") or "").strip()
    if not query:
        return {"text": "query 파라미터가 비어있습니다.", "status": "error"}

    raw_file_ids = tool_input.get("file_ids") or []
    file_ids: list[int] | None = None
    if isinstance(raw_file_ids, list):
        coerced: list[int] = []
        for v in raw_file_ids:
            try:
                coerced.append(int(v))
            except (TypeError, ValueError):
                continue
        file_ids = coerced or None

    raw_file_names = tool_input.get("file_names") or []
    file_names = [
        n for n in raw_file_names if isinstance(n, str) and n.strip()
    ] or None

    raw_top_k = tool_input.get("top_k")
    try:
        top_k = int(raw_top_k) if raw_top_k is not None else SEARCH_TOP_K
    except (TypeError, ValueError):
        top_k = SEARCH_TOP_K
    top_k = max(1, min(top_k, 10))

    search_result = embedding_service.search_conversation(
        smry_id=smry_id,
        query=query,
        top_k=top_k,
        file_ids=file_ids,
        file_names=file_names,
    )
    results = search_result.get("results", [])
    fallback = search_result.get("fallback")
    missing = search_result.get("missing_files") or []

    text = _format_search_result(
        results,
        fallback_note=fallback,
        missing_files=missing,
    )
    log.info(
        "search_attachment: smry=%s query=%r file_ids=%s file_names=%s "
        "top_k=%d -> %d hits (fallback=%s, missing=%d)",
        smry_id, query, file_ids, file_names, top_k,
        len(results), fallback, len(missing),
    )
    return {
        "text": text,
        "_meta": {
            "result_count": len(results),
            "fallback": fallback,
            "missing_files": list(missing),
        },
    }


def parse_tool_input(raw_json: str) -> dict:
    """스트림에서 누적된 JSON 문자열 파싱. 실패 시 빈 dict"""
    try:
        data = json.loads(raw_json)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}
