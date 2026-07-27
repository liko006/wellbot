"""
kb_retriever.py

Bedrock KB Retrieve 순수 로직 모듈.
공용 KB / 팀 KB / 개인 KB 를 선택적으로 조회하고 결과를 병합.

kb_modes (복수 선택 가능):
    ["shared"]              → 공용 KB 만
    ["team"]                → 팀 KB 만
    ["personal"]            → 개인 KB 만
    ["shared", "personal"]  → 공용 + 개인
    ["shared", "team", "personal"] → 전체

결과 병합: 각 KB 에서 top_k 개 조회 → score 기준 정렬 → 최종 top_k 개 반환
"""

import logging
import os
from functools import lru_cache
from typing import Any
from urllib.parse import unquote

import boto3

from wellbot.services.knowledgebase.config import get_kb_config
from wellbot.services.knowledgebase.kb_utils import shared_base
from wellbot.services.knowledgebase.personal_kb_manager import get_user_kb
from wellbot.services.knowledgebase.team_kb_manager import get_user_team_kb
from wellbot.constants import KB_AUTHORITY_FLOOR, KB_MIN_SCORE, KB_SEARCH_TOP_K

log = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# 환경 설정 (lazy — import 사이드이펙트 방지)
# ──────────────────────────────────────────────
def _region() -> str:
    return os.getenv("AWS_REGION", "ap-northeast-2")


@lru_cache(maxsize=1)
def _shared_kb_id() -> str:
    """공용 KB ID. 최초 호출 시 1회 로드 후 캐싱"""
    return get_kb_config().get("shared_kb", {}).get("kb_id", "")


@lru_cache(maxsize=1)
def _get_client():
    """Bedrock Agent Runtime 클라이언트 (싱글턴)"""
    return boto3.client("bedrock-agent-runtime", region_name=_region())


def _coerce_page(raw: Any) -> int | None:
    """메타데이터의 page 값을 int 로 정규화. 없거나 변환 불가 시 None.

    Bedrock 이 숫자를 int/float/str 중 무엇으로 돌려줄지 보장되지 않아 방어적으로 처리.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    try:
        return int(float(raw))
    except (ValueError, TypeError):
        return None


# ──────────────────────────────────────────────
# 인덱싱된 URI → 원본 URI 매핑
# ──────────────────────────────────────────────
def _map_to_original_uri(s3_uri: str) -> str:
    """Bedrock 이 반환하는 indexed 파일 URI 를 사용자에게 노출할 원본 URI 로 변환.

    pptx 의 경우: raw/{name}_pptx.json → originals/{name}.pptx
    xlsx(Upstage 변환) 의 경우: raw/{name}_xlsx.md → originals/{name}.xlsx
    pdf(Upstage 변환) 의 경우: raw/{name}_pdf.md → originals/{name}.pdf
    그 외 형식은 변경 없이 그대로 반환.
    """
    _CONVERTED_SUFFIXES = (("_pptx.json", ".pptx"), ("_xlsx.md", ".xlsx"), ("_pdf.md", ".pdf"))
    if "/raw/" in s3_uri:
        for conv_suffix, orig_ext in _CONVERTED_SUFFIXES:
            if s3_uri.endswith(conv_suffix):
                base, _, filename = s3_uri.rpartition("/")
                original_filename = filename.replace(conv_suffix, orig_ext, 1)
                original_base = base.replace("/raw", "/originals", 1)
                return f"{original_base}/{original_filename}"
    return s3_uri


# ──────────────────────────────────────────────
# 단일 KB Retrieve
# ──────────────────────────────────────────────
def _retrieve_single(
    kb_id: str,
    query: str,
    top_k: int,
    source: str,
) -> list[dict[str, Any]]:
    """단일 KB 에서 Retrieve 호출. 결과에 source 태그 부착"""
    try:
        resp = _get_client().retrieve(
            knowledgeBaseId=kb_id,
            retrievalQuery={"text": query},
            retrievalConfiguration={
                "vectorSearchConfiguration": {"numberOfResults": top_k}
            },
        )
        results = []
        for item in resp.get("retrievalResults", []):
            metadata = item.get("metadata", {})
            raw_uri = item.get("location", {}).get("s3Location", {}).get("uri", "")
            s3_uri = _map_to_original_uri(raw_uri)

            # Bedrock 메타데이터 title 이 변환본(_pptx.json/_xlsx.md/_pdf.md)을 가리키면
            # 부자연스러우므로 무시 → 매핑된 원본 URI 파일명으로 폴백.
            metadata_title = metadata.get("x-amz-bedrock-kb-document-title", "")
            if any(s in metadata_title for s in ("_pptx.json", "_xlsx.md", "_pdf.md")):
                metadata_title = ""

            title = (
                metadata_title
                or s3_uri.split("/")[-1].replace("%20", " ")
                or "Unknown Document"
            )
            results.append({
                "content":    item.get("content", {}).get("text", ""),
                "score":      round(item.get("score", 0.0), 4),
                "title":      title,
                "source_uri": s3_uri,
                "source":     source,
                # page 는 로컬 파싱(Lambda parse_pdf, pdfplumber) PDF 에만 존재.
                # Upstage 변환 PDF(_pdf.md→parse_md)·그 외 형식엔 없음(None) → 출처에 페이지 미표시.
                "page":       _coerce_page(metadata.get("page")),
            })
        return results
    except Exception:
        log.warning("KB Retrieve 실패: kb_id=%s source=%s", kb_id, source, exc_info=True)
        return []


# ──────────────────────────────────────────────
# 결과 병합
# ──────────────────────────────────────────────
def _shared_doc_key(source_uri: str) -> str | None:
    """공용(shared) KB source_uri → `"도메인/[서브/]파일명"` 논리 키. shared 아니면 None.

    `…/{shared_base()}/{도메인}/(raw|originals)/{서브?}/{파일}` 에서 `raw`/`originals`
    마커 세그먼트만 제거하고 나머지를 이어붙인다(percent-decode 포함).
    폴더는 도메인(표시용)일 뿐 권위와 무관 — 권위는 문서별 티어(config)로 결정.
    """
    marker = f"/{shared_base()}/"          # dev=/shared-dev/, prd=/shared/
    if marker not in source_uri:
        return None
    parts = [unquote(p) for p in source_uri.split(marker, 1)[-1].split("/") if p]
    for i, p in enumerate(parts):
        if p in ("raw", "originals"):
            parts = parts[:i] + parts[i + 1:]
            break
    return "/".join(parts) or None


def _tier_weight(tier: Any, tiers: dict) -> float:
    """티어 → 정렬 배수. yaml 이 int/str 어느 쪽으로 파싱해도 매칭."""
    if tier in tiers:
        return float(tiers[tier])
    if str(tier) in tiers:
        return float(tiers[str(tier)])
    return 1.0


def _authority_weight(source: str, source_uri: str, tiers: dict, docs: dict) -> float:
    """공용(shared) KB 문서의 권위 티어 배수를 반환. 미배정/미설정/비-shared 이면 1.0.

    문서별 속성(`docs`: `"도메인/[서브/]파일명"` → `{tier, dept, ...}`, 문서-major)에서
    tier 를 조회하고, 티어 사다리(`tiers`: 티어 → 배수, 0=최상)로 배수를 결정한다. 폴더는
    도메인(표시)일 뿐 권위와 무관 — 권위 순위가 도메인 경계를 가로지르므로 문서 단위 속성.
    """
    if source != "shared" or not docs:
        return 1.0
    key = _shared_doc_key(source_uri)
    if key is None:
        return 1.0
    attrs = docs.get(key)
    if not attrs:
        return 1.0
    tier = attrs.get("tier")
    if tier is None:
        return 1.0
    return _tier_weight(tier, tiers)


def _merge_results(
    all_results: list[dict],
    top_k: int,
    min_score: float = KB_MIN_SCORE,
    rank_offset: int = 0,
) -> list[dict[str, Any]]:
    """모든 KB 결과를 정렬 후 상위 top_k 개 반환.

    min_score 미만의 청크는 무관 결과로 간주하여 제외.
    이로써 LLM 이 '찾을 수 없음'으로 답변하는데도 저점수 출처가 표시되는 것을 방지.

    공용 KB 는 문서별 속성(config `shared_kb.docs`: 문서→{tier,dept}, +`authority_tiers` 사다리)로
    **정렬 순서만** 우대한다(soft; tier 기준). `item["score"]`(원본)은 그대로 유지 — UI·min_score·
    인용 매칭은 원본 기준. tier 미배정이면 배수 1.0 → 순수 score 정렬(현행과 동일). dept 는 표시 전용.

    rank_offset: 한 턴에 kb_search 가 여러 번 호출되면 rank 가 매번 1 부터 겹쳐 인용
    마커([N])가 여러 문서에 오귀속된다. 이전 호출들의 결과 수만큼 밀어 rank 를 전역 유니크하게 만든다.
    """
    filtered = [r for r in all_results if r.get("score", 0.0) >= min_score]

    cfg = get_kb_config().get("shared_kb", {})
    tiers = cfg.get("authority_tiers") or {}
    docs = cfg.get("docs") or {}          # 문서-major: "도메인/파일명" → {tier, dept, ...}

    # decorate-then-sort: 정렬 부작용 없이 가중 계산 + shared 결과 관측 로그
    scored: list[tuple[float, dict]] = []
    for r in filtered:
        src, uri = r.get("source", ""), r.get("source_uri", "")
        w = _authority_weight(src, uri, tiers, docs)
        eff = r["score"] * w if (w != 1.0 and r["score"] >= KB_AUTHORITY_FLOOR) else r["score"]
        if src == "shared":
            # 담당 부서(config docs[key].dept)를 컨텍스트 표시용으로 부착 → _format_context 가 (담당: X) 로 노출.
            attrs = docs.get(_shared_doc_key(uri))
            dept = attrs.get("dept") if attrs else None
            if dept:
                r["dept"] = dept
            # 배수 미적용(w=1.0)도 로깅 → 문서 키 매칭/티어 배정/인코딩 미스매치를 튜닝 시 노출
            log.debug(
                "rerank src=%s uri=%s raw=%.4f w=%.2f eff=%.4f",
                src, uri, r["score"], w, eff,
            )
        scored.append((eff, r))

    # stable sort → eff 동점 시 원래 순서(shared→team→personal, 각 score desc) 유지
    scored.sort(key=lambda t: t[0], reverse=True)
    merged = [r for _, r in scored[:top_k]]
    for idx, item in enumerate(merged, 1):
        item["rank"] = rank_offset + idx
    return merged


def _format_context(results: list[dict[str, Any]]) -> str:
    """병합된 결과를 LLM 프롬프트용 컨텍스트 문자열로 포맷팅"""
    if not results:
        return "관련 문서를 찾을 수 없습니다."

    source_labels = {
        "shared": "회사 문서",
        "team": "팀 문서",
        "personal": "내 문서",
    }
    lines = []
    for item in results:
        label = source_labels.get(item["source"], item["source"])
        # 담당 부서(shared, config 지정 시)를 제목 옆에 표시 → 규정 답변에서 LLM 이 해당 부서로 안내.
        dept = item.get("dept")
        dept_str = f" (담당: {dept})" if dept else ""
        # score 는 LLM 에 노출하지 않음: 재랭킹으로 순서는 eff 기준인데 표시 score 는 원본이라
        # 둘이 모순돼 LLM 이 순서 신호를 상쇄할 수 있음. 유도는 순서(rank)로만.
        lines.append(
            f"[{item['rank']}] [{label}] {item['title']}{dept_str}\n"
            f"{item['content']}"
        )
    return "\n\n".join(lines)


# ──────────────────────────────────────────────
# Public 인터페이스
# ──────────────────────────────────────────────
def retrieve(
    query: str,
    emp_no: str,
    kb_modes: list[str],    # ["shared", "team", "personal"]
    top_k: int = KB_SEARCH_TOP_K,
    rank_offset: int = 0,
) -> dict[str, Any]:
    """
    kb_modes 에 포함된 KB 들을 각각 조회하고 결과를 병합하여 반환.

    각 KB 에서 top_k 개씩 조회 → score 기준 정렬 → 최종 top_k 개 반환.

    rank_offset: 한 턴 내 다중 kb_search 호출 시 rank(=인용 마커 [N])를 이어붙여
    호출 간 번호 충돌·오귀속을 막기 위한 시작 오프셋. context 문자열도 이 rank 로 생성된다.

    반환값:
    {
        "results":  [{ rank, content, score, title, source_uri, source }, ...],
        "context":  "LLM 프롬프트용 컨텍스트 문자열",
        "sources_searched": { "shared": bool, "team": bool, "personal": bool },
    }
    """
    all_results = []
    searched = {"shared": False, "team": False, "personal": False}

    if "shared" in kb_modes:
        shared_kb_id = _shared_kb_id()
        if shared_kb_id:
            results = _retrieve_single(shared_kb_id, query, top_k, source="shared")
            all_results.extend(results)
            searched["shared"] = True
        else:
            log.warning("shared_kb.kb_id 미설정 (knowBase.yaml), 공용 KB 스킵")

    if "team" in kb_modes:
        team_record = get_user_team_kb(emp_no)
        if team_record:
            results = _retrieve_single(
                team_record["kb_id"], query, top_k, source="team"
            )
            all_results.extend(results)
            searched["team"] = True

    if "personal" in kb_modes:
        personal_record = get_user_kb(emp_no)
        if personal_record:
            results = _retrieve_single(
                personal_record["kb_id"], query, top_k, source="personal"
            )
            all_results.extend(results)
            searched["personal"] = True

    merged = _merge_results(all_results, top_k, rank_offset=rank_offset)
    context = _format_context(merged)

    return {
        "results": merged,
        "context": context,
        "sources_searched": searched,
    }
