"""표기 일관성 검사.

같은 개념을 문서 안에서 다르게 표기한 경우(띄어쓰기/철자/대소문자)를 검출한다.
값 일관성(수치 불일치)과는 별개로, 표기 방식의 흔들림만 본다.
예: '총 금액'(3p) 와 '총금액'(22p), 'WellBot' 와 'Wellbot'.

방식(일관성 검사와 동일한 2단계, 크로스 페이지 안전):
  Step 1: 청크별로 주요 용어를 '원문 표기 그대로' + 페이지로 추출 (LLM)
  Step 2: Python 으로 공백/대소문자 무시 그룹핑 → 한 그룹에 표기가 2가지 이상이면
          표기 불일치로 보고 (LLM 재검증 없이 결정론적)
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import defaultdict
from collections.abc import Callable

from wellbot.services.report_checker.bedrock import call_model, parse_json_response
from wellbot.services.report_checker.config import get_config
from wellbot.services.report_checker.models import (
    AnalysisCancelled,
    NotationIssue,
    ProgressEvent,
)

log = logging.getLogger(__name__)

ProgressCb = Callable[[ProgressEvent], None] | None

EXTRACT_SYSTEM = """당신은 문서 표기 검수 전문가입니다.
주어진 텍스트에서 표기가 흔들릴 수 있는 '핵심 용어'를 원문에 적힌 표기 그대로 추출하세요.
대상: 고유명사, 제품·시스템·조직명, 합성 명사, 전문용어, 자주 반복되는 항목명 등.
제외: 흔한 일반 단어, 조사, 수치·금액 그 자체.

중요: term 은 반드시 원문에 나온 '표기 그대로'(띄어쓰기·대소문자 유지) 적으세요.
정규화하거나 다듬지 마세요.

JSON 외 다른 텍스트는 절대 포함하지 마세요. 없으면 [] 반환.

응답 형식:
[
  {"page": <페이지 번호(정수)>, "term": "<원문 표기 그대로>"}
]"""


def _group_key(surface: str) -> str:
    """그룹핑 키: 공백 전부 제거 + 소문자 (표기 차이를 흡수해 같은 개념 묶기)."""
    return re.sub(r"\s+", "", surface).lower()


def _display_form(surface: str) -> str:
    """표시/구분용 표면형: 양끝 공백 제거 + 내부 연속 공백 1칸으로."""
    return re.sub(r"\s+", " ", surface.strip())


def extract_terms(
    pages: dict[int, str],
    on_progress: ProgressCb = None,
    cancel_check=None,
    usage=None,
) -> list[tuple[int, str]]:
    """청크별로 (page, 표면형 용어) 목록 추출."""
    cfg = get_config()
    terms: list[tuple[int, str]] = []
    nums = sorted(pages.keys())
    size = cfg.extract_chunk_size
    chunks = [nums[i : i + size] for i in range(0, len(nums), size)]
    total = len(chunks)

    for idx, chunk in enumerate(chunks, 1):
        if cancel_check and cancel_check():
            raise AnalysisCancelled()
        if on_progress:
            on_progress(
                ProgressEvent(
                    stage="notation",
                    detail=f"표기 수집 {idx}/{total} (p{chunk[0]}~p{chunk[-1]})",
                    current=idx,
                    total=total,
                )
            )
        text = "\n\n".join(f"=== 페이지 {p} ===\n{pages[p]}" for p in chunk)
        try:
            raw = call_model(f"다음 텍스트에서 핵심 용어를 표기 그대로 추출하세요:\n\n{text}", EXTRACT_SYSTEM, usage=usage)
            items = parse_json_response(raw)
            for it in items:
                term = str(it.get("term", "")).strip()
                if term:
                    terms.append((int(it.get("page", chunk[0])), term))
        except json.JSONDecodeError:
            log.warning("report_checker 표기수집 청크 JSON 파싱 실패 chunk=%s", chunk)
        except Exception as e:
            log.warning("report_checker 표기수집 청크 실패 chunk=%s err=%s", chunk, e)
        time.sleep(cfg.call_interval_sec)

    return terms


def find_notation_conflicts(terms: list[tuple[int, str]]) -> list[NotationIssue]:
    """공백/대소문자 무시 그룹 내에 표기가 2가지 이상이면 표기 불일치로 판정."""
    # group_key → display_form → set(pages)
    groups: dict[str, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))
    for page, surface in terms:
        gk = _group_key(surface)
        if not gk:
            continue
        groups[gk][_display_form(surface)].add(page)

    issues: list[NotationIssue] = []
    for _gk, forms in groups.items():
        if len(forms) < 2:
            continue  # 표기 변형 1개뿐 → 문제 없음
        variants = [
            {"form": form, "pages": sorted(pages)} for form, pages in forms.items()
        ]
        # 가장 이른 페이지 순으로
        variants.sort(key=lambda v: v["pages"][0] if v["pages"] else 0)
        concept = variants[0]["form"]
        issues.append(NotationIssue(concept=concept, variants=variants))

    issues.sort(key=lambda i: min((p for v in i.variants for p in v["pages"]), default=0))
    log.info("report_checker 표기 불일치 count=%d", len(issues))
    return issues


def check_notation(
    pages: dict[int, str],
    on_progress: ProgressCb = None,
    cancel_check=None,
    usage=None,
) -> list[NotationIssue]:
    """표기 일관성 검사 (추출 → 그룹 판정)."""
    terms = extract_terms(pages, on_progress, cancel_check, usage)
    issues = find_notation_conflicts(terms)
    # 최종 카운트 통지 (스텝퍼 라이브 반영)
    if on_progress:
        on_progress(
            ProgressEvent(
                stage="notation",
                detail=f"표기 일관성 검사 완료 ({len(issues)}건)",
                current=1,
                total=1,
                notation_count=len(issues),
            )
        )
    return issues
