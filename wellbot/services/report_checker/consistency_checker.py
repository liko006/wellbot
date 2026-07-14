"""일관성(수치/기술) 검사 — 3단계 하이브리드.

Step 1: 청크별 사실(Fact) 추출 (LLM)
Step 2: Python dict 로 전체 교차 비교 → 불일치 후보 (컨텍스트 윈도우 무관)
Step 3: 불일치 후보를 LLM 이 검증 (진짜 오류만 채택)

사용자 사전의 동일 항목 별칭(alias_groups)은 "총 금액 / 합계 금액 / Total" 처럼
같은 항목을 다르게 기입한 표기 변형들을 하나로 묶는 것이다. 라벨이 달라 서로 다른
키로 흩어진 사실을 한 버킷에 모아 값을 교차비교하게 한다. 값 불일치 자체는 이후
LLM 검증(별도/연결·계획/실적 등 정당한 차이는 제외)을 그대로 거친다.
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
4. 그러나 범위·구분·기준이 다르면 서로 다른 항목이므로 key 에 그 구분을 반드시 포함하세요.
   같은 지표라도 아래가 다르면 다른 key 로 추출:
   - 재무제표 범위: 별도(개별) vs 연결  → 예: "별도 영업활동현금흐름", "연결 영업활동현금흐름"
   - 계획 vs 실적, 당기 vs 전기, 연간 vs 분기, 부서·사업·법인별 등
   (스코프를 떼고 뭉뚱그리면 성격이 다른 값이 오탐으로 잡히므로 금지)
5. 일반적인 서술문(배경, 목적 등)은 추출하지 마세요 — 수치/정의만

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
- 진짜 오류: 완전히 동일한 항목·범위·기준인데 값이 서로 다른 경우 → include: true
- 무시해도 됨(include: false): 범위·구분·기준이 달라 값이 다른 게 당연한 경우.
  예) 별도(개별) vs 연결 재무제표(예: "현금흐름표-영업활동현금흐름" vs
      "연결현금흐름표-영업활동현금흐름"), 계획 vs 실적, 당기 vs 전기,
      연간 vs 분기, 부서·사업·법인별 구분 등.
  * key 나 문장에서 범위·구분을 나타내는 수식어(별도/연결/계획/실적 등)가 다르면
    같은 지표여도 서로 다른 항목이므로 오류가 아님.

후보에 "note" 필드가 있으면, 사용자가 그 항목들을 '동일 항목(표기만 다름)'으로 지정한
것이니 라벨이 달라도 같은 항목으로 간주하고 값만 비교하세요. (단 그 경우에도 범위·기준이
정말 다르면 오류가 아닐 수 있음)

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


def normalize_value(v: str) -> str:
    """값 정규화: 소문자 + 공백/콤마 제거 (표기 차이 흡수)."""
    v = v.strip().lower()
    v = re.sub(r"\s+", "", v)
    v = re.sub(r",", "", v)
    return v


def normalize_key(k: str) -> str:
    """키 정규화: 소문자 + 공백/특수문자 제거."""
    nk = k.strip().lower()
    nk = re.sub(r"[\s_\-·•]", "", nk)
    return nk


def _alias_groups(dictionary: UserDictionary) -> list[tuple[str, set[str]]]:
    """동일 항목 별칭을 (표시용 대표 라벨, {정규화된 별칭들}) 목록으로 변환."""
    groups: list[tuple[str, set[str]]] = []
    for group in dictionary.alias_groups:
        terms = [t for t in group if t and t.strip()]
        norm = {normalize_key(t) for t in terms if normalize_key(t)}
        if len(norm) >= 2:
            groups.append((terms[0], norm))
    return groups


def _match_alias(nk: str, groups: list[tuple[str, set[str]]]) -> int:
    """정규화된 키 nk 가 속하는 별칭 그룹 인덱스. 없으면 -1.

    정확 일치(정규화 후 동일) 매칭 — 사용자가 나열한 표기 변형과 같은 키만 묶는다.
    (부분일치는 '금액' 이 모든 금액 항목을 흡수하는 과병합을 유발하므로 쓰지 않음)
    """
    for i, (_label, terms) in enumerate(groups):
        if nk in terms:
            return i
    return -1


def extract_facts(
    pages: dict[int, str],
    on_progress: ProgressCb = None,
    cancel_check=None,
    usage=None,
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
            raw = call_model(f"다음 보고서에서 핵심 정보를 추출하세요:\n\n{text}", EXTRACT_SYSTEM, usage=usage)
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

    - 일반 키: 정규화된 키가 같은 사실끼리 값 비교.
    - 별칭 그룹: 사용자가 동일 항목으로 지정한 표기 변형들을 한 버킷에 모아 교차비교.
      후보에 aliased=True 표시(검증 단계에서 LLM 에 '동일 항목' 힌트 제공).

    반환: [{"id":.., "key":.., "occurrences":[...], "aliased": bool}]
    """
    dictionary = dictionary or UserDictionary()
    groups = _alias_groups(dictionary)

    # bucket_key → {aliased, label, values: {normalized_value: [Fact,...]}}
    buckets: dict[str, dict] = defaultdict(
        lambda: {"aliased": False, "label": "", "values": defaultdict(list)}
    )
    for fact in facts:
        nk = normalize_key(fact.key)
        nv = normalize_value(fact.value)
        if not nk or not nv:
            continue
        gi = _match_alias(nk, groups)
        if gi >= 0:
            bkey = f"__alias__{gi}"
            buckets[bkey]["aliased"] = True
            buckets[bkey]["label"] = groups[gi][0]
        else:
            bkey = nk
        buckets[bkey]["values"][nv].append(fact)

    conflicts: list[dict] = []
    for bkey, b in buckets.items():
        value_groups = b["values"]
        if len(value_groups) <= 1:
            continue
        if b["aliased"] and b["label"]:
            original_key = b["label"]
        else:
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
            {
                "key": original_key,
                "normalized_key": bkey,
                "occurrences": occurrences,
                "aliased": b["aliased"],
            }
        )

    conflicts.sort(key=lambda x: x["occurrences"][0]["page"])
    # 검증 단계에서 안전하게 되찾을 수 있도록 안정적 id 부여
    for i, c in enumerate(conflicts):
        c["id"] = i
    n_alias = sum(1 for c in conflicts if c["aliased"])
    log.info(
        "report_checker 불일치 후보 count=%d (별칭병합 %d)", len(conflicts), n_alias
    )
    return conflicts


def validate_conflicts(
    conflicts: list[dict],
    on_progress: ProgressCb = None,
    cancel_check=None,
    usage=None,
) -> list[ConsistencyError]:
    """불일치 후보를 LLM 으로 검증 (배치).

    별칭 그룹으로 병합된 후보(aliased)에는 "사용자가 동일 항목으로 지정(표기만 상이)"
    힌트를 함께 넘겨, LLM 이 라벨이 다르다는 이유만으로 서로 다른 항목이라 오판하지
    않게 한다. 그래도 계획/실적·별도/연결 같은 정당한 차이는 LLM 이 걸러낸다.
    """
    if not conflicts:
        return []

    cfg = get_config()
    all_errors: list[ConsistencyError] = []

    by_id = {c["id"]: c for c in conflicts}
    batch_size = cfg.validate_batch_size
    batches = [
        conflicts[i : i + batch_size] for i in range(0, len(conflicts), batch_size)
    ]
    total = len(batches)

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
        # LLM 에는 id/key/occurrences (+ 별칭 힌트) 전달
        payload = []
        for c in batch:
            item = {"id": c["id"], "key": c["key"], "occurrences": c["occurrences"]}
            if c.get("aliased"):
                item["note"] = "사용자가 동일 항목으로 지정함(표기만 다를 뿐 같은 항목)"
            payload.append(item)
        prompt = (
            "다음은 보고서에서 발견된 불일치 후보입니다. 진짜 오류인지 판단해주세요:\n\n"
            + json.dumps(payload, ensure_ascii=False, indent=2)
        )
        try:
            raw = call_model(prompt, VALIDATE_SYSTEM, usage=usage)
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
