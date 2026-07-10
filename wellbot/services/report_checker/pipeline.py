"""분석 오케스트레이터.

PDF 페이지 텍스트 + 사용자 사전을 받아 오탈자·일관성 검사를 수행하고
AnalysisResult 를 반환한다. 진행 상황은 on_progress 콜백으로 통지한다.

이 함수는 동기(blocking)이며 여러 Bedrock 호출을 순차 실행한다.
Reflex State 에서는 백그라운드 스레드로 실행하고 콜백을 통해 UI 를 갱신한다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from wellbot.services.report_checker.attention_checker import check_attention
from wellbot.services.report_checker.consistency_checker import (
    extract_facts,
    find_conflicts,
    validate_conflicts,
)
from wellbot.services.report_checker.notation_checker import check_notation
from wellbot.services.report_checker.models import (
    AnalysisResult,
    ProgressEvent,
    Usage,
    UserDictionary,
)
from wellbot.services.report_checker.typo_checker import check_typos

log = logging.getLogger(__name__)

ProgressCb = Callable[[ProgressEvent], None] | None


def run_analysis(
    pages: dict[int, str],
    dictionary: UserDictionary | None = None,
    on_progress: ProgressCb = None,
    *,
    do_consistency: bool = True,
    do_notation: bool = False,
    cancel_check=None,
    usage: Usage | None = None,
) -> AnalysisResult:
    """오탈자 검사(항상) + 주의 항목 + 표기 일관성 + 값 일관성 검사(선택).

    cancel_check: 인자 없이 호출해 True 면 청크 사이에서 AnalysisCancelled 발생.
    usage: 외부에서 주입한 토큰 누적기. 예외(취소/에러)로 중단돼도 호출자가 그때까지의
           부분 사용량을 읽을 수 있도록 여기에 계속 누적한다. 미지정 시 새로 생성.
    """
    dictionary = dictionary or UserDictionary()
    usage = usage if usage is not None else Usage()
    result = AnalysisResult(usage=usage)

    # 1) 오탈자 검사 (기본)
    result.typo_errors = check_typos(pages, dictionary, on_progress, cancel_check, usage)

    # 2) 주의 항목 검사 (사용자 규칙이 있을 때만)
    if dictionary.watch_items:
        result.attention_errors = check_attention(
            pages, dictionary, on_progress, cancel_check, usage
        )

    # 3) 표기 일관성 검사 (선택) — 같은 개념의 표기 흔들림
    if do_notation:
        result.notation_errors = check_notation(
            pages, on_progress, cancel_check, usage
        )

    # 4) 값 일관성 검사 (3단계, 선택)
    if do_consistency:
        facts = extract_facts(pages, on_progress, cancel_check, usage)
        conflicts = find_conflicts(facts, dictionary)
        result.consistency_errors = validate_conflicts(
            conflicts, on_progress, cancel_check, usage
        )

    if on_progress:
        on_progress(
            ProgressEvent(
                stage="done",
                detail="분석 완료",
                typo_count=len(result.typo_errors),
                consistency_count=len(result.consistency_errors),
            )
        )
    return result
