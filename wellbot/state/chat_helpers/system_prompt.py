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


def augment_system_with_kb(base_prompt: str, kb_modes: list[str]) -> str:
    """system prompt 에 KB 활성화 안내 append.

    LLM 이 kb_search 도구를 능동적으로 호출하도록 어떤 KB 가 활성화됐는지
    명시하고 사용 지침 + 인용 표기 규칙 제공.
    """
    if not kb_modes:
        return base_prompt

    _labels = {"shared": "회사 문서", "team": "팀 문서", "personal": "내 문서"}
    active = [_labels.get(m, m) for m in kb_modes]

    lines: list[str] = [
        "",
        "## 지식베이스 검색 (사용자가 활성화함)",
        f"활성화된 KB: {', '.join(active)}",
        "",
        (
            "사용자가 이 대화에서 지식베이스 검색을 명시적으로 켜둔 상태입니다. "
            "사용자는 답변에 KB 내용이 반영되기를 기대합니다."
        ),
        "",
        "**호출 원칙: 기본은 검색, 예외만 생략**",
        (
            "- 사실 확인, 정책·규정·절차·매뉴얼, 사내 정보, 업무 데이터, "
            "특정 문서·자료의 내용을 다루는 질문 → **반드시 먼저 `kb_search` 호출**. "
            "사용자가 '지식베이스', '문서', '업로드' 같은 단어를 쓰지 않더라도 "
            "내용상 KB에 있을 법한 정보면 검색합니다."
        ),
        (
            "- 일반 지식만으로 답하기 전에 KB 검색을 먼저 시도하세요. "
            "KB에 더 정확하거나 최신 정보가 있을 수 있습니다."
        ),
        (
            "- 검색을 생략해도 되는 경우: 인사·잡담, 단순 번역, 일반적인 코드 작성, "
            "사용자가 직접 제공한 텍스트만으로 답할 수 있는 질문."
        ),
        (
            "- 검색 결과가 비면 같은 의도의 쿼리로 재시도하지 말고 "
            "사용자에게 못 찾았음을 안내하거나 일반 지식으로 답변하세요."
        ),
        "",
        "**인용 표기 (필수)**",
        (
            "- 검색 결과의 각 청크는 [1], [2] 같은 번호로 식별됩니다."
        ),
        (
            "- 답변에서 청크의 내용을 활용한 부분에는 반드시 [N] 형식으로 번호를 본문에 삽입하세요. "
            "예: '근태 규정에 따르면 연차는 ... [1]'"
        ),
        (
            "- 번호가 본문에 없는 청크는 시스템이 '사용되지 않음'으로 간주하여 출처에서 제외합니다. "
            "실제로 활용한 청크는 빠짐없이 [N] 표기를 해주세요."
        ),
        (
            "- 한 문장에서 여러 청크를 참조하면 [1, 3] 또는 [1][3] 모두 가능합니다."
        ),
    ]
    return f"{base_prompt}\n\n" + "\n".join(lines)
