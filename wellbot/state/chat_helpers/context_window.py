"""LLM 컨텍스트 윈도우 선택.

긴 대화에서 전체 히스토리를 매 턴 Bedrock 에 보내면 입력 토큰이 무한정 커져
비용·지연·컨텍스트 한도 초과를 유발한다. 최근 우선 슬라이딩 윈도우로 히스토리를
토큰 예산 내로 제한한다. (문서 내용 회상은 kb_search·search_attachment 툴이
담당하므로 요약 없이 윈도우만으로 충분.)
"""

from __future__ import annotations

from wellbot.services.files.chunker import estimate_tokens
from wellbot.state.chat_models import Message


def select_context_window(
    messages: list[Message], max_tokens: int
) -> list[Message]:
    """Bedrock 에 보낼 대화 히스토리를 토큰 예산으로 제한(최근 우선).

    - 마지막(현재) 메시지는 예산과 무관하게 항상 포함.
    - 끝에서부터 역순으로 예산(max_tokens)까지 포함.
    - Bedrock Converse 는 user↔assistant 교대 + user 시작을 요구하므로,
      윈도우 선두의 assistant 메시지를 제거해 user 로 시작하도록 맞춘다.

    Args:
        messages: 시간순(오름차순) 메시지 목록. 마지막이 현재 turn.
        max_tokens: 히스토리 토큰 예산(추정 기준).

    Returns:
        예산 내 최근 메시지 부분목록(시간순 유지).
    """
    if not messages:
        return []

    n = len(messages)
    start = n - 1  # 마지막 메시지는 무조건 포함
    used = estimate_tokens(messages[-1].content)
    for i in range(n - 2, -1, -1):
        cost = estimate_tokens(messages[i].content)
        if used + cost > max_tokens:
            break
        used += cost
        start = i

    # user 로 시작하도록 선두의 assistant 제거 (교대 규칙 위반 방지)
    while start < n - 1 and messages[start].role != "user":
        start += 1

    return messages[start:]
