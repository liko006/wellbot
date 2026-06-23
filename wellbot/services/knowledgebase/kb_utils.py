"""
kb_utils.py

KB 매니저 공통 인프라.
personal_kb_manager, team_kb_manager 에서 공유하는 상수/클라이언트/함수.

- 설정 상수 (S3 버킷, Lambda ARN 등)
- AWS 클라이언트 (s3, bedrock-agent, s3vectors)
- 파일 크기 검증
- xlsx/csv 분할 업로드
- pptx → json 변환 (Bedrock KB 미지원 형식 전처리)
- KB 생성/조회/Ingestion (kind="personal"|"team" 으로 분기)
"""

import io
import json
import logging
import os
import re
import tempfile
import time
from functools import lru_cache
from pathlib import Path
from typing import Optional

import boto3
import pandas as pd

from wellbot.constants import FILE_PARSER_MODE, KB_MAX_DOCS, PDF_VIA_UPSTAGE
from wellbot.services.files import storage_service
from wellbot.services.knowledgebase.config import get_kb_config

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 식별자
# ──────────────────────────────────────────────
AGNT_ID_KB    = "Agnt_KnowBase"
KB_INFO_SEP   = "||"

# kind → S3 경로 / KB 이름에 사용되는 base 토큰
_KB_KIND_PREFIX_BASE = {"personal": "users", "team": "teams"}
_KB_KIND_LABEL       = {"personal": "개인",  "team": "팀"}


# ──────────────────────────────────────────────
# 설정 / 리전 (lazy — import 사이드이펙트 방지)
# ──────────────────────────────────────────────
@lru_cache(maxsize=1)
def _cfg() -> dict:
    """personal_kb 섹션 (팀 KB 도 공유). 최초 호출 시 1회 로드 후 캐싱."""
    return get_kb_config()["personal_kb"]


def _region() -> str:
    return os.getenv("AWS_REGION", "ap-northeast-2")


def get_s3_bucket() -> str:
    """KB 파일 저장 버킷. personal/team 매니저가 업로드/삭제 시 사용."""
    return _cfg()["s3_bucket"]


# ──────────────────────────────────────────────
# AWS 클라이언트 (lazy — 최초 호출 시 1회 생성 후 캐싱)
# ──────────────────────────────────────────────
def _get_s3():
    """S3 클라이언트 — storage_service 의 region 설정 클라이언트 재사용"""
    return storage_service.get_client()


@lru_cache(maxsize=1)
def _get_bedrock_agent():
    return boto3.client("bedrock-agent", region_name=_region())


@lru_cache(maxsize=1)
def _get_s3vectors():
    return boto3.client("s3vectors", region_name=_region())

# ──────────────────────────────────────────────
# 상수
# ──────────────────────────────────────────────
ROWS_PER_SPLIT = 50_000          # xlsx/csv 행 기준 분할 단위

TABULAR_EXTS = {".xlsx", ".csv"}

# Bedrock KB 가 지원하지 않지만, 업로드 시 변환 처리하는 형식
CONVERTIBLE_EXTS = {".pptx"}


def get_originals_prefix(raw_prefix: str) -> str:
    """raw/ prefix 를 originals/ prefix 로 변환.

    pptx 등 변환이 필요한 파일의 원본은 Bedrock 의 inclusionPrefix(raw/) 밖에
    저장해서 ingestion 대상에서 제외. 다운로드와 문서 목록 조회용도로만 사용.

    예: 'users/123/raw/'  → 'users/123/originals/'
        'teams/A1/raw/'   → 'teams/A1/originals/'
    """
    return raw_prefix.rsplit("raw/", 1)[0] + "originals/"

SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".pptx", ".xlsx", ".csv",
    ".md", ".txt", ".json", ".html", ".htm",
}

MAX_FILE_SIZES = {
    ".txt":  30 * 1024 * 1024,   # 30MB
    ".md":   30 * 1024 * 1024,
    ".json": 30 * 1024 * 1024,
    ".csv":  None,               # 분할 업로드 — 제한 없음
    ".xlsx": None,
}
MAX_FILE_SIZE_DEFAULT = 100 * 1024 * 1024  # 100MB


# ──────────────────────────────────────────────
# 파일 크기 검증
# ──────────────────────────────────────────────
def validate_file_size(file_bytes: bytes, filename: str) -> None:
    """
    파일 형식별 크기 제한 검증.
    xlsx/csv 는 분할 업로드로 처리되므로 제한 없음.
    """
    ext = Path(filename).suffix.lower()
    limit = MAX_FILE_SIZES.get(ext, MAX_FILE_SIZE_DEFAULT)
    if limit is None:
        return
    if len(file_bytes) > limit:
        limit_mb = limit // (1024 * 1024)
        actual_mb = len(file_bytes) / (1024 * 1024)
        raise ValueError(
            f"파일 크기 초과: {filename} ({actual_mb:.1f}MB). "
            f"{ext} 파일은 {limit_mb}MB 이하만 업로드 가능합니다."
        )


# ──────────────────────────────────────────────
# 분할 업로드 (xlsx / csv)
# ──────────────────────────────────────────────
def cleanup_existing_parts(
    bucket: str, prefix: str, stem: str, ext: str,
) -> None:
    """
    동일 파일명의 기존 분할 파트를 S3 에서 삭제.
    재업로드 시 파트 수/시트 구성이 달라져서 오래된 파트가 남는 것을 방지.

    파트 명명: '{stem}_part{N}.ext' (csv·단일시트 xlsx) 또는
    '{stem}_{sheet}_part{N}.ext' (멀티시트 xlsx) 둘 다 매칭.
    """
    part_re = re.compile(rf"^{re.escape(stem)}(?:_.+)?_part\d+$")
    paginator = _get_s3().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            filename = key.split("/")[-1]
            if filename.endswith(ext) and part_re.match(Path(filename).stem):
                _get_s3().delete_object(Bucket=bucket, Key=key)


def _safe_sheet_slug(sheet_name: object) -> str:
    """시트명을 S3 키/파일명에 안전한 슬러그로 변환 (한글 보존, 특수문자 → _)."""
    slug = re.sub(r"[^\w가-힣]+", "_", str(sheet_name)).strip("_")
    return slug or "sheet"


def _upload_df_parts(
    bucket: str,
    prefix: str,
    df: "pd.DataFrame",
    part_stem: str,
    ext: str,
    sheet_name: object = None,
) -> list[str]:
    """단일 DataFrame 을 ROWS_PER_SPLIT 행 단위로 분할해 S3 업로드. 반환: URI 목록.

    행이 0개인 (헤더만 있는) 시트는 색인할 내용이 없으므로 스킵.
    xlsx 는 원본 시트명을 파트 내부 시트명으로 보존 → Lambda 가 sheet 메타 태깅.
    """
    uris: list[str] = []
    total_rows = len(df)
    if total_rows == 0:
        return uris
    for i, start in enumerate(range(0, total_rows, ROWS_PER_SPLIT)):
        chunk_df = df.iloc[start:start + ROWS_PER_SPLIT]
        split_filename = f"{part_stem}_part{i + 1}{ext}"
        buf = io.BytesIO()

        if ext == ".csv":
            chunk_df.to_csv(buf, index=False)
        else:
            xl_sheet = (str(sheet_name)[:31] if sheet_name else "") or "Sheet1"
            with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                chunk_df.to_excel(writer, index=False, sheet_name=xl_sheet)

        buf.seek(0)
        key = f"{prefix}{split_filename}"
        _get_s3().put_object(Bucket=bucket, Key=key, Body=buf.read())
        uris.append(f"s3://{bucket}/{key}")
    return uris


def split_and_upload_tabular(
    bucket: str,
    prefix: str,
    file_bytes: bytes,
    filename: str,
) -> list[str]:
    """
    xlsx/csv 를 ROWS_PER_SPLIT 행 단위로 분할해서 S3 에 저장.
    업로드 전 기존 분할 파트를 정리해서 오래된 데이터 잔류를 방지.

    xlsx 는 **모든 시트**를 각각 분할 업로드 (시트명 보존). 멀티시트면 파일명에
    시트 슬러그를 포함('{stem}_{sheet}_part{N}.xlsx'), 단일시트면 기존 명명 유지.
    반환: 업로드된 S3 URI 목록
    """
    ext = Path(filename).suffix.lower()
    stem = Path(filename).stem

    cleanup_existing_parts(bucket, prefix, stem, ext)

    if ext == ".csv":
        try:
            df = pd.read_csv(io.BytesIO(file_bytes))
        except UnicodeDecodeError:
            df = pd.read_csv(io.BytesIO(file_bytes), encoding="cp949")
        return _upload_df_parts(bucket, prefix, df, stem, ext)

    # xlsx: 전체 시트를 dict 로 읽어 시트별 분할 (sheet_name=None → {시트명: df})
    sheets = pd.read_excel(io.BytesIO(file_bytes), sheet_name=None)
    multi = len(sheets) > 1
    uris: list[str] = []
    for sheet_name, df in sheets.items():
        part_stem = f"{stem}_{_safe_sheet_slug(sheet_name)}" if multi else stem
        uris.extend(
            _upload_df_parts(bucket, prefix, df, part_stem, ext, sheet_name=sheet_name)
        )
    return uris


# ──────────────────────────────────────────────
# pptx → json 변환
# ──────────────────────────────────────────────
def pptx_to_dict(file_bytes: bytes) -> dict:
    """pptx 바이트를 슬라이드별 구조화 dict 로 추출 (제목/본문/표/노트).

    convert_pptx_to_json(개인·팀 업로드) 과 scripts/shared_kb_manager(공용 KB)
    가 공유하는 코어 추출 로직. 키는 'slide_{번호}_{제목}', 값은 슬라이드 텍스트.
    """
    from pptx import Presentation

    prs = Presentation(io.BytesIO(file_bytes))
    result: dict = {}

    for slide_num, slide in enumerate(prs.slides, 1):
        slide_title = "No Title"
        if slide.shapes.title and slide.shapes.title.has_text_frame:
            slide_title = slide.shapes.title.text.strip() or "No Title"

        parts = []

        for shape in slide.shapes:
            if shape.has_table:
                table = shape.table
                rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
                if rows:
                    headers = rows[0]
                    for row in rows[1:]:
                        row_text = " | ".join(
                            f"{headers[i]}: {cell}" for i, cell in enumerate(row)
                            if i < len(headers)
                        )
                        if row_text.strip():
                            parts.append(row_text)
            elif shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    text = para.text.strip()
                    if text:
                        parts.append(text)

        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                parts.append(f"[노트] {notes}")

        if parts:
            result[f"slide_{slide_num}_{slide_title}"] = "\n".join(parts)

    return result


def convert_pptx_to_json(file_bytes: bytes, filename: str) -> tuple[bytes, str]:
    """pptx 바이트를 슬라이드별 JSON 으로 변환 (Bedrock KB 미지원 형식 전처리).

    Lambda 의 parse_json 이 처리 가능하도록 업로드 전 변환.

    반환: (json_bytes, 변환된_파일명)
        예: ("report.pptx" → b'{...}', "report_pptx.json")
    """
    result = pptx_to_dict(file_bytes)
    stem = Path(filename).stem
    json_filename = f"{stem}_pptx.json"
    json_bytes = json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8")
    return json_bytes, json_filename


# ──────────────────────────────────────────────
# Upstage Document Parse → markdown 변환 (xlsx / pdf)
# ──────────────────────────────────────────────
def xlsx_via_upstage_enabled() -> bool:
    """xlsx 를 Upstage 로 변환할지 여부 (FILE_PARSER_MODE 가 upstage/hybrid 일 때).

    첨부파일 파서와 동일한 노브(FILE_PARSER_MODE)를 공유한다.
    local 모드면 기존 pandas 행 분할(split_and_upload_tabular)을 그대로 사용.
    """
    return (FILE_PARSER_MODE or "local").lower() in ("upstage", "hybrid")


def pdf_via_upstage_enabled() -> bool:
    """PDF 를 Upstage 로 변환할지 여부 (xlsx 와 독립된 PDF 전용 노브 PDF_VIA_UPSTAGE).

    개인/팀/공용 KB 업로드 전부에 적용. 끄면 원본 PDF 가 그대로 색인되어
    Lambda 의 pdfplumber 커스텀 파싱(parse_pdf)으로 폴백한다.
    """
    return bool(PDF_VIA_UPSTAGE)


def _convert_via_upstage(file_bytes: bytes, filename: str, out_suffix: str) -> tuple[bytes, str]:
    """파일을 Upstage Document Parse 로 markdown 변환 (xlsx/pdf 공용).

    변환된 markdown 은 Lambda 의 parse_md 가 청킹한다.
    반환: (markdown_bytes, f"{stem}{out_suffix}")
    Upstage 호출 실패/빈 결과 시 예외 전파 (호출자가 폴백 처리).
    """
    # 모듈 레벨 import 사이드이펙트 방지를 위해 지연 import
    from wellbot.services.files.file_parser import UpstageParser

    # 임시 파일을 원본 파일명으로 생성 (Upstage 로그/제목에 임의 임시명이 노출되지 않도록).
    # filename 은 업로드 basename 이라 경로 구분자가 없지만 방어적으로 .name 사용.
    safe_name = Path(filename).name or "upload"
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / safe_name
        tmp_path.write_bytes(file_bytes)
        parsed = UpstageParser().parse(tmp_path)

    md = parsed.text or ""
    if not md.strip():
        raise ValueError("Upstage 변환 결과가 비어 있습니다")
    stem = Path(filename).stem
    return md.encode("utf-8"), f"{stem}{out_suffix}"


def convert_xlsx_to_markdown(file_bytes: bytes, filename: str) -> tuple[bytes, str]:
    """xlsx 를 Upstage 로 markdown 변환 (병합·공백 많은 표를 견고하게 처리).
    예: report.xlsx → report_xlsx.md. 실패 시 예외 전파(호출자가 pandas 분할 폴백)."""
    return _convert_via_upstage(file_bytes, filename, "_xlsx.md")


def convert_pdf_to_markdown(file_bytes: bytes, filename: str) -> tuple[bytes, str]:
    """PDF 를 Upstage 로 markdown 변환 (이미지/스캔 내용까지 OCR+레이아웃 해석).
    예: report.pdf → report_pdf.md. 실패 시 예외 전파(호출자가 원본 PDF 색인 폴백)."""
    return _convert_via_upstage(file_bytes, filename, "_pdf.md")


# ──────────────────────────────────────────────
# KB 정보 인코딩 / 디코딩 (DB path_addr 컬럼용)
# ──────────────────────────────────────────────
def encode_kb_info(kb_id: str, data_source_id: str) -> str:
    return f"{kb_id}{KB_INFO_SEP}{data_source_id}"


def decode_kb_info(kb_info: str) -> tuple[str, str]:
    parts = kb_info.split(KB_INFO_SEP, 1)
    if len(parts) != 2:
        raise ValueError(f"잘못된 KB_INFO 형식: {kb_info}")
    return parts[0], parts[1]


# ──────────────────────────────────────────────
# S3 경로 헬퍼
# ──────────────────────────────────────────────
def raw_prefix(kind: str, owner: str) -> str:
    """kind='personal'→'users/{owner}/raw/', kind='team'→'teams/{owner}/raw/'."""
    return f"{_KB_KIND_PREFIX_BASE[kind]}/{owner}/raw/"


def processed_prefix(kind: str, owner: str) -> str:
    """processed/ prefix. raw_prefix 와 동일 규칙."""
    return f"{_KB_KIND_PREFIX_BASE[kind]}/{owner}/processed/"


# ──────────────────────────────────────────────
# Bedrock KB 생성 / 조회
# ──────────────────────────────────────────────
def create_vector_index(kind: str, owner: str) -> str:
    """S3 Vectors 인덱스 생성. 반환: index ARN."""
    resp = _get_s3vectors().create_index(
        vectorBucketName=_cfg()["s3_vector_bucket"],
        indexName=f"aiinno-bedrock-kb-{kind}-vector-index-{owner.lower()}",
        dataType="float32",
        dimension=1024,
        distanceMetric="cosine",
        metadataConfiguration={
            "nonFilterableMetadataKeys": ["AMAZON_BEDROCK_TEXT"]
        },
    )
    return resp["indexArn"]


def create_bedrock_kb(kind: str, owner: str, vector_index_arn: str) -> str:
    """Bedrock Knowledge Base 생성. 반환: knowledgeBaseId."""
    label = _KB_KIND_LABEL[kind]
    resp = _get_bedrock_agent().create_knowledge_base(
        name=f"aiinno-bedrock-kb-{kind}-{owner}",
        description=f"{owner}의 {label} Knowledge Base",
        roleArn=_cfg()["kb_role_arn"],
        knowledgeBaseConfiguration={
            "type": "VECTOR",
            "vectorKnowledgeBaseConfiguration": {
                "embeddingModelArn": (
                    f"arn:aws:bedrock:{_region()}::foundation-model/{_cfg()['embedding_model']}"
                ),
            },
        },
        storageConfiguration={
            "type": "S3_VECTORS",
            "s3VectorsConfiguration": {"indexArn": vector_index_arn},
        },
    )
    return resp["knowledgeBase"]["knowledgeBaseId"]

def wait_until_kb_ready(kb_id: str) -> None:
    """KB 가 ACTIVE 가 될 때까지 폴링."""
    cfg = _cfg()
    poll_timeout = cfg.get("kb_poll_timeout", 120)
    poll_interval = cfg.get("kb_poll_interval", 5)
    start = time.time()
    while time.time() - start < poll_timeout:
        resp = _get_bedrock_agent().get_knowledge_base(knowledgeBaseId=kb_id)
        status = resp["knowledgeBase"]["status"]
        if status == "ACTIVE":
            return
        if status == "FAILED":
            raise RuntimeError(f"KB 생성 실패: kb_id={kb_id}")
        time.sleep(poll_interval)
    raise TimeoutError(f"KB ACTIVE 대기 타임아웃: kb_id={kb_id}")


def create_data_source(kind: str, owner: str, kb_id: str) -> str:
    """KB 의 Data Source 생성. 반환: dataSourceId."""
    resp = _get_bedrock_agent().create_data_source(
        knowledgeBaseId=kb_id,
        name=f"aiinno-bedrock-kb-ds-{kind}-{owner}",
        dataSourceConfiguration={
            "type": "S3",
            "s3Configuration": {
                "bucketArn": f"arn:aws:s3:::{_cfg()['s3_bucket']}",
                "inclusionPrefixes": [raw_prefix(kind, owner)],
            },
        },
        vectorIngestionConfiguration={
            "chunkingConfiguration": {"chunkingStrategy": "NONE"},
            "customTransformationConfiguration": {
                "intermediateStorage": {
                    "s3Location": {
                        "uri": f"s3://{_cfg()['s3_intermediate_bucket']}/{processed_prefix(kind, owner)}",
                    },
                },
                "transformations": [{
                    "stepToApply": "POST_CHUNKING",
                    "transformationFunction": {
                        "transformationLambdaConfiguration": {"lambdaArn": _cfg()['lambda_arn']},
                    },
                }],
            },
        },
    )
    return resp["dataSource"]["dataSourceId"]


def find_existing_kb(kind: str, owner: str) -> Optional[dict]:
    """Bedrock API 로 이미 생성된 KB 검색 (DB 미등록 상태 방어).

    반환: {"kb_id", "data_source_id"} 또는 None.
    """
    kb_name = f"aiinno-bedrock-kb-{kind}-{owner}"
    try:
        paginator = _get_bedrock_agent().get_paginator("list_knowledge_bases")
        for page in paginator.paginate():
            for kb in page.get("knowledgeBaseSummaries", []):
                if kb["name"] == kb_name and kb["status"] == "ACTIVE":
                    kb_id = kb["knowledgeBaseId"]
                    ds_resp = _get_bedrock_agent().list_data_sources(knowledgeBaseId=kb_id)
                    ds_list = ds_resp.get("dataSourceSummaries", [])
                    if ds_list:
                        return {"kb_id": kb_id, "data_source_id": ds_list[0]["dataSourceId"]}
    except Exception:
        log.debug("KB 조회 실패 (무시): kb_name=%s", kb_name, exc_info=True)
    return None


# ──────────────────────────────────────────────
# Ingestion 실행 / 폴링
# ──────────────────────────────────────────────
def start_ingestion(kb_id: str, data_source_id: str) -> str:
    """Ingestion Job 실행. 반환: ingestion_job_id."""
    resp = _get_bedrock_agent().start_ingestion_job(
        knowledgeBaseId=kb_id,
        dataSourceId=data_source_id,
    )
    return resp["ingestionJob"]["ingestionJobId"]


def is_ingestion_in_progress(kb_id: str, data_source_id: str) -> bool:
    """진행 중인 ingestion job 이 있는지 확인 (팀 KB 동시성 방지용)."""
    try:
        resp = _get_bedrock_agent().list_ingestion_jobs(
            knowledgeBaseId=kb_id,
            dataSourceId=data_source_id,
            sortBy={"attribute": "STARTED_AT", "order": "DESCENDING"},
            maxResults=1,
        )
        jobs = resp.get("ingestionJobSummaries", [])
        if jobs and jobs[0]["status"] in ("STARTING", "IN_PROGRESS"):
            return True
    except Exception:
        log.debug(
            "ingestion 상태 조회 실패 (무시): kb_id=%s ds_id=%s",
            kb_id, data_source_id, exc_info=True,
        )
    return False


# ──────────────────────────────────────────────
# 누적 문서 수 카운트 / 상한 검증
# ──────────────────────────────────────────────
# xlsx/csv 분할본 _partN 을 base 로 합치기 위한 패턴
_PART_RE = re.compile(r"^(.*)_part\d+(\.(?:xlsx|csv))$", re.IGNORECASE)
# 변환본(원본은 originals/ 에 별도 보관) — 논리 문서 카운트에서 제외
_CONVERTED_SUFFIXES_COUNT = ("_pptx.json", "_xlsx.md", "_pdf.md")


def count_kb_docs(bucket: str, raw_prefix: str) -> int:
    """KB 의 논리적 문서 수(사용자가 올린 '파일' 수)를 센다.

    raw_prefix 예: 'users/{emp}/raw/' → root 'users/{emp}/' 아래 raw/ + originals/ 를
    스캔. 변환본(_pptx.json/_xlsx.md/_pdf.md)은 제외(원본을 originals/ 에서 셈),
    xlsx/csv 분할본(_partN)은 base 로 합침, raw/+originals/ 를 파일명으로 dedup.
    """
    root = raw_prefix.rsplit("raw/", 1)[0]  # users/{emp}/ 또는 teams/{dept}/
    logical: set[str] = set()
    paginator = _get_s3().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=root):
        for obj in page.get("Contents", []) or []:
            rel = obj["Key"][len(root):]          # raw/... 또는 originals/...
            sub, _, filename = rel.partition("/")
            if sub not in ("raw", "originals") or not filename:
                continue
            if filename.endswith(_CONVERTED_SUFFIXES_COUNT):
                continue                          # 변환본은 originals 원본으로 카운트
            m = _PART_RE.match(filename)
            logical.add(m.group(1) + m.group(2) if m else filename)
    return len(logical)


def _scope_from_prefix(prefix: str) -> Optional[str]:
    """업로드 prefix 로 KB scope 판별. 'users/'→personal, 'teams/'→team, 그 외 None."""
    if prefix.startswith("users/"):
        return "personal"
    if prefix.startswith("teams/"):
        return "team"
    return None  # shared 등은 상한 미적용


def enforce_kb_doc_limit(bucket: str, prefix: str, new_count: int) -> None:
    """누적 문서 수 상한 검증. 초과 시 ValueError (배치당 개수 제한과 별개).

    상한이 정의되지 않은 scope(shared 등)는 검증을 건너뛴다.
    """
    scope = _scope_from_prefix(prefix)
    cap = KB_MAX_DOCS.get(scope) if scope else None
    if not cap:
        return
    existing = count_kb_docs(bucket, prefix)
    if existing + new_count > cap:
        label = {"personal": "개인", "team": "팀"}.get(scope, scope)
        raise ValueError(
            f"{label} 지식베이스에는 최대 {cap}개까지 업로드할 수 있습니다. "
            f"(현재 {existing}개, 요청 {new_count}개)"
        )


# ──────────────────────────────────────────────
# 파일 업로드 / 삭제 (S3)
# ──────────────────────────────────────────────
def upload_files_to_kb(
    bucket: str,
    prefix: str,
    files: list[tuple[bytes, str]],
    with_rollback: bool = False,
) -> list[str]:
    """파일 목록을 S3 {bucket}/{prefix} 에 업로드. 최대 5개.

    - pptx: 원본은 originals/ 로, JSON 변환본은 raw/ 로 업로드
    - xlsx/csv: ROWS_PER_SPLIT 단위로 분할 업로드
    - 그 외: 단일 업로드 (형식별 크기 제한 검증)
    - with_rollback=True 시 도중 실패하면 부분 업로드분을 모두 삭제

    반환: 업로드된 S3 URI 목록 (pptx 의 originals/ 원본 URI 포함).
    """
    if len(files) > 5:
        raise ValueError(
            f"한 번에 최대 5개 파일만 업로드 가능합니다. (요청: {len(files)}개)"
        )
    # 누적 문서 수 상한 (개인/팀). 초과 시 ValueError.
    enforce_kb_doc_limit(bucket, prefix, len(files))
    for file_bytes, filename in files:
        ext = Path(filename).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"지원하지 않는 파일 형식: {filename}")
        validate_file_size(file_bytes, filename)

    uploaded_uris: list[str] = []
    try:
        for file_bytes, filename in files:
            ext = Path(filename).suffix.lower()

            if ext in CONVERTIBLE_EXTS:
                # 원본은 raw/ 밖의 originals/ 에 저장 (Bedrock 인덱싱 대상 제외)
                originals = get_originals_prefix(prefix)
                originals_key = f"{originals}{filename}"
                _get_s3().put_object(Bucket=bucket, Key=originals_key, Body=file_bytes)
                # 롤백 시 orphan 으로 남지 않도록 삭제 대상에 포함
                uploaded_uris.append(f"s3://{bucket}/{originals_key}")
                file_bytes, filename = convert_pptx_to_json(file_bytes, filename)
                ext = ".json"
            elif ext == ".pdf" and pdf_via_upstage_enabled():
                # PDF 를 Upstage markdown 으로 변환 (이미지/스캔 내용까지 읽음).
                # 원본 PDF 는 originals/ 에 보관, 변환본 _pdf.md 만 raw/ 에 색인.
                # 변환 실패 시 원본 PDF 를 그대로 색인 → Lambda parse_pdf 폴백.
                try:
                    md_bytes, md_name = convert_pdf_to_markdown(file_bytes, filename)
                except Exception:
                    log.warning(
                        "Upstage PDF 변환 실패, 원본 PDF 색인으로 폴백: %s", filename,
                        exc_info=True,
                    )
                else:
                    originals = get_originals_prefix(prefix)
                    originals_key = f"{originals}{filename}"
                    _get_s3().put_object(Bucket=bucket, Key=originals_key, Body=file_bytes)
                    uploaded_uris.append(f"s3://{bucket}/{originals_key}")
                    file_bytes, filename = md_bytes, md_name
                    ext = ".md"

            if ext in TABULAR_EXTS:
                uris = split_and_upload_tabular(bucket, prefix, file_bytes, filename)
            else:
                key = f"{prefix}{filename}"
                _get_s3().put_object(Bucket=bucket, Key=key, Body=file_bytes)
                uris = [f"s3://{bucket}/{key}"]
            uploaded_uris.extend(uris)

    except Exception:
        if with_rollback and uploaded_uris:
            log.warning("S3 업로드 실패, 롤백 시작: %d개 삭제", len(uploaded_uris))
            for uri in uploaded_uris:
                key = uri.replace(f"s3://{bucket}/", "")
                try:
                    _get_s3().delete_object(Bucket=bucket, Key=key)
                except Exception as del_err:
                    log.warning("S3 롤백 실패: %s, %s", key, del_err)
        raise

    return uploaded_uris


def delete_files_from_kb(bucket: str, prefix: str, filenames: list[str]) -> None:
    """선택된 파일들을 S3 에서 삭제.

    pptx 의 경우 원본(originals/) 과 인덱싱본(raw/_pptx.json) 둘 다 삭제.
    삭제 후 ingestion job 을 실행해야 Bedrock 이 변경을 감지하여
    S3 Vectors 의 해당 파일 벡터를 제거 (호출자가 직접 트리거).

    S3 delete_object 는 멱등이므로 키가 없어도 예외를 던지지 않음.
    """
    originals = get_originals_prefix(prefix)
    keys_to_delete: list[str] = []
    for filename in filenames:
        ext = Path(filename).suffix.lower()
        stem = Path(filename).stem
        if ext == ".pptx":
            keys_to_delete.append(f"{originals}{filename}")
            keys_to_delete.append(f"{prefix}{stem}_pptx.json")
        elif ext == ".xlsx":
            # Upstage 변환본: 원본(originals/) + 변환 md(raw/_xlsx.md)
            # local 분할본: raw/{name} (또는 _partN). 어느 경우인지 알 수 없으니
            # 멱등 삭제로 가능한 키를 모두 시도 (없는 키는 no-op).
            keys_to_delete.append(f"{originals}{filename}")
            keys_to_delete.append(f"{prefix}{stem}_xlsx.md")
            keys_to_delete.append(f"{prefix}{filename}")
        elif ext == ".pdf":
            # Upstage 변환본: 원본(originals/) + 변환 md(raw/_pdf.md)
            # 폴백(미변환): raw/{name}. 멱등 삭제로 가능한 키 모두 시도.
            keys_to_delete.append(f"{originals}{filename}")
            keys_to_delete.append(f"{prefix}{stem}_pdf.md")
            keys_to_delete.append(f"{prefix}{filename}")
        else:
            keys_to_delete.append(f"{prefix}{filename}")

    for key in keys_to_delete:
        _get_s3().delete_object(Bucket=bucket, Key=key)


# ──────────────────────────────────────────────
# Ingestion 상태 폴링
# ──────────────────────────────────────────────
def poll_ingestion_status(
    kb_id: str,
    data_source_id: str,
    job_id: str,
    poll_interval: int | None = None,
    poll_timeout: int | None = None,
) -> str:
    """
    Ingestion Job 완료 여부 폴링.
    반환: 최종 상태 문자열.
    실패 시 상태에 사유를 포함: "FAILED: 사유..."
    """
    cfg = _cfg()
    if poll_interval is None:
        poll_interval = cfg.get("ingest_poll_interval", 5)
    if poll_timeout is None:
        poll_timeout = cfg.get("ingest_poll_timeout", 300)
    start = time.time()
    while time.time() - start < poll_timeout:
        resp = _get_bedrock_agent().get_ingestion_job(
            knowledgeBaseId=kb_id,
            dataSourceId=data_source_id,
            ingestionJobId=job_id,
        )
        job = resp["ingestionJob"]
        status = job["status"]
        stats = job.get("statistics", {})

        if status in ("COMPLETE", "FAILED", "STOPPED"):
            if status != "COMPLETE":
                reasons = job.get("failureReasons", [])
                if reasons:
                    detail = "; ".join(reasons[:3])
                    log.warning("Bedrock Ingestion 실패: %s", detail)
                    return f"{status}: {detail}"
            num_failed = stats.get("numberOfDocumentsFailed", 0)
            if status == "COMPLETE" and num_failed > 0:
                num_indexed = stats.get("numberOfNewDocumentsIndexed", 0)
                num_scanned = stats.get("numberOfDocumentsScanned", 0)
                log.warning(
                    "Bedrock 부분 실패: %d개 문서 실패 (scanned=%d, indexed=%d)",
                    num_failed, num_scanned, num_indexed,
                )
                return f"COMPLETE_WITH_ERRORS: {num_indexed}개 성공, {num_failed}개 실패"
            return status
        time.sleep(poll_interval)
    raise TimeoutError(f"Ingestion 완료 대기 타임아웃: job_id={job_id}")
