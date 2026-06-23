"""
shared_kb_manager.py

공용 Knowledge Base 파일 업로드 및 Ingestion 관리 스크립트.
챗봇 서버와 무관하게 관리자가 직접 CLI로 실행.

2단계 폴더 계층 (대분류/소분류):
    --folder 는 "대분류/소분류"(예: 규정/인사) 또는 "대분류"(예: 규정) 형태.
    Data Source 는 대분류 단위로 1개만 생성되며, 소분류는 그 raw/ 안의 하위 폴더다.
    따라서 대분류 1개가 소분류를 무제한 담을 수 있어 'KB당 DS 5개' 한도를 우회한다.

S3 경로 구조:
    shared/{대분류}/raw/{소분류}/      ← 원본 업로드 파일 (소분류 없으면 raw/ 바로 밑)
    shared/{대분류}/originals/{소분류}/ ← xlsx 원본 보관 (인덱싱 제외)
    shared/{대분류}/processed/         ← Lambda 변환 결과 (intermediate 버킷)

사용 예시:
    # 소분류 디렉토리 전체 업로드 + Ingestion (규정 대분류, 인사 소분류)
    python scripts/shared_kb_manager.py --action upload --folder 규정/인사 --dir ./docs/shared_kb_docs/규정/인사/

    # 파일 1개 업로드 (대분류만 — raw/ 바로 밑)
    python scripts/shared_kb_manager.py --action upload --folder policy --file ./docs/shared_kb_docs/policy/policy_2026.pdf

    # 파싱 방식 강제 지정 (--parser auto|upstage|local). 예: 이번 업로드는 로컬 파서로
    python scripts/shared_kb_manager.py --action upload --folder policy --dir ./docs/... --parser local

    # 특정 대분류 Ingestion만 실행 (그 DS 의 전 소분류 재처리)
    python scripts/shared_kb_manager.py --action ingest --folder 규정

    # Ingestion 상태 확인
    python scripts/shared_kb_manager.py --action status --folder 규정 --job-id abc123

    # 등록된 대분류(Data Source) 목록 확인
    python scripts/shared_kb_manager.py --action list

    # 새 대분류(Data Source) 등록
    python scripts/shared_kb_manager.py --action add-folder --folder 규정

    # 대분류 이름 변경 (S3 서버사이드 이동 + DS 갱신 + 재-ingest, 재업로드 불필요)
    python scripts/shared_kb_manager.py --action rename-folder --folder 규정 --to 사내규정

설정은 .env(인프라) + config/knowBase.yaml(동작 옵션) 두 곳에서 가져온다.
get_kb_config() 가 .env 의 KB_* 변수를 shared_kb 섹션에 주입하므로,
인프라 키(s3_bucket·s3_intermediate_bucket·lambda_arn·kb_role_arn)는 yaml 이 아닌 .env 에 둔다.

.env 에 채워야 하는 항목:
    S3_BUCKET_NAME              # KB 파일 저장 버킷 (채팅 첨부와 공유)
    KB_S3_INTERMEDIATE_BUCKET   # Lambda 변환 결과 중간 버킷
    KB_LAMBDA_ARN               # Custom Transformation Lambda ARN
    KB_ROLE_ARN                 # Bedrock KB IAM Role ARN
    # (KB_S3_VECTOR_BUCKET 는 공용 KB 에선 미사용 — 인덱스를 새로 만들지 않음)

config/knowBase.yaml 의 shared_kb 섹션에 둘 항목 (동작 옵션 + 폴더 레지스트리):
    shared_kb:
        kb_id:           "your-shared-kb-id"   # 사전 생성한 공용 KB ID
        embedding_model: "amazon.titan-embed-text-v2:0"
        poll_interval:   5
        poll_timeout:    300
        folders:                                # add-folder 실행 시 자동 추가
            policy:     "ds-id-policy"
            manual:     "ds-id-manual"
            notice:     "ds-id-notice"
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import time
from pathlib import Path

import boto3
import pandas as pd
import yaml

# 프로젝트 루트를 sys.path에 추가 (scripts/ 에서 직접 실행하기 위함)
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ──────────────────────────────────────────────
# 설정 로드 (.env 의 KB_* 변수 주입은 get_kb_config 가 처리)
# ──────────────────────────────────────────────
CONFIG_PATH = ROOT / "config" / "knowBase.yaml"

from wellbot.env import init_env  # noqa: E402
init_env()  # KB 모듈의 모듈레벨 os.getenv 보장 (다른 wellbot import 전에 호출)

from wellbot.services.knowledgebase.config import get_kb_config  # noqa: E402  (sys.path 설정 이후 import)
from wellbot.services.knowledgebase.kb_utils import (  # noqa: E402
    convert_pdf_to_markdown,
    convert_xlsx_to_markdown,
    pdf_via_upstage_enabled,
    pptx_to_dict,
    xlsx_via_upstage_enabled,
)

_config = get_kb_config()
_kb_cfg = _config["shared_kb"]

SHARED_KB_ID           = _kb_cfg["kb_id"]
KB_ROLE_ARN            = _kb_cfg["kb_role_arn"]
S3_BUCKET              = _kb_cfg["s3_bucket"]
S3_INTERMEDIATE_BUCKET = _kb_cfg["s3_intermediate_bucket"]
LAMBDA_ARN             = _kb_cfg["lambda_arn"]
EMBEDDING_MODEL        = _kb_cfg["embedding_model"]
POLL_INTERVAL          = _kb_cfg.get("poll_interval", 5)
POLL_TIMEOUT           = _kb_cfg.get("poll_timeout", 300)

# ──────────────────────────────────────────────
# 업로드 제한 설정
# ──────────────────────────────────────────────
ROWS_PER_SPLIT   = 50_000
TABULAR_EXTS     = {".xlsx", ".csv"}
CONVERTIBLE_EXTS = {".pptx"}     # Bedrock KB 미지원 → 업로드 전 json 변환
MAX_FILE_SIZES   = {
    ".txt":  30 * 1024 * 1024,
    ".md":   30 * 1024 * 1024,
    ".json": 30 * 1024 * 1024,
    ".csv":  None,
    ".xlsx": None,
}
MAX_FILE_SIZE_DEFAULT = 100 * 1024 * 1024  # 100MB

# ──────────────────────────────────────────────
# AWS 클라이언트
# ──────────────────────────────────────────────
_s3            = boto3.client("s3")
_bedrock_agent = boto3.client("bedrock-agent", region_name="ap-northeast-2")


# ──────────────────────────────────────────────
# S3 경로 헬퍼
# ──────────────────────────────────────────────
# 2단계 폴더 계층: 대분류(top) = Data Source 단위, 소분류(sub) = raw/ 내부 하위 폴더.
#   --folder "규정/인사"  → top="규정", sub="인사"
#   --folder "규정"       → top="규정", sub=""   (기존 단일 계층과 동일 = 하위호환)
# DS 1개(inclusionPrefix=shared/{top}/raw/)가 그 아래 모든 소분류를 포함하므로
# Bedrock 의 'KB당 DS 5개' 한도를 소분류로는 소모하지 않는다.
def _split_folder(folder: str) -> tuple[str, str]:
    """'규정/인사' → ('규정', '인사'), '규정' → ('규정', '')."""
    parts = [p for p in folder.strip("/").split("/") if p]
    if not parts:
        return "", ""
    return parts[0], "/".join(parts[1:])


def _raw_prefix(folder: str) -> str:
    """업로드/인덱싱 대상 prefix. 소분류가 있으면 raw/ 안에 중첩한다."""
    top, sub = _split_folder(folder)
    return f"shared/{top}/raw/{sub}/" if sub else f"shared/{top}/raw/"


def _originals_prefix(folder: str) -> str:
    """xlsx 원본 보관 prefix. raw/ 의 계층을 originals/ 에 그대로 미러링한다
    (kb_retriever._map_to_original_uri 의 /raw/→/originals/ 치환과 일치)."""
    return _raw_prefix(folder).replace("/raw/", "/originals/", 1)


def _processed_prefix(folder: str) -> str:
    """DS 의 중간 저장(intermediateStorage) prefix — 대분류 단위."""
    top, _ = _split_folder(folder)
    return f"shared/{top}/processed/"


def _ds_name(folder: str) -> str:
    """Bedrock 데이터소스 이름 생성 (ASCII 안전, 대분류 단위).

    Bedrock 리소스 이름은 영문/숫자/하이픈/언더스코어만 허용하므로 한글 폴더명을
    그대로 쓸 수 없다. ASCII 슬러그 + 폴더명 해시 8자로 고유하고 유효한 이름을 만든다.
    한글 등 비ASCII 폴더는 슬러그가 비므로 해시만 사용 (콘솔 식별은 description 의
    한글 폴더명으로 한다). 영문 폴더는 슬러그가 남아 콘솔에서도 읽기 쉽다.
        'policy' → 'aiinno-bedrock-kb-ds-shared-policy-1a2b3c4d'
        '규정'   → 'aiinno-bedrock-kb-ds-shared-7e8f9a0b'
    """
    top, _ = _split_folder(folder)
    ascii_slug = re.sub(r"[^a-zA-Z0-9]+", "-", top).strip("-").lower()
    digest = hashlib.md5(top.encode("utf-8")).hexdigest()[:8]
    suffix = f"{ascii_slug}-{digest}" if ascii_slug else digest
    return f"aiinno-bedrock-kb-ds-shared-{suffix}"[:100]


# ──────────────────────────────────────────────
# config/knowBase.yaml 폴더 → Data Source 매핑 관리
# ──────────────────────────────────────────────
def _get_folders() -> dict:
    # yaml 에서 'folders:' 를 값 없이 비워두면 None 으로 파싱되므로 (키가 있어 .get
    # 기본값이 적용되지 않음) None 도 빈 dict 로 정규화한다.
    return _kb_cfg.get("folders") or {}


def _get_data_source_id(folder: str) -> str:
    # DS 는 대분류(top) 단위. 소분류가 와도 top 으로 조회한다.
    top, _ = _split_folder(folder)
    folders = _get_folders()
    if top not in folders:
        raise ValueError(
            f"대분류 '{top}'가 등록되어 있지 않습니다. "
            f"등록된 대분류: {list(folders.keys())}\n"
            f"새 대분류 등록: python scripts/shared_kb_manager.py --action add-folder --folder {top}"
        )
    return folders[top]


def _save_data_source_id(folder: str, data_source_id: str) -> None:
    """
    config/knowBase.yaml의 shared_kb.folders에 폴더를 추가.
    yaml.dump 대신 텍스트 삽입으로 기존 주석/형식을 보존.
    """
    content = CONFIG_PATH.read_text(encoding="utf-8")

    new_entry = f"    {folder}: \"{data_source_id}\""

    # folders: {} (빈 dict) → folders:\n    folder: "ds-id" 로 변환
    if "folders: {}" in content:
        content = content.replace(
            "folders: {}",
            f"folders:\n{new_entry}",
        )
    elif "folders:" in content:
        lines = content.split("\n")
        insert_idx = None
        in_folders = False
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("folders:"):
                in_folders = True
                insert_idx = i + 1
                continue
            if in_folders:
                if line.startswith("    ") and (stripped.startswith("#") or ":" in stripped):
                    insert_idx = i + 1
                else:
                    break

        if insert_idx is not None:
            lines.insert(insert_idx, new_entry)
            content = "\n".join(lines)
    else:
        print("[Config] folders 키를 찾을 수 없어 yaml.dump로 fallback")
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            full_config = yaml.safe_load(f)
        shared = full_config["shared_kb"]
        if not shared.get("folders"):
            shared["folders"] = {}
        shared["folders"][folder] = data_source_id
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(full_config, f, allow_unicode=True, default_flow_style=False)
        _kb_cfg["folders"] = shared["folders"]
        return

    CONFIG_PATH.write_text(content, encoding="utf-8")

    # 메모리 캐시도 업데이트 (folders 가 None/누락이어도 안전하게)
    if not _kb_cfg.get("folders"):
        _kb_cfg["folders"] = {}
    _kb_cfg["folders"][folder] = data_source_id
    print(f"[Config] 폴더 등록 완료: {folder} → {data_source_id}")


def _rename_folder_in_yaml(old: str, new: str, ds_id: str) -> None:
    """folders 레지스트리에서 대분류 키를 old→new 로 변경 (ds_id 동일 유지)."""
    content = CONFIG_PATH.read_text(encoding="utf-8")
    old_entry = f'{old}: "{ds_id}"'
    new_entry = f'{new}: "{ds_id}"'
    if old_entry in content:
        content = content.replace(old_entry, new_entry, 1)
        CONFIG_PATH.write_text(content, encoding="utf-8")
    else:
        # 따옴표/형식이 달라 텍스트 치환 실패 시 yaml 로드/덤프 폴백 (주석 손실 가능)
        print("[Config] 텍스트 치환 실패 → yaml.dump 폴백")
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            full = yaml.safe_load(f)
        fmap = (full.get("shared_kb", {}) or {}).get("folders") or {}
        if old in fmap:
            fmap[new] = fmap.pop(old)
        full["shared_kb"]["folders"] = fmap
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(full, f, allow_unicode=True, default_flow_style=False)

    # 메모리 캐시 갱신
    fmap = _kb_cfg.get("folders") or {}
    fmap.pop(old, None)
    fmap[new] = ds_id
    _kb_cfg["folders"] = fmap
    print(f"[Config] 레지스트리 키 변경: {old} → {new}")


# ──────────────────────────────────────────────
# Data Source 생성 (새 폴더 등록 시)
# ──────────────────────────────────────────────
def _ds_s3_config(top: str) -> dict:
    """대분류 DS 의 S3 설정. inclusionPrefix=shared/{top}/raw/ 가 전 소분류 포함."""
    return {
        "type": "S3",
        "s3Configuration": {
            "bucketArn":         f"arn:aws:s3:::{S3_BUCKET}",
            "inclusionPrefixes": [f"shared/{top}/raw/"],
        },
    }


def _ds_vector_config(top: str) -> dict:
    """대분류 DS 의 청킹/변환 설정 (NONE 청킹 + 커스텀 Lambda POST_CHUNKING)."""
    return {
        "chunkingConfiguration": {
            "chunkingStrategy": "NONE",
        },
        "customTransformationConfiguration": {
            "intermediateStorage": {
                "s3Location": {
                    "uri": f"s3://{S3_INTERMEDIATE_BUCKET}/{_processed_prefix(top)}",
                },
            },
            "transformations": [{
                "stepToApply": "POST_CHUNKING",
                "transformationFunction": {
                    "transformationLambdaConfiguration": {
                        "lambdaArn": LAMBDA_ARN,
                    },
                },
            }],
        },
    }


def add_folder(folder: str) -> str:
    # DS 는 대분류(top) 단위로만 생성한다. 소분류가 와도 top 의 DS 하나를 공유하며,
    # inclusionPrefix=shared/{top}/raw/ 가 그 아래 모든 소분류를 자동 포함한다.
    top, _ = _split_folder(folder)
    folders = _get_folders()
    if top in folders:
        print(f"[Folder] 이미 등록된 대분류: {top} → {folders[top]}")
        return folders[top]

    print(f"[Folder] 새 대분류 Data Source 생성: top={top}")

    resp = _bedrock_agent.create_data_source(
        knowledgeBaseId=SHARED_KB_ID,
        # 이름은 ASCII 슬러그+해시 (한글 폴더 지원). 한글 대분류명은 description 으로 식별.
        name=_ds_name(top),
        description=f"공용 KB - {top} (대분류)",
        dataSourceConfiguration=_ds_s3_config(top),
        vectorIngestionConfiguration=_ds_vector_config(top),
    )
    data_source_id = resp["dataSource"]["dataSourceId"]
    _save_data_source_id(top, data_source_id)
    return data_source_id


# ──────────────────────────────────────────────
# 대분류 이름 변경 (S3 서버사이드 이동 + DS 갱신 + 재-ingest)
# ──────────────────────────────────────────────
def _copy_prefix(old_top: str, new_top: str) -> int:
    """S3 shared/{old_top}/ 아래 전 객체를 shared/{new_top}/ 로 서버사이드 복사."""
    src_prefix = f"shared/{old_top}/"
    dst_prefix = f"shared/{new_top}/"
    paginator = _s3.get_paginator("list_objects_v2")
    count = 0
    for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=src_prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            new_key = dst_prefix + key[len(src_prefix):]
            _s3.copy_object(
                Bucket=S3_BUCKET,
                CopySource={"Bucket": S3_BUCKET, "Key": key},
                Key=new_key,
            )
            count += 1
    return count


def _delete_prefix(top: str) -> int:
    """S3 shared/{top}/ 아래 전 객체 삭제."""
    prefix = f"shared/{top}/"
    paginator = _s3.get_paginator("list_objects_v2")
    count = 0
    for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=prefix):
        batch = [{"Key": o["Key"]} for o in page.get("Contents", [])]
        if batch:
            _s3.delete_objects(Bucket=S3_BUCKET, Delete={"Objects": batch})
            count += len(batch)
    return count


def rename_folder(old: str, new: str) -> None:
    """대분류 이름 변경. S3 서버사이드 이동 + DS inclusionPrefix 갱신 + 재-ingest.

    DS(ds_id)는 유지하고 inclusionPrefix/이름/intermediateStorage 만 새 경로로
    갱신한다. 옛 경로 파일을 삭제한 뒤 재-ingest 하면 증분 동기화로 옛 경로 문서의
    벡터가 제거되고 새 경로가 색인된다 (DS 삭제/deletion policy 에 의존하지 않음).
    파일은 S3 서버사이드 복사라 로컬에서 다시 올릴 필요가 없다.
    """
    old_top, _ = _split_folder(old)
    new_top, _ = _split_folder(new)
    if not old_top or not new_top:
        raise ValueError("old/new 대분류 이름이 비어 있습니다.")
    if old_top == new_top:
        raise ValueError("old 와 new 가 동일합니다.")
    folders = _get_folders()
    if old_top not in folders:
        raise ValueError(
            f"대분류 '{old_top}' 가 등록되어 있지 않습니다. 등록됨: {list(folders.keys())}"
        )
    if new_top in folders:
        raise ValueError(f"대분류 '{new_top}' 가 이미 존재합니다. 병합은 지원하지 않습니다.")

    ds_id = folders[old_top]
    print(f"[Rename] 대분류 이름 변경: {old_top} → {new_top} (ds_id={ds_id})")

    # 1. S3 서버사이드 복사 (재업로드 없음) — raw/·originals/ 포함 전부
    copied = _copy_prefix(old_top, new_top)
    print(f"[S3] 복사 완료: shared/{old_top}/ → shared/{new_top}/ ({copied}개)")

    # 2. 옛 경로 객체 삭제 (재-ingest 전 정리 → 증분 동기화가 옛 문서 벡터 제거)
    deleted = _delete_prefix(old_top)
    print(f"[S3] 옛 경로 삭제: shared/{old_top}/ ({deleted}개)")

    # 3. DS 를 새 경로로 갱신 (ds_id 유지)
    _bedrock_agent.update_data_source(
        knowledgeBaseId=SHARED_KB_ID,
        dataSourceId=ds_id,
        name=_ds_name(new_top),
        description=f"공용 KB - {new_top} (대분류)",
        dataSourceConfiguration=_ds_s3_config(new_top),
        vectorIngestionConfiguration=_ds_vector_config(new_top),
    )
    print(f"[Bedrock] Data Source 갱신: inclusionPrefix → shared/{new_top}/raw/")

    # 4. yaml 레지스트리 키 변경 (ds_id 동일)
    _rename_folder_in_yaml(old_top, new_top, ds_id)

    # 5. 재-ingest → 새 경로 색인 + 옛 경로 문서 벡터 제거(증분 동기화)
    job_id = start_ingestion(new_top)
    print(f"⚙️  재-ingest 시작: job_id={job_id}")
    status = poll_ingestion_status(new_top, job_id)
    print(f"✅ 이름 변경 완료: {old_top} → {new_top}, status={status}")


# ──────────────────────────────────────────────
# 파일 크기 검증
# ──────────────────────────────────────────────
def _validate_file_size(file_path: str) -> None:
    path  = Path(file_path)
    ext   = path.suffix.lower()
    size  = path.stat().st_size
    limit = MAX_FILE_SIZES.get(ext, MAX_FILE_SIZE_DEFAULT)
    if limit is None:
        return
    if size > limit:
        limit_mb  = limit // (1024 * 1024)
        actual_mb = size / (1024 * 1024)
        raise ValueError(
            f"파일 크기 초과: {path.name} ({actual_mb:.1f}MB). "
            f"{ext} 파일은 {limit_mb}MB 이하만 업로드 가능합니다."
        )


# ──────────────────────────────────────────────
# 분할 업로드 (xlsx / csv)
# ──────────────────────────────────────────────
def _cleanup_existing_parts(folder: str, stem: str, ext: str) -> None:
    """재업로드 시 파트 수가 달라져서 오래된 파트가 남는 것을 방지."""
    prefix    = _raw_prefix(folder)
    paginator = _s3.get_paginator("list_objects_v2")
    deleted   = 0
    for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            key      = obj["Key"]
            filename = key.split("/")[-1]
            if filename.startswith(f"{stem}_part") and filename.endswith(ext):
                _s3.delete_object(Bucket=S3_BUCKET, Key=key)
                deleted += 1
                print(f"[S3] 기존 파트 삭제: {key}")
    if deleted:
        print(f"[S3] 기존 파트 {deleted}개 정리 완료: stem={stem}, ext={ext}")


def _split_and_upload_tabular(folder: str, file_path: str) -> list[str]:
    """xlsx/csv를 ROWS_PER_SPLIT 행 단위로 분할해서 S3 raw/ 에 저장."""
    path = Path(file_path)
    ext  = path.suffix.lower()
    stem = path.stem

    _cleanup_existing_parts(folder, stem, ext)

    if ext == ".csv":
        try:
            df = pd.read_csv(file_path)
        except UnicodeDecodeError:
            df = pd.read_csv(file_path, encoding="cp949")
    else:
        df = pd.read_excel(file_path)

    total_rows = len(df)
    print(f"[Upload] 분할 업로드 시작: {path.name}, 총 {total_rows}행, {ROWS_PER_SPLIT}행씩 분할")

    uris = []
    for i, start in enumerate(range(0, total_rows, ROWS_PER_SPLIT)):
        chunk_df       = df.iloc[start:start + ROWS_PER_SPLIT]
        split_filename = f"{stem}_part{i + 1}{ext}"
        buf            = io.BytesIO()

        if ext == ".csv":
            chunk_df.to_csv(buf, index=False)
        else:
            chunk_df.to_excel(buf, index=False)

        buf.seek(0)
        key = f"{_raw_prefix(folder)}{split_filename}"
        _s3.put_object(Bucket=S3_BUCKET, Key=key, Body=buf.read())
        uri = f"s3://{S3_BUCKET}/{key}"
        print(
            f"[S3] 분할 업로드: {uri} "
            f"(rows {start}~{min(start + ROWS_PER_SPLIT, total_rows) - 1})"
        )
        uris.append(uri)

    print(f"[Upload] 분할 업로드 완료: {path.name} → {len(uris)}개 파트")
    return uris


# ──────────────────────────────────────────────
# pptx → json 변환
# ──────────────────────────────────────────────
def convert_pptx_to_json(file_path: str) -> str:
    """pptx 파일을 슬라이드별 JSON 으로 변환 (Bedrock KB 미지원 형식 전처리).

    슬라이드 추출 코어는 kb_utils.pptx_to_dict 재사용. 파일 I/O 만 담당.
    반환: 변환된 json 파일 경로 (예: report.pptx → report_pptx.json)
    """
    path = Path(file_path)
    result = pptx_to_dict(path.read_bytes())

    json_path = path.parent / f"{path.stem}_pptx.json"
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[Convert] pptx → json 변환: {path.name} → {json_path.name} ({len(result)}슬라이드)")
    return str(json_path)


# ──────────────────────────────────────────────
# 파일 수집 유틸
# ──────────────────────────────────────────────
def collect_files_from_dir(dir_path: str) -> list[str]:
    supported = {".pdf", ".docx", ".pptx", ".xlsx", ".csv", ".md", ".txt", ".json", ".html", ".htm"}
    paths = [
        str(p) for p in Path(dir_path).iterdir()
        if p.is_file() and p.suffix.lower() in supported
    ]
    print(f"[Dir] {len(paths)}개 파일 수집: {dir_path}")
    return paths


# ──────────────────────────────────────────────
# 파일 업로드
# ──────────────────────────────────────────────
def _use_upstage(ext: str, parser: str) -> bool:
    """업로드 시 해당 형식을 Upstage 로 변환할지 결정.

    parser: "auto"=기존 게이트(FILE_PARSER_MODE/PDF_VIA_UPSTAGE) 따름,
            "upstage"=강제 Upstage, "local"=강제 로컬(xlsx=pandas, pdf=pdfplumber).
    """
    if parser == "upstage":
        return True
    if parser == "local":
        return False
    # auto: 형식별 기본 게이트
    if ext == ".xlsx":
        return xlsx_via_upstage_enabled()
    if ext == ".pdf":
        return pdf_via_upstage_enabled()
    return False


def upload_files(folder: str, file_paths: list[str], parser: str = "auto") -> list[str]:
    """
    여러 파일을 S3 shared/{대분류}/raw/{소분류}/ 에 업로드.
    - folder 는 "규정/인사"(대분류/소분류) 또는 "규정"(대분류만) 형태.
    - parser: "auto"(기본)=기존 게이트, "upstage"=강제 Upstage, "local"=강제 로컬.
    - pptx: 원본을 S3에 보관 후 json으로 변환하여 인덱싱용 업로드
    - xlsx/csv: ROWS_PER_SPLIT 행 단위로 분할 업로드
    - 그 외: 형식별 크기 제한 검증 후 단일 업로드 (기본 100MB)
    - 업로드 실패 시 부분 업로드된 파일 롤백
    반환: 업로드된 S3 URI 목록
    """
    # DS 는 대분류(top) 단위. 미등록 대분류면 DS 자동 생성 (소분류는 DS 를 안 만듦).
    top, _ = _split_folder(folder)
    if top not in _get_folders():
        print(f"[Upload] 미등록 대분류 → 자동 생성: {top}")
        add_folder(top)

    for file_path in file_paths:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"파일 없음: {file_path}")
        _validate_file_size(file_path)

    # 변환 단계: pptx → json, (FILE_PARSER_MODE=upstage/hybrid 시) xlsx → markdown.
    # 원본은 인덱싱 대상(raw/) 밖 또는 별도 보관, 인덱싱은 변환본으로.
    converted_files: list[str] = []
    resolved_paths: list[str] = []
    for file_path in file_paths:
        ext = Path(file_path).suffix.lower()
        if ext in CONVERTIBLE_EXTS:
            # 원본 pptx를 S3에 보관 (다운로드용)
            original_key = f"{_raw_prefix(folder)}{Path(file_path).name}"
            with open(file_path, "rb") as f:
                _s3.put_object(Bucket=S3_BUCKET, Key=original_key, Body=f)
            print(f"[S3] 원본 보관: s3://{S3_BUCKET}/{original_key}")
            # json 변환본 (Bedrock 인덱싱용)
            json_path = convert_pptx_to_json(file_path)
            resolved_paths.append(json_path)
            converted_files.append(json_path)
        elif ext == ".xlsx" and _use_upstage(".xlsx", parser):
            # xlsx → Upstage markdown 변환 (병합·공백 많은 표를 견고하게 처리).
            # 원본은 originals/ 에 보관해 Bedrock 인덱싱 대상(raw/)에서 제외하고,
            # 변환된 _xlsx.md 만 raw/ 에 올려 parse_md 로 청킹한다.
            # 변환 실패 시 기존 pandas 행 분할로 폴백.
            with open(file_path, "rb") as f:
                data = f.read()
            try:
                md_bytes, md_name = convert_xlsx_to_markdown(data, Path(file_path).name)
            except Exception as exc:
                print(f"[Upstage] xlsx 변환 실패, pandas 분할로 폴백: {Path(file_path).name} ({exc})")
                resolved_paths.append(file_path)
            else:
                originals_prefix = _originals_prefix(folder)
                original_key = f"{originals_prefix}{Path(file_path).name}"
                _s3.put_object(Bucket=S3_BUCKET, Key=original_key, Body=data)
                print(f"[S3] 원본 보관(originals/): s3://{S3_BUCKET}/{original_key}")
                # 변환본을 임시 .md 파일로 써서 raw/ 단일 업로드 경로에 태운다
                md_tmp = Path(file_path).parent / md_name
                md_tmp.write_bytes(md_bytes)
                resolved_paths.append(str(md_tmp))
                converted_files.append(str(md_tmp))
                print(f"[Convert] xlsx → markdown 변환: {Path(file_path).name} → {md_name}")
        elif ext == ".pdf" and _use_upstage(".pdf", parser):
            # PDF → Upstage markdown 변환 (이미지/스캔 내용까지 읽음).
            # 원본 PDF 는 originals/ 보관, 변환본 _pdf.md 만 raw/ 색인.
            # 변환 실패 시 원본 PDF 를 그대로 색인 → Lambda parse_pdf 폴백.
            with open(file_path, "rb") as f:
                data = f.read()
            try:
                md_bytes, md_name = convert_pdf_to_markdown(data, Path(file_path).name)
            except Exception as exc:
                print(f"[Upstage] PDF 변환 실패, 원본 PDF 색인으로 폴백: {Path(file_path).name} ({exc})")
                resolved_paths.append(file_path)
            else:
                originals_prefix = _originals_prefix(folder)
                original_key = f"{originals_prefix}{Path(file_path).name}"
                _s3.put_object(Bucket=S3_BUCKET, Key=original_key, Body=data)
                print(f"[S3] 원본 보관(originals/): s3://{S3_BUCKET}/{original_key}")
                md_tmp = Path(file_path).parent / md_name
                md_tmp.write_bytes(md_bytes)
                resolved_paths.append(str(md_tmp))
                converted_files.append(str(md_tmp))
                print(f"[Convert] PDF → markdown 변환: {Path(file_path).name} → {md_name}")
        else:
            resolved_paths.append(file_path)

    uploaded_uris: list[str] = []
    try:
        for file_path in resolved_paths:
            path = Path(file_path)
            ext  = path.suffix.lower()

            if ext in TABULAR_EXTS:
                uris = _split_and_upload_tabular(folder, file_path)
            else:
                key = f"{_raw_prefix(folder)}{path.name}"
                with open(path, "rb") as f:
                    _s3.put_object(Bucket=S3_BUCKET, Key=key, Body=f)
                uri  = f"s3://{S3_BUCKET}/{key}"
                print(f"[S3] 업로드 완료: {uri}")
                uris = [uri]

            uploaded_uris.extend(uris)

    except Exception:
        if uploaded_uris:
            print(f"[S3] 업로드 실패, 롤백 시작: {len(uploaded_uris)}개 삭제")
            for uri in uploaded_uris:
                key = uri.replace(f"s3://{S3_BUCKET}/", "")
                try:
                    _s3.delete_object(Bucket=S3_BUCKET, Key=key)
                except Exception as del_err:
                    print(f"[S3] 롤백 실패: {key}, {del_err}")
        raise
    finally:
        for tmp in converted_files:
            try:
                Path(tmp).unlink(missing_ok=True)
            except Exception:
                pass

    return uploaded_uris


# ──────────────────────────────────────────────
# Ingestion
# ──────────────────────────────────────────────
def start_ingestion(folder: str) -> str:
    data_source_id = _get_data_source_id(folder)
    resp = _bedrock_agent.start_ingestion_job(
        knowledgeBaseId=SHARED_KB_ID,
        dataSourceId=data_source_id,
    )
    job_id = resp["ingestionJob"]["ingestionJobId"]
    print(f"[Bedrock] Ingestion 시작: folder={folder}, job_id={job_id}")
    return job_id


def poll_ingestion_status(folder: str, job_id: str) -> str:
    data_source_id = _get_data_source_id(folder)
    start = time.time()

    while time.time() - start < POLL_TIMEOUT:
        resp   = _bedrock_agent.get_ingestion_job(
            knowledgeBaseId=SHARED_KB_ID,
            dataSourceId=data_source_id,
            ingestionJobId=job_id,
        )
        status = resp["ingestionJob"]["status"]
        stats  = resp["ingestionJob"].get("statistics", {})
        print(
            f"[Bedrock] Ingestion 상태: folder={folder}, status={status}, "
            f"scanned={stats.get('numberOfDocumentsScanned', '-')}, "
            f"indexed={stats.get('numberOfNewDocumentsIndexed', '-')}, "
            f"failed={stats.get('numberOfDocumentsFailed', '-')}"
        )
        if status in ("COMPLETE", "FAILED", "STOPPED"):
            return status
        time.sleep(POLL_INTERVAL)

    raise TimeoutError(f"Ingestion 완료 대기 타임아웃: job_id={job_id}")


def upload_and_ingest(folder: str, file_paths: list[str], parser: str = "auto") -> None:
    """파일 목록 전체 업로드 → Ingestion 1회 실행 → 완료 대기."""
    uploaded = upload_files(folder, file_paths, parser=parser)
    if not uploaded:
        print("업로드된 파일이 없습니다.")
        return

    total_files = len(file_paths)
    total_uris  = len(uploaded)
    print(f"\n📤 {total_files}개 파일 업로드 완료 (S3 오브젝트 {total_uris}개)")
    job_id = start_ingestion(folder)
    print(f"⚙️  Ingestion 시작: job_id={job_id}")
    status = poll_ingestion_status(folder, job_id)
    print(f"✅ 완료: folder={folder}, status={status}")


# ──────────────────────────────────────────────
# 목록 출력
# ──────────────────────────────────────────────
def list_folders() -> None:
    folders = _get_folders()
    if not folders:
        print("등록된 대분류가 없습니다.")
        return
    print(f"\n{'대분류':<20} {'Data Source ID'}")
    print("-" * 60)
    for folder, ds_id in folders.items():
        print(f"{folder:<20} {ds_id}")


# ──────────────────────────────────────────────
# CLI 진입점
# ──────────────────────────────────────────────
def _parse_args():
    parser = argparse.ArgumentParser(description="공용 KB 관리 스크립트")
    parser.add_argument(
        "--action",
        required=True,
        choices=["upload", "ingest", "status", "list", "add-folder", "rename-folder"],
    )
    parser.add_argument("--folder",  help="대분류 또는 대분류/소분류 (예: 규정, 규정/인사)")
    parser.add_argument("--to",      help="rename-folder 시 새 대분류 이름")
    parser.add_argument("--file",    nargs="+", help="업로드할 파일 경로")
    parser.add_argument("--dir",     help="업로드할 디렉토리 경로")
    parser.add_argument("--job-id",  help="Ingestion Job ID (status 확인용)")
    parser.add_argument(
        "--parser", choices=["auto", "upstage", "local"], default="auto",
        help="xlsx/pdf 파싱 방식 (auto=기본 게이트, upstage=강제 Upstage, local=pandas/pdfplumber)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    if args.action == "list":
        list_folders()

    elif args.action == "add-folder":
        if not args.folder:
            print("--folder 옵션이 필요합니다.")
            sys.exit(1)
        ds_id = add_folder(args.folder)
        print(f"✅ 폴더 등록 완료: {args.folder} → {ds_id}")

    elif args.action == "rename-folder":
        if not args.folder or not args.to:
            print("--folder(옛 대분류) 와 --to(새 대분류) 옵션이 필요합니다.")
            sys.exit(1)
        rename_folder(args.folder, args.to)

    elif args.action == "upload":
        if not args.folder:
            print("--folder 옵션이 필요합니다.")
            sys.exit(1)
        if not args.file and not args.dir:
            print("--file 또는 --dir 옵션이 필요합니다.")
            sys.exit(1)

        file_paths = []
        if args.dir:
            file_paths.extend(collect_files_from_dir(args.dir))
        if args.file:
            file_paths.extend(args.file)

        if not file_paths:
            print("업로드할 파일이 없습니다.")
            sys.exit(1)

        print(f"📂 업로드 대상: {len(file_paths)}개 파일 → folder={args.folder} (parser={args.parser})")
        upload_and_ingest(args.folder, file_paths, parser=args.parser)

    elif args.action == "ingest":
        if not args.folder:
            print("--folder 옵션이 필요합니다.")
            sys.exit(1)
        job_id = start_ingestion(args.folder)
        print(f"Ingestion 시작: job_id={job_id}")
        status = poll_ingestion_status(args.folder, job_id)
        print(f"✅ 완료: status={status}")

    elif args.action == "status":
        if not args.folder or not args.job_id:
            print("--folder, --job-id 옵션이 필요합니다.")
            sys.exit(1)
        status = poll_ingestion_status(args.folder, args.job_id)
        print(f"상태: {status}")
