"""report_maker 파일 저장/조회 (S3).

S3 의존을 이 파일 하나로 격리한다. 앱의 storage_service(범용 S3 유틸)를 재사용하되,
report_maker prefix 규칙(emp_no·템플릿 스코프)만 노출한다. 신원(emp_no)은 항상 서버가
세션에서 도출한 값이어야 한다(클라이언트 문자열 신뢰 금지 — legacy IDOR 해소).

prefix: {S3_KEY_PREFIX}/report_maker/{emp_no}/{template}/
  input/style_docs/{ts}_{name}   스타일 학습 참고 문서
  input/{ts}_{name}              주제 첨부(선택)
  meta/combined_style.json       AgentCore 미가용 시 스타일 폴백
  meta/analyzed.json             스타일 분석 완료 파일 이력(중복 분석 방지)
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path

from wellbot.constants import KST
from wellbot.services.files import storage_service
from wellbot.services.report_maker.parsing import to_safe_id

log = logging.getLogger(__name__)

_STYLE_DOCS = "input/style_docs"
_META_COMBINED = "meta/combined_style.json"     # 작성 스타일 정본(단일 편집기, 조회·생성용)
_META_ANALYZED = "meta/analyzed.json"
_META_EXTRACTED = "meta/style_extracted.json"   # 스타일에 이미 반영(추출)된 문서 basename 목록
# 아래 3개는 구(2-레이어) 데이터의 잔여 사이드카 — delete_style 정리에서만 참조(하위호환).
_META_DOC_DESCS = "meta/style_doc_descs.json"
_META_DOC_BASE = "meta/style_doc_base.json"
_META_MANUAL = "meta/style_manual.json"


def _base_prefix() -> str:
    root = os.environ.get("S3_KEY_PREFIX", "files").strip("/")
    return f"{root}/report_maker" if root else "report_maker"


def template_prefix(emp_no: str, template: str) -> str:
    # template 을 안전 ID 로 정규화 — 슬래시·한글·빈값으로 인한 malformed/중첩 key 방지
    # (memory.actor_id_for 의 to_safe_id 와 일관). emp_no 는 항상 서버 도출값.
    return f"{_base_prefix()}/{emp_no}/{to_safe_id(template)}/"


def emp_prefix(emp_no: str) -> str:
    """emp_no 의 report_maker S3 프리픽스 전체(모든 템플릿 포함) — 초기화/정리용."""
    return f"{_base_prefix()}/{emp_no}/"


def _safe_name(filename: str) -> str:
    """경로 조작 차단 — basename 만 취한다 (H1/H2)."""
    return os.path.basename(filename or "").strip() or "file"


def owns_key(key: str, emp_no: str, template: str) -> bool:
    """S3 key 가 (emp_no, template) 스코프에 속하는지 검증.

    업로드 API 가 클라이언트에 돌려준 key 를 재소비(다운로드)하기 전에 호출한다.
    key 는 클라이언트가 조작할 수 있으므로 항상 서버 도출 emp_no/template 로
    스코프를 재검증해야 한다(읽기 경로 IDOR 방지). emp_no/template 이 비면 거부.
    """
    if not key or not emp_no or not template:
        return False
    return key.startswith(template_prefix(emp_no, template))


# ──────────────────────────────────────────────────────────────
# 입력 파일
# ──────────────────────────────────────────────────────────────
def save_style_doc(emp_no: str, template: str, filename: str, data: bytes) -> str:
    """스타일 학습용 참고 문서를 S3 에 저장하고 key 반환."""
    ts = datetime.now(tz=KST).strftime("%y%m%d%H%M%S")
    key = f"{template_prefix(emp_no, template)}{_STYLE_DOCS}/{ts}_{_safe_name(filename)}"
    storage_service.upload_bytes(data, key, content_type="application/octet-stream")
    log.info("스타일 문서 저장 emp_no=%s key=%s", emp_no, key)
    return key


def save_topic_file(emp_no: str, template: str, filename: str, data: bytes) -> str:
    """주제 첨부(이미지/문서)를 S3 에 저장하고 key 반환."""
    ts = datetime.now(tz=KST).strftime("%y%m%d%H%M%S")
    key = f"{template_prefix(emp_no, template)}input/{ts}_{_safe_name(filename)}"
    storage_service.upload_bytes(data, key, content_type="application/octet-stream")
    log.info("주제 첨부 저장 emp_no=%s key=%s", emp_no, key)
    return key


def list_style_docs(emp_no: str, template: str) -> list[str]:
    """스타일 학습 문서 key 목록 (최근순)."""
    prefix = f"{template_prefix(emp_no, template)}{_STYLE_DOCS}/"
    metas = storage_service.list_objects_with_meta(prefix)
    metas.sort(key=lambda o: o.get("last_modified") or 0, reverse=True)
    return [o["key"] for o in metas]


def style_doc_name(key: str) -> str:
    """스타일 문서 key 에서 원본 파일명 복원 ('{ts}_{name}' → name)."""
    base = os.path.basename(key)
    # save_style_doc 규약: '{yymmddHHMMSS}_{원본파일명}'. 앞의 타임스탬프만 제거.
    parts = base.split("_", 1)
    return parts[1] if len(parts) == 2 and parts[0].isdigit() else base


def list_style_doc_names(emp_no: str, template: str) -> list[str]:
    """스타일 학습에 올린 원본 파일명 목록(최근순)."""
    return [style_doc_name(k) for k in list_style_docs(emp_no, template)]


def delete_style(emp_no: str, template: str) -> int:
    """작성 스타일 관련 S3 파일만 삭제(대화·주제 첨부는 보존). 삭제 객체 수 반환.

    삭제: input/style_docs/*, 정본/분석/추출마커 및 구(2-레이어) 잔여 사이드카.
    """
    prefix = template_prefix(emp_no, template)
    count = storage_service.delete_prefix(f"{prefix}{_STYLE_DOCS}/")
    for meta in (_META_COMBINED, _META_ANALYZED, _META_DOC_DESCS, _META_DOC_BASE,
                 _META_MANUAL, _META_EXTRACTED):
        key = f"{prefix}{meta}"
        if storage_service.object_exists(key):
            storage_service.delete_object(key)
            count += 1
    return count


def download_to_temp(s3_key: str) -> str:
    """S3 객체를 임시 파일로 내려받아 로컬 경로 반환 (파싱용).

    무작위 이름(mkstemp)으로 만들어 동시 사용자 간 충돌·경로 조작을 차단(H2/H3).
    호출측이 사용 후 os.remove 로 정리한다.
    """
    suffix = Path(s3_key).suffix.lower()
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="rptmk_")
    os.close(fd)
    storage_service.download_to_file(s3_key, Path(path))
    return path


# ──────────────────────────────────────────────────────────────
# 스타일 폴백 (AgentCore 미가용 시)
# ──────────────────────────────────────────────────────────────
def load_combined_style(emp_no: str, template: str) -> str:
    key = f"{template_prefix(emp_no, template)}{_META_COMBINED}"
    if not storage_service.object_exists(key):
        return ""
    try:
        data = json.loads(storage_service.download_bytes(key))
        return data.get("style_desc", "")
    except Exception:
        log.exception("combined_style.json 로드 실패 key=%s", key)
        return ""


def save_combined_style(emp_no: str, template: str, style_desc: str) -> None:
    """combined_style.json 전체 덮어쓰기(스타일 편집기 저장 경로)."""
    key = f"{template_prefix(emp_no, template)}{_META_COMBINED}"
    body = json.dumps({"style_desc": style_desc}, ensure_ascii=False).encode("utf-8")
    storage_service.upload_bytes(body, key, content_type="application/json; charset=utf-8")
    log.info("combined_style 저장 emp_no=%s template=%s", emp_no, template)


# ── 메타 JSON 헬퍼 (추출 마커 등) ──
def _load_json(emp_no: str, template: str, meta_key: str, default):
    key = f"{template_prefix(emp_no, template)}{meta_key}"
    if not storage_service.object_exists(key):
        return default
    try:
        return json.loads(storage_service.download_bytes(key))
    except Exception:
        log.exception("메타 로드 실패 key=%s", key)
        return default


def _save_json(emp_no: str, template: str, meta_key: str, data) -> None:
    key = f"{template_prefix(emp_no, template)}{meta_key}"
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    storage_service.upload_bytes(body, key, content_type="application/json; charset=utf-8")


def delete_style_doc_file(emp_no: str, template: str, basename: str) -> bool:
    """참고 문서 원본 파일 하나를 S3 에서 삭제(basename = '{ts}_{name}'). 삭제 성공 시 True."""
    key = f"{template_prefix(emp_no, template)}{_STYLE_DOCS}/{_safe_name(basename)}"
    if storage_service.object_exists(key):
        storage_service.delete_object(key)
        return True
    return False


def load_extracted_docs(emp_no: str, template: str):
    """스타일에 이미 반영(추출)된 문서 basename 목록. 마커 파일이 없으면 None(미초기화)."""
    data = _load_json(emp_no, template, _META_EXTRACTED, None)
    return data if isinstance(data, list) else None


def save_extracted_docs(emp_no: str, template: str, basenames) -> None:
    """추출 완료 문서 basename 목록 저장(전체 덮어쓰기)."""
    _save_json(emp_no, template, _META_EXTRACTED, list(basenames))


# ──────────────────────────────────────────────────────────────
# 분석 이력 (중복 분석 방지)
# ──────────────────────────────────────────────────────────────
def get_analyzed_history(emp_no: str, template: str) -> set[str]:
    key = f"{template_prefix(emp_no, template)}{_META_ANALYZED}"
    if not storage_service.object_exists(key):
        return set()
    try:
        data = json.loads(storage_service.download_bytes(key))
        return set(data.get("analyzed", []))
    except Exception:
        log.exception("analyzed.json 로드 실패 key=%s", key)
        return set()


def save_analyzed_history(emp_no: str, template: str, analyzed: set[str]) -> None:
    key = f"{template_prefix(emp_no, template)}{_META_ANALYZED}"
    body = json.dumps({"analyzed": sorted(analyzed)}, ensure_ascii=False).encode("utf-8")
    storage_service.upload_bytes(body, key, content_type="application/json; charset=utf-8")


def delete_template_files(emp_no: str, template: str) -> int:
    """템플릿의 모든 S3 파일 삭제. 삭제 개수 반환."""
    return storage_service.delete_prefix(template_prefix(emp_no, template))
