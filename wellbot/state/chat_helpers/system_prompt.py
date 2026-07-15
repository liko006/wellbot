"""시스템 프롬프트 가공 헬퍼.

ChatState 의 send_message 가 LLM 호출 직전 system prompt 에
첨부파일 메타 목록을 append 할 때 사용.
"""

from __future__ import annotations

import textwrap
from datetime import datetime

from wellbot.constants import KST
from wellbot.services.files import attachment_service
from wellbot.state.chat_models import mime_to_label

_WEEKDAYS_KO = ("월", "화", "수", "목", "금", "토", "일")


def augment_system_with_datetime(base_prompt: str) -> str:
    """system prompt 맨 앞에 현재 시각(KST) + 상대 날짜 해석 지침 주입.

    LLM 은 현재 시각을 모르므로, 매 턴 재조립되는 system prompt 에 최신 KST 를
    넣어 '오늘/지금/이번 주' 등 상대 표현을 올바르게 해석하도록 한다.
    """
    now = datetime.now(KST)
    stamp = now.strftime("%Y-%m-%d") + f" ({_WEEKDAYS_KO[now.weekday()]}) " + now.strftime("%H:%M")
    block = (
        "## 현재 시각\n"
        f"현재 시각(KST): {stamp}\n"
        "사용자가 '오늘', '지금', '이번 주', '지난달', 'N일 후' 등 상대적 시점을 말하면 "
        "반드시 위 현재 시각을 기준으로 계산·해석하세요."
    )
    return f"{block}\n\n{base_prompt}"


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
    active = ", ".join(_labels.get(m, m) for m in kb_modes)

    block = textwrap.dedent(
        f"""\
        ## 지식베이스 검색 (사용자가 활성화함)
        활성화된 KB: {active}

        사용자가 이 대화에서 지식베이스 검색을 명시적으로 켜둔 상태입니다. 사용자는 답변에 KB 내용이 반영되기를 기대합니다.

        **호출 원칙: 기본은 검색, 예외만 생략**
        - 사실 확인, 정책·규정·절차·매뉴얼, 사내 정보, 업무 데이터, 특정 문서·자료의 내용을 다루는 질문 → **반드시 먼저 `kb_search` 호출**. 사용자가 '지식베이스', '문서', '업로드' 같은 단어를 쓰지 않더라도 내용상 KB에 있을 법한 정보면 검색합니다.
        - 일반 지식만으로 답하기 전에 KB 검색을 먼저 시도하세요. KB에 더 정확하거나 최신 정보가 있을 수 있습니다.
        - 검색을 생략해도 되는 경우: 인사·잡담, 단순 번역, 일반적인 코드 작성, 사용자가 직접 제공한 텍스트만으로 답할 수 있는 질문.
        - 검색 결과가 비면 같은 의도의 쿼리로 재시도하지 말고 사용자에게 못 찾았음을 안내하거나 일반 지식으로 답변하세요.

        **인용 표기**
        - 검색 결과의 각 청크는 [1], [2] 번호로 식별됩니다.
        - 답변 형식(문장·목록·표·단계 등)은 질문에 가장 적합하게 자유롭게 고르세요. 인용 때문에 굳이 줄글로 쓸 필요는 없습니다.
        - 어떤 형식이든 청크를 활용한 부분에 해당 [N]을 붙이세요.
        - [N]이 본문에 없는 청크는 '사용 안 함'으로 간주되어 출처에서 제외됩니다. 실제 활용한 청크는 빠짐없이 표기하세요.
        - 여러 청크 참조는 [1, 3] 또는 [1][3] 모두 가능."""
    )
    result = f"{base_prompt}\n\n{block}"

    # 공용 KB(규정 문서 + 권위 정렬)는 shared 결과에만 적용되므로 shared 활성 시에만 보강.
    # 인용 [N] 규칙은 위 block 에 이미 있으므로 재진술하지 않는다.
    if "shared" in kb_modes:
        shared_block = textwrap.dedent(
            """\
            **규정·사규 관련 답변 (해당 시)**
            - 질문이 사내 규정·사규·정책과 관련되면, 답변의 근거가 된 조항을 함께 제시하고 그 조항에 기반해 답하세요.
            - 특정 행위·상황이 규정에 부합하는지/위배되는지 묻는 경우엔, 근거 조항에 비추어 부합/위배 여부까지 판단해 답하세요.
            - 규정은 개정·예외가 있을 수 있으니, 판단이 필요하거나 중요한 사안은 담당 부서에 최종 확인하도록 안내하세요. 근거 문서에 담당 부서가 표시(담당: ...)돼 있으면 그 부서명으로 안내하세요.

            **문서 간 내용이 다를 때**
            - 검색 결과는 관련도·권위 순으로 정렬돼 위에 있을수록 우선입니다. 여러 문서가 상충하면 상단(먼저 제시된) 문서를 기준으로 답하세요.
            - 하위 문서에 의미 있게 다른 내용이 있을 때만 함께 안내하세요(충돌이 실제 있고 유의미할 때만, 억지 병기 금지). 그 문서는 파일명으로 지칭하세요."""
        )
        result = f"{result}\n\n{shared_block}"
    return result
