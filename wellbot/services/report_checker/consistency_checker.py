"""일관성(수치/기술) 검사 — 3단계 하이브리드.

Step 1: 청크별 사실(Fact) 추출 (LLM)
Step 2: Python dict 로 전체 교차 비교 → 불일치 후보 (컨텍스트 윈도우 무관)
Step 3: 불일치 후보를 LLM 이 검증 (진짜 오류만 채택)

사용자 사전의 동의어 그룹(synonym_groups)은 키/값 정규화 시 대표어로 합쳐,
표기만 다른 동일 개념이 불일치로 오탐되지 않게 한다.
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
    ConsistencyError,
    Fact,
    ProgressEvent,
    UserDictionary,
)

log = logging.getLogger(__name__)

ProgressCb = Callable[[ProgressEvent], None] | None

EXTRACT_SYSTEM = """당신은 보고서 정보 추출 전문가입니다.
주어진 텍스트에서 수치, 금액, 날짜, 비율, 점수, 코어 피처, 고유명사 등
다른 페이지와 비교 시 불일치 가능성이 있는 항목을 추출하세요.

규칙:
1. key 는 항목을 식별하는 짧고 일관된 이름 (예: "지원비", "이사비", "참여기업수", "사업기간")
2. value 는 해당 수치/내용 (단위 포함, 예: "1,200만원", "3개사", "2023.01~2024.12")
3. 같은 개념이면 key 를 동일하게 써야 함 (예: "총예산"과 "총 예산"은 "총예산"으로 통일)
4. 일반적인 서술문(배경, 목적 등)은 추출하지 마세요 — 수치/정의만

JSON 외 다른 텍스트는 절대 포함하지 마세요. 없으면 [] 반환.

응답 형식:
[
  {
    "page": <페이지 번호(정수)>,
    "key": "<항목명>",
    "value": "<값>",
    "sentence": "<관련 원문 문장 (50자 이내)>"
  }
]"""

VALIDATE_SYSTEM = """당신은 문서 교정 전문가입니다.
주어진 불일치 후보 목록을 검토하여 진짜 오류인지 판단하고 JSON으로만 응답하세요.

판단 기준:
- 진짜 오류: 같은 항목에 대해 서로 다른 값이 기재된 경우 → include: true
- 무시해도 됨: 맥락이 달라서 값이 다른 게 당연한 경우 (예: 계획 vs 실적) → include: false

JSON 외 다른 텍스트는 절대 포함하지 마세요.

각 후보에는 "id" 가 있습니다. 응답에 반드시 같은 "id" 를 그대로 넣으세요.

응답 형식:
[
  {
    "id": <후보 id(정수)>,
    "include": true 또는 false,
    "inconsistent_content": "<불일치 내용 한 줄 요약>",
    "reason": "<교정 필요 사유 — 몇 페이지에서 무엇이라 하고 몇 페이지에서 무엇이라 하는지>",
    "pages": [<페이지번호>, ...]
  }
]"""


def _build_synonym_map(dictionary: UserDictionary) -> dict[str, str]:
    """동의어 그룹을 {정규화된 변형 → 정규화된 대표어} 맵으로 변환.

    대표어는 각 그룹의 첫 번째 용어. 키/값 정규화 모두에 사용한다.
    """
    syn: dict[str, str] = {}
    for group in dictionary.synonym_groups:
        if len(group) < 2:
            continue
        canonical = _base_normalize(group[0])
        for term in group:
            syn[_base_normalize(term)] = canonical
    return syn


def _base_normalize(s: str) -> str:
    """공통 정규화: 소문자 + 공백/콤마 제거."""
    s = s.strip().lower()
    s = re.sub(r"\s+", "", s)
    s = re.sub(r",", "", s)
    return s


def normalize_value(v: str, synonym_map: dict[str, str] | None = None) -> str:
    """값 정규화 + 동의어 대표어 치환."""
    nv = _base_normalize(v)
    if synonym_map and nv in synonym_map:
        return synonym_map[nv]
    return nv


def normalize_key(k: str, synonym_map: dict[str, str] | None = None) -> str:
    """키 정규화: 공백/특수문자 제거 + 동의어 대표어 치환."""
    nk = k.strip().lower()
    nk = re.sub(r"[\s_\-·•]", "", nk)
    if synonym_map and nk in synonym_map:
        return synonym_map[nk]
    return nk


def extract_facts(
    pages: dict[int, str],
    on_progress: ProgressCb = None,
    cancel_check=None,
) -> list[Fact]:
    """전체 문서에서 핵심 사실 추출 (청크별)."""
    cfg = get_config()
    all_facts: list[Fact] = []
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
                    stage="consistency",
                    detail=f"사실 추출 {idx}/{total} (p{chunk[0]}~p{chunk[-1]})",
                    current=idx,
                    total=total,
                )
            )
        text = "\n\n".join(f"=== 페이지 {p} ===\n{pages[p]}" for p in chunk)
        try:
            raw = call_model(f"다음 보고서에서 핵심 정보를 추출하세요:\n\n{text}", EXTRACT_SYSTEM)
            items = parse_json_response(raw)
            for it in items:
                all_facts.append(
                    Fact(
                        page=int(it.get("page", chunk[0])),
                        key=str(it.get("key", "")).strip(),
                        value=str(it.get("value", "")).strip(),
                        sentence=str(it.get("sentence", "")).strip(),
                    )
                )
        except json.JSONDecodeError:
            log.warning("report_checker 사실추출 청크 JSON 파싱 실패 chunk=%s", chunk)
        except Exception as e:
            log.warning("report_checker 사실추출 청크 실패 chunk=%s err=%s", chunk, e)
        time.sleep(cfg.call_interval_sec)

    log.info("report_checker 추출 사실 count=%d", len(all_facts))
    return all_facts


def find_conflicts(
    facts: list[Fact],
    dictionary: UserDictionary | None = None,
) -> list[dict]:
    """Python dict 로 전체 비교 → 불일치 후보.

    반환: [{"id":.., "key":.., "occurrences":[{page,value,sentence}, ...]}]
    """
    dictionary = dictionary or UserDictionary()
    synonym_map = _build_synonym_map(dictionary)

    # normalized_key → normalized_value → [Fact, ...]
    key_map: dict[str, dict[str, list[Fact]]] = defaultdict(lambda: defaultdict(list))
    for fact in facts:
        nk = normalize_key(fact.key, synonym_map)
        nv = normalize_value(fact.value, synonym_map)
        if nk and nv:
            key_map[nk][nv].append(fact)

    conflicts: list[dict] = []
    for nk, value_groups in key_map.items():
        if len(value_groups) <= 1:
            continue
        # 가장 긴 원본 key 를 대표 이름으로
        original_key = max(
            (f.key for grp in value_groups.values() for f in grp),
            key=len,
        )
        occurrences: list[dict] = []
        for _nv, fact_list in value_groups.items():
            for f in fact_list:
                occurrences.append(
                    {"page": f.page, "value": f.value, "sentence": f.sentence}
                )
        occurrences.sort(key=lambda x: x["page"])
        conflicts.append(
            {"key": original_key, "normalized_key": nk, "occurrences": occurrences}
        )

    conflicts.sort(key=lambda x: x["occurrences"][0]["page"])
    # 검증 단계에서 안전하게 되찾을 수 있도록 안정적 id 부여
    for i, c in enumerate(conflicts):
        c["id"] = i
    log.info("report_checker 불일치 후보 count=%d", len(conflicts))
    return conflicts


def validate_conflicts(
    conflicts: list[dict],
    on_progress: ProgressCb = None,
    cancel_check=None,
) -> list[ConsistencyError]:
    """불일치 후보를 LLM 으로 검증 (배치)."""
    if not conflicts:
        return []

    cfg = get_config()
    by_id = {c["id"]: c for c in conflicts}
    batch_size = cfg.validate_batch_size
    batches = [
        conflicts[i : i + batch_size] for i in range(0, len(conflicts), batch_size)
    ]
    total = len(batches)
    all_errors: list[ConsistencyError] = []

    for idx, batch in enumerate(batches, 1):
        if cancel_check and cancel_check():
            raise AnalysisCancelled()
        if on_progress:
            on_progress(
                ProgressEvent(
                    stage="consistency",
                    detail=f"불일치 검증 {idx}/{total}",
                    current=idx,
                    total=total,
                    consistency_count=len(all_errors),
                )
            )
        # LLM 에는 id/key/occurrences 만 전달
        payload = [
            {"id": c["id"], "key": c["key"], "occurrences": c["occurrences"]}
            for c in batch
        ]
        prompt = (
            "다음은 보고서에서 발견된 불일치 후보입니다. 진짜 오류인지 판단해주세요:\n\n"
            + json.dumps(payload, ensure_ascii=False, indent=2)
        )
        try:
            raw = call_model(prompt, VALIDATE_SYSTEM)
            items = parse_json_response(raw)
            for it in items:
                if not it.get("include"):
                    continue
                # 원본 후보는 문자열 key 가 아니라 id 로 되찾는다(원본 버그 수정)
                match = by_id.get(it.get("id"))
                values = (
                    list({o["value"] for o in match["occurrences"]}) if match else []
                )
                all_errors.append(
                    ConsistencyError(
                        pages=it.get("pages", []),
                        key=(match["key"] if match else it.get("key", "")),
                        values=values,
                        inconsistent_content=it.get("inconsistent_content", ""),
                        reason=it.get("reason", ""),
                    )
                )
        except Exception as e:
            log.warning("report_checker 불일치 검증 배치 실패 batch=%d err=%s", idx, e)

    log.info("report_checker 확정 일관성 오류 count=%d", len(all_errors))
    return all_errors
