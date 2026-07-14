"""오탈자 검사.

페이지를 청크로 나눠 Bedrock 에 검사시키고 TypoError 목록을 반환한다.
사용자 사전의 제외어(exclusions)는 (1) 프롬프트에 주입하고 (2) 결과를 후필터한다.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable

from wellbot.services.report_checker.bedrock import call_model, parse_json_response
from wellbot.services.report_checker.config import get_config
from wellbot.services.report_checker.models import (
    AnalysisCancelled,
    ProgressEvent,
    TypoError,
    UserDictionary,
)

log = logging.getLogger(__name__)

ProgressCb = Callable[[ProgressEvent], None] | None

TYPO_SYSTEM = """당신은 한국어/영어 문서 교정 전문가입니다.
주어진 보고서 텍스트에서 오탈자(맞춤법 오류, 띄어쓰기, 명백한 오탈자)를 찾아 JSON 배열로만 응답하세요.
JSON 외 다른 텍스트는 절대 포함하지 마세요.

응답 형식 (오류 없으면 [] 반환):
[
  {
    "page": <페이지 번호(정수)>,
    "original": "<오류 단어/구절>",
    "correction": "<교정 단어/구절>",
    "context": "<오류 앞뒤 30자 내외 원문>"
  }
]

주의: 확실한 오류만 포함하세요. 애매한 경우는 제외하세요."""


def _normalize_word(w: str) -> str:
    """제외어 매칭용 정규화: 공백 제거 + 소문자."""
    return re.sub(r"\s+", "", w.strip().lower())


def _exclusion_clause(dictionary: UserDictionary) -> str:
    """제외어를 시스템 프롬프트에 덧붙일 문구 생성."""
    if not dictionary.exclusions:
        return ""
    joined = ", ".join(dictionary.exclusions)
    return (
        "\n\n다음 단어/표현은 이 문서에서 올바른 표기이므로 절대 오탈자로 보고하지 마세요: "
        f"{joined}"
    )


def check_typos(
    pages: dict[int, str],
    dictionary: UserDictionary | None = None,
    on_progress: ProgressCb = None,
    cancel_check=None,
    usage=None,
) -> list[TypoError]:
    """오탈자 검사. 청크별로 Bedrock 호출."""
    cfg = get_config()
    dictionary = dictionary or UserDictionary()
    system = TYPO_SYSTEM + _exclusion_clause(dictionary)
    excluded = {_normalize_word(x) for x in dictionary.exclusions}

    errors: list[TypoError] = []
    nums = sorted(pages.keys())
    size = cfg.typo_chunk_size
    chunks = [nums[i : i + size] for i in range(0, len(nums), size)]
    total = len(chunks)

    for idx, chunk in enumerate(chunks, 1):
        if cancel_check and cancel_check():
            raise AnalysisCancelled()
        if on_progress:
            on_progress(
                ProgressEvent(
                    stage="typo",
                    detail=f"오탈자 검사 {idx}/{total} (p{chunk[0]}~p{chunk[-1]})",
                    current=idx,
                    total=total,
                    typo_count=len(errors),
                )
            )
        text = "\n\n".join(f"=== 페이지 {p} ===\n{pages[p]}" for p in chunk)
        try:
            raw = call_model(f"다음 텍스트의 오탈자를 찾으세요:\n\n{text}", system, usage=usage)
            items = parse_json_response(raw)
            for it in items:
                original = it.get("original", "")
                # 제외어 후필터 (프롬프트 주입이 무시된 경우 대비)
                if _normalize_word(original) in excluded:
                    continue
                errors.append(
                    TypoError(
                        page=int(it.get("page", chunk[0])),
                        original=original,
                        correction=it.get("correction", ""),
                        context=it.get("context", ""),
                    )
                )
        except json.JSONDecodeError:
            log.warning("report_checker 오탈자 청크 JSON 파싱 실패 chunk=%s", chunk)
        except Exception as e:
            log.warning("report_checker 오탈자 청크 실패 chunk=%s err=%s", chunk, e)
        time.sleep(cfg.call_interval_sec)

    # 마지막 청크까지 반영한 최종 누적 카운트 통지 (이후 단계에서도 정확히 표시)
    if on_progress and total:
        on_progress(
            ProgressEvent(
                stage="typo",
                detail=f"오탈자 검사 완료 ({len(errors)}건)",
                current=total,
                total=total,
                typo_count=len(errors),
            )
        )

    log.info("report_checker 오탈자 합계 count=%d", len(errors))
    return errors
