"""시스템 프롬프트 가공 헬퍼.

ChatState 의 send_message 가 LLM 호출 직전 system prompt 에
첨부파일 메타 목록을 append 할 때 사용.
"""

from __future__ import annotations

from wellbot.services.files import attachment_service
from wellbot.state.chat_models import mime_to_label


def augment_system_with_attachments(base_prompt: str, conv_id: str) -> str:
    """system prompt 에 현재 대화의 첨부파일 메타 목록 추가.

    파일은 [#file_no] file_name 형식으로 노출하여, LLM 이
    search_attachment 호출 시 file_ids 로 정확 매칭하도록 유도.
    """
    if not conv_id:
        return base_prompt
    try:
        atts = attachment_service.get_conversation_attachments(conv_id)
    except Exception:
        return base_prompt
    if not atts:
        return base_prompt

    # 캐시 hit 가정. 실패 시 무시
    missing_set: set[str] = set()
    try:
        from wellbot.services.ai import embedding_service
        conv_index = embedding_service.get_cache().get(conv_id)
        if conv_index is not None:
            missing_set = set(conv_index.missing_files)
    except Exception:
        missing_set = set()

    lines: list[str] = [
        "",
        "## 이 대화에 첨부된 파일",
        (
            "아래 파일들이 대화에 첨부되어 있습니다. "
            "사용자의 질문이 첨부 파일과 관련될 가능성이 있으면 "
            "`search_attachment` 도구를 호출해 실제 내용을 확인한 뒤 답변하세요. "
            "여러 파일을 검색할 때는 한 번의 호출에 `file_ids` 배열로 일괄 지정하세요 "
            "(파일별로 분할 호출하지 말 것). "
            "각 항목 앞의 [#NNN] 숫자가 file_id 입니다 - 이 값을 그대로 사용하면 "
            "정확 매칭이 보장됩니다. "
            "검색 결과가 비면 같은 의도의 쿼리로 재시도하지 말고 "
            "사용자에게 못 찾았음을 안내하거나 일반 지식으로 답변하세요."
        ),
        "",
    ]
    for a in atts:
        mime = a.mime or ""
        type_label = mime_to_label(mime)
        tokens = a.token_count
        token_str = f"{tokens:,} 토큰" if tokens is not None and tokens > 0 else "처리 중"
        extras = [type_label, token_str]
        if a.file_name in missing_set:
            extras.append("인덱스 미준비")
        lines.append(f"[#{a.file_no}] {a.file_name} ({', '.join(extras)})")
    return f"{base_prompt}\n\n" + "\n".join(lines)
