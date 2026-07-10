"""일관성(수치/기술) 검사 — 3단계 하이브리드.

Step 1: 청크별 사실(Fact) 추출 (LLM)
Step 2: Python dict 로 전체 교차 비교 → 불일치 후보 (컨텍스트 윈도우 무관)
Step 3: 불일치 후보를 LLM 이 검증 (진짜 오류만 채택)

사용자 사전의 정합성 어서션(assertion_groups)은 "이 이름들은 같은 항목이니 값이
일치해야 한다"는 선언이다. 라벨이 달라(추출기가 다르게 표기해도) 서로 다른 키로
흩어진 항목을 강제로 한 버킷에 모아 교차비교하고, 값이 다르면 LLM 판정을 건너뛰고
불일치로 확정 보고한다(사용자 의도 존중).
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


def _assertion_matchers(dictionary: UserDictionary) -> list[tuple[str, list[str]]]:
    """정합성 어서션을 (표시용 대표 라벨, [정규화된 매칭어들]) 목록으로 변환."""
    matchers: list[tuple[str, list[str]]] = []
    for group in dictionary.assertion_groups:
        terms = [t for t in group if t and t.strip()]
        if not terms:
            continue
        norm = [normalize_key(t) for t in terms]
        norm = [t for t in norm if t]
        if norm:
            matchers.append((terms[0], norm))
    return matchers


def _match_assertion(nk: str, matchers: list[tuple[str, list[str]]]) -> int:
    """정규화된 키 nk 가 속하는 어서션 그룹 인덱스. 없으면 -1.

    부분 문자열(양방향 포함) 매칭 → 추출기가 라벨을 조금 다르게 붙여도
    같은 항목으로 묶는다. (예: 어서션 '지원금' 이 '연구지원금' 을 포함)
    """
    for i, (_label, terms) in enumerate(matchers):
        for t in terms:
            if t and (t in nk or nk in t):
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
    - 어서션 그룹: 라벨이 달라도 매칭되는 사실을 한 버킷에 모아 강제 교차비교하고,
      후보에 asserted=True 표시(검증 단계에서 LLM 판정 없이 확정).

    반환: [{"id":.., "key":.., "occurrences":[...], "asserted": bool}]
    """
    dictionary = dictionary or UserDictionary()
    matchers = _assertion_matchers(dictionary)

    # bucket_key → {asserted, label, values: {normalized_value: [Fact,...]}}
    buckets: dict[str, dict] = defaultdict(
        lambda: {"asserted": False, "label": "", "values": defaultdict(list)}
    )
    for fact in facts:
        nk = normalize_key(fact.key)
        nv = normalize_value(fact.value)
        if not nk or not nv:
            continue
        gi = _match_assertion(nk, matchers)
        if gi >= 0:
            bkey = f"__assert__{gi}"
            buckets[bkey]["asserted"] = True
            buckets[bkey]["label"] = matchers[gi][0]
        else:
            bkey = nk
        buckets[bkey]["values"][nv].append(fact)

    conflicts: list[dict] = []
    for bkey, b in buckets.items():
        value_groups = b["values"]
        if len(value_groups) <= 1:
            continue
        if b["asserted"] and b["label"]:
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
                "asserted": b["asserted"],
            }
        )

    conflicts.sort(key=lambda x: x["occurrences"][0]["page"])
    # 검증 단계에서 안전하게 되찾을 수 있도록 안정적 id 부여
    for i, c in enumerate(conflicts):
        c["id"] = i
    n_assert = sum(1 for c in conflicts if c["asserted"])
    log.info(
        "report_checker 불일치 후보 count=%d (어서션 %d)", len(conflicts), n_assert
    )
    return conflicts


def validate_conflicts(
    conflicts: list[dict],
    on_progress: ProgressCb = None,
    cancel_check=None,
    usage=None,
) -> list[ConsistencyError]:
    """불일치 후보 확정.

    - 어서션 후보(asserted): 사용자가 "값이 일치해야 한다"고 선언 → LLM 판정 없이 확정.
    - 일반 후보: LLM 이 진짜 오류인지 배치 검증.
    """
    if not conflicts:
        return []

    cfg = get_config()
    all_errors: list[ConsistencyError] = []

    # 1) 어서션 후보 — 즉시 확정 (사용자 의도 존중)
    asserted = [c for c in conflicts if c.get("asserted")]
    for c in asserted:
        occ = c["occurrences"]
        values = list({o["value"] for o in occ})
        pages = sorted({o["page"] for o in occ})
        detail = " / ".join(f"{o['page']}p: {o['value']}" for o in occ)
        all_errors.append(
            ConsistencyError(
                pages=pages,
                key=c["key"],
                values=values,
                inconsistent_content=f"'{c['key']}' 값이 페이지마다 다릅니다",
                reason=f"[사용자 지정 정합성 항목] {detail}",
            )
        )

    # 2) 일반 후보 — LLM 검증
    normal = [c for c in conflicts if not c.get("asserted")]
    by_id = {c["id"]: c for c in normal}
    batch_size = cfg.validate_batch_size
    batches = [
        normal[i : i + batch_size] for i in range(0, len(normal), batch_size)
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
