"""주의 항목 검사.

사용자가 자연어로 입력한 주의 규칙(watch_items)을 기준으로, 각 페이지 청크에서
규칙 위반을 찾아 보고한다. 텍스트로 확인 가능한 규칙(표기·용어·단위 등)만 대상이며,
윗첨자/굵게 등 순수 서식 규칙은 텍스트 추출 특성상 검증할 수 없다.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable

from wellbot.services.report_checker.bedrock import call_model, parse_json_response
from wellbot.services.report_checker.config import get_config
from wellbot.services.report_checker.models import (
    AnalysisCancelled,
    AttentionIssue,
    ProgressEvent,
    UserDictionary,
)

log = logging.getLogger(__name__)

ProgressCb = Callable[[ProgressEvent], None] | None

ATTENTION_SYSTEM = """당신은 문서 검수 전문가입니다.
아래 '주의 규칙'을 기준으로 주어진 보고서 텍스트에서 규칙을 위반한 부분을 찾아
JSON 배열로만 응답하세요. JSON 외 다른 텍스트는 절대 포함하지 마세요.

중요:
- 반드시 아래 제시된 규칙에 대한 위반만 보고하세요. 일반 오탈자는 보고하지 마세요.
- 텍스트만으로 확실히 판단되는 위반만 포함하세요. 애매하면 제외하세요.
- 위반이 없으면 [] 를 반환하세요.

주의 규칙:
{rules}

응답 형식:
[
  {{
    "page": <페이지 번호(정수)>,
    "rule": "<위반한 규칙 (위 목록 중 하나)>",
    "excerpt": "<위반이 있는 원문 발췌 (30자 내외)>",
    "issue": "<무엇이 규칙에 어긋나는지 한 줄 설명>"
  }}
]"""


def check_attention(
    pages: dict[int, str],
    dictionary: UserDictionary | None = None,
    on_progress: ProgressCb = None,
    cancel_check=None,
    usage=None,
) -> list[AttentionIssue]:
    """주의 규칙 위반 검사. 규칙이 없으면 빈 목록."""
    dictionary = dictionary or UserDictionary()
    if not dictionary.watch_items:
        return []

    cfg = get_config()
    rules_block = "\n".join(f"- {r}" for r in dictionary.watch_items)
    system = ATTENTION_SYSTEM.format(rules=rules_block)

    issues: list[AttentionIssue] = []
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
                    stage="attention",
                    detail=f"주의 항목 검사 {idx}/{total} (p{chunk[0]}~p{chunk[-1]})",
                    current=idx,
                    total=total,
                    attention_count=len(issues),
                )
            )
        text = "\n\n".join(f"=== 페이지 {p} ===\n{pages[p]}" for p in chunk)
        try:
            raw = call_model(f"다음 텍스트에서 주의 규칙 위반을 찾으세요:\n\n{text}", system, usage=usage)
            items = parse_json_response(raw)
            for it in items:
                issues.append(
                    AttentionIssue(
                        page=int(it.get("page", chunk[0])),
                        rule=str(it.get("rule", "")).strip(),
                        excerpt=str(it.get("excerpt", "")).strip(),
                        issue=str(it.get("issue", "")).strip(),
                    )
                )
        except json.JSONDecodeError:
            log.warning("report_checker 주의항목 청크 JSON 파싱 실패 chunk=%s", chunk)
        except Exception as e:
            log.warning("report_checker 주의항목 청크 실패 chunk=%s err=%s", chunk, e)
        time.sleep(cfg.call_interval_sec)

    # 루프 종료 후 전체 누적 카운트를 한 번 더 통지 → 이후 단계(일관성) 동안에도
    # 스텝퍼가 최종 위반 수를 표시하도록(마지막 청크 반영 포함).
    if on_progress and total:
        on_progress(
            ProgressEvent(
                stage="attention",
                detail=f"주의 항목 검사 완료 ({len(issues)}건)",
                current=total,
                total=total,
                attention_count=len(issues),
            )
        )

    log.info("report_checker 주의항목 위반 count=%d", len(issues))
    return issues
