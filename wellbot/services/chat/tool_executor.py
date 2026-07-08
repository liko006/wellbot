"""Bedrock Converse tool use 핸들러.

LLM 이 호출할 수 있는 도구(tool) 의 스펙을 정의하고, 실제 실행을 담당.

[Tool List]
- search_attachment
- kb_search
"""

from __future__ import annotations

import json
import logging
from typing import Any

from wellbot.constants import KB_SEARCH_TOP_K, READ_ATTACHMENT_MAX_TOKENS, SEARCH_TOP_K
from wellbot.services.ai import embedding_service
from wellbot.services.files.chunker import estimate_tokens
from wellbot.services.knowledgebase import retrieve as kb_retrieve

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


READ_ATTACHMENT_TOOL: dict = {
    "toolSpec": {
        "name": "read_attachment",
        "description": (
            "현재 대화에 첨부된 문서의 전체 내용을 처음부터 끝까지 읽습니다. "
            "문서 전체를 대상으로 하는 작업(전체 요약, 번역, 전수 검토, 목차·구조 파악 등)에 사용하세요. "
            "특정 사실·키워드만 필요하면 search_attachment 를 쓰세요. "
            "여러 파일이 필요하면 file_ids 에 모두 포함하세요. "
            "문서가 매우 길어 결과가 잘리면, 반환된 안내의 offset 값으로 다시 호출해 이어읽으세요."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "file_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": (
                            "읽을 파일의 정수 ID 배열. system prompt 의 [#NNN] 숫자. "
                            "생략하면 대화의 모든 첨부를 읽음."
                        ),
                    },
                    "file_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "fallback. file_ids 를 모를 때만 사용(부분 매칭).",
                    },
                    "offset": {
                        "type": "integer",
                        "description": (
                            "이어읽기 시작 위치(문자 오프셋). 기본 0. "
                            "직전 호출 결과가 잘렸을 때만 안내된 값으로 사용."
                        ),
                    },
                },
                "required": [],
            }
        },
    }
}


KB_SEARCH_TOOL: dict = {
    "toolSpec": {
        "name": "kb_search",
        "description": (
            "지식베이스(Knowledge Base)에서 관련 문서를 의미 기반으로 검색합니다. "
            "사용자가 KB 검색을 활성화한 상태에서는 다음과 같은 질문에 적극적으로 호출하세요: "
            "사실 확인, 정책·규정·절차·매뉴얼, 사내 정보, 업무 데이터, 특정 문서나 자료의 내용. "
            "사용자가 '지식베이스', '문서', '업로드' 등의 단어를 명시적으로 쓰지 않더라도 "
            "내용상 KB에 있을 법한 정보면 검색합니다. "
            "일반 지식만으로 답변하기 전에 먼저 검색해 KB 내용을 반영하세요. "
            "검색을 생략해도 되는 경우는 인사·잡담, 단순 번역, 일반적인 코드 작성 등 "
            "명백히 KB와 무관한 요청에 한정합니다. "
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
                    # kb_scope 는 LLM 입력 스키마에서 제외. 검색 범위는 사용자의 UI
                    # 선택(kb_modes)으로 결정되며 _tool_exec 에서 주입하므로, LLM 이
                    # 추측해 넘겨도 항상 덮어써져 무의미.
                    "top_k": {
                        "type": "integer",
                        "description": f"반환할 상위 결과 개수. 보통 생략하세요(기본 {KB_SEARCH_TOP_K}).",
                    },
                },
                "required": ["query"],
            }
        },
    }
}


def build_tool_config(
    *, include_attachment: bool = True, include_kb: bool = True
) -> dict | None:
    """Bedrock Converse 의 toolConfig 파라미터 반환.

    적용 가능한 도구만 포함한다. 검색 가능한(텍스트) 첨부가 없는데
    search_attachment 를 노출하면 LLM 이 이미지 전용 첨부에 대고 빈 검색을
    반복(empty_limit/max_iter 폴백까지 소진)하므로, 해당 도구는 제외한다.

    Returns:
        toolConfig dict, 또는 노출할 도구가 없으면 None (호출자는 일반 스트리밍).
    """
    tools: list[dict] = []
    if include_attachment:
        tools.append(SEARCH_ATTACHMENT_TOOL)
        tools.append(READ_ATTACHMENT_TOOL)
    if include_kb:
        tools.append(KB_SEARCH_TOOL)
    if not tools:
        return None
    return {
        "tools": tools,
        # auto: LLM 이 자율적으로 사용 여부 결정
        "toolChoice": {"auto": {}},
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
    emp_no: str = "",
) -> dict:
    """LLM 이 호출한 도구를 실제 실행하고 결과를 반환

    Returns:
        Bedrock Converse toolResult.content 블록에 넣을 dict
        성공: {"text": "...", "_meta": {...}}
        실패: {"text": "...", "status": "error"}

        _meta 는 호출자(루프 가드)가 활용할 수 있는 부가 정보 -
        {result_count: int, fallback: str | None}
    """
    try:
        if tool_name == "search_attachment":
            return _run_search_attachment(tool_input, smry_id)
        if tool_name == "read_attachment":
            return _run_read_attachment(tool_input, smry_id)
        if tool_name == "kb_search":
            return _run_kb_search(tool_input, emp_no)
        log.warning("알 수 없는 tool 호출: %s", tool_name)
        return {"text": f"알 수 없는 도구입니다: {tool_name}", "status": "error"}
    except Exception as exc:
        log.exception("tool 실행 실패: %s", exc)
        return {"text": f"도구 실행 중 오류가 발생했습니다: {exc}", "status": "error"}


def _parse_top_k(raw: Any, default: int, max_k: int) -> int:
    """tool_input 의 top_k 를 정수로 변환 후 1..max_k 로 클램프. 변환 실패 시 default"""
    try:
        value = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, max_k))


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

    top_k = _parse_top_k(tool_input.get("top_k"), SEARCH_TOP_K, 10)

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


def _run_read_attachment(tool_input: dict[str, Any], smry_id: str) -> dict:
    """read_attachment 실행 — 첨부 문서 전체 텍스트 반환(예산 내), 초과 시 잘림+이어읽기 안내."""
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

    try:
        offset = max(0, int(tool_input.get("offset") or 0))
    except (TypeError, ValueError):
        offset = 0

    data = embedding_service.load_conversation_texts(
        smry_id, file_ids=file_ids, file_names=file_names
    )
    files = data.get("files", [])
    missing = data.get("missing_files") or []
    fallback = data.get("fallback")

    if not files:
        note = "읽을 수 있는 첨부 문서가 없습니다."
        if missing:
            note += " (처리 중이거나 실패한 파일: " + ", ".join(missing) + ")"
        return {"text": note, "_meta": {"result_count": 0, "missing_files": list(missing)}}

    sections = [
        f"===== 파일: {f['file_name']} (#{f['file_no']}) =====\n{f['text']}"
        for f in files
    ]
    full = "\n\n".join(sections)
    total_chars = len(full)
    body = full[offset:] if offset < total_chars else ""

    truncated = False
    next_offset = 0
    if estimate_tokens(body) > READ_ATTACHMENT_MAX_TOKENS:
        density = estimate_tokens(body) / max(1, len(body))  # 토큰/문자
        keep = max(1, int(READ_ATTACHMENT_MAX_TOKENS / density))
        body = body[:keep]
        truncated = True
        next_offset = offset + keep

    header = [
        f"첨부 문서 {len(files)}개, 전체 {total_chars:,}자 중 "
        f"{offset:,}~{offset + len(body):,}자 구간."
    ]
    if fallback:
        header.append(f"(참고: {fallback})")
    if missing:
        header.append("(참고: 제외된 파일 - " + ", ".join(missing) + ")")
    text = "\n".join(header) + "\n\n" + body
    if truncated:
        text += (
            f"\n\n…(문서가 길어 여기까지만 표시했습니다. 이어읽으려면 "
            f"offset={next_offset} 로 read_attachment 를 다시 호출하세요.)"
        )

    log.info(
        "read_attachment: smry=%s files=%d offset=%d chars=%d truncated=%s "
        "(fallback=%s, missing=%d)",
        smry_id, len(files), offset, len(body), truncated, fallback, len(missing),
    )
    return {
        "text": text,
        "_meta": {
            "result_count": len(files),
            "truncated": truncated,
            "missing_files": list(missing),
        },
    }


def _run_kb_search(tool_input: dict[str, Any], emp_no: str) -> dict:
    """kb_search 실제 실행"""
    query = (tool_input.get("query") or "").strip()
    if not query:
        return {"text": "query 파라미터가 비어있습니다.", "status": "error"}

    kb_scope: list[str] = tool_input.get("kb_scope") or []

    top_k = _parse_top_k(tool_input.get("top_k"), KB_SEARCH_TOP_K, KB_SEARCH_TOP_K)

    retrieve_result = kb_retrieve(query=query, emp_no=emp_no, kb_modes=kb_scope, top_k=top_k)
    results: list[dict] = retrieve_result.get("results", [])
    context: str = retrieve_result.get("context", "")
    sources_searched: dict = retrieve_result.get("sources_searched", {})

    log.info(
        "kb_search: emp_no=%s query=%r kb_scope=%s top_k=%d -> %d hits",
        emp_no, query, kb_scope, top_k, len(results),
    )

    if not results:
        text = (
            "지식베이스 검색 결과 0건. 관련 내용이 존재하지 않을 가능성이 높습니다. "
            "동일 의도의 쿼리로 재시도하지 말고, 사용자에게 '관련 내용을 찾지 못함'을 "
            "안내하거나 보유한 일반 지식으로 답변하세요."
        )
        return {
            "text": text,
            "_meta": {
                "result_count": 0,
                "sources_searched": sources_searched,
                "source_docs": [],
            },
        }

    # source_uri 별로 그룹핑하여 같은 파일의 여러 청크는 한 항목으로 합치되,
    # 각 청크의 rank 는 'ranks' 리스트에 모두 보관 (인용 마커 매칭용)
    by_uri: dict[str, dict] = {}
    for r in results:
        uri = r["source_uri"]
        if uri not in by_uri:
            by_uri[uri] = {
                "title": r["title"],
                "source_uri": uri,
                "source": r["source"],
                "score": r["score"],  # 정렬 후 첫 값이 최고점
                "ext": r["title"].rsplit(".", 1)[-1].lower() if "." in r["title"] else "",
                "ranks": [],
                # rank → page (PDF 청크). 인용 마커([N]) 매칭으로 '인용된 청크의 페이지'만 추려 표시.
                # 표시용 페이지 집합은 rank_pages.values() 에서 파생하므로 별도 pages 리스트는 두지 않음.
                "rank_pages": {},
            }
        rank = r.get("rank", 0)
        by_uri[uri]["ranks"].append(rank)
        page = r.get("page")
        if page is not None:
            by_uri[uri]["rank_pages"][rank] = page
    source_docs = list(by_uri.values())
    return {
        "text": context,
        "_meta": {
            "result_count": len(results),
            "sources_searched": sources_searched,
            "source_docs": source_docs,
        },
    }


def parse_tool_input(raw_json: str) -> dict:
    """스트림에서 누적된 JSON 문자열을 파싱. 실패 시 빈 dict"""
    try:
        data = json.loads(raw_json)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}
