"""공용(shared) Knowledge Base 운용 서비스.

폴더(대분류=Data Source) 등록·파일 업로드·Ingestion·이름 변경을 담당한다.
개인/팀의 ``personal_kb_manager`` / ``team_kb_manager`` 와 같은 층이며, 변환·분할
같은 공통 인프라는 ``kb_utils`` 를 재사용한다.

**호출자는 둘이다** — CLI(``scripts/shared_kb_manager.py``)와 admin UI. 그래서
admin 계층이 아니라 도메인 계층에 둔다(최상위 스크립트가 UI 계층을 import 하면
의존 방향이 뒤집힌다). 진행 상황은 print 가 아니라 모듈 logger 로 남기고, CLI 가
그 logger 에 stdout 핸들러를 붙여 사람이 읽는 출력을 만든다.

2단계 폴더 계층 (대분류/소분류):
    ``folder`` 인자는 "대분류/소분류"(예: 규정/인사) 또는 "대분류"(예: 규정).
    Data Source 는 **대분류 단위로 1개**만 만들고 소분류는 그 raw/ 안의 하위
    폴더다. inclusionPrefix=shared{env}/{대분류}/raw/ 가 전 소분류를 포함하므로
    'KB당 DS 5개' 한도를 소분류로 소모하지 않는다.

S3 경로 구조:
    shared{env}/{대분류}/raw/{소분류}/       ← 색인 대상 (원본 또는 변환본)
    shared{env}/{대분류}/originals/{소분류}/ ← 변환 원본 보관 (색인 제외)
    shared{env}/{대분류}/processed/          ← Lambda 변환 결과 (intermediate 버킷)

폴더 → Data Source id 레지스트리는 ``config/knowBase.yaml`` 의
``shared_kb.folders`` 에 둔다(주석 보존을 위해 텍스트 삽입으로 기록).
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from pathlib import Path

import yaml

from wellbot.paths import KNOWBASE_YAML
from wellbot.services.files import storage_service
from wellbot.services.knowledgebase.config import get_kb_config
from wellbot.services.knowledgebase.kb_utils import (
    CONVERTIBLE_EXTS,
    SHARED_PARSE_POLICY,
    SUPPORTED_EXTENSIONS,
    TABULAR_EXTS,
    ParsePolicy,
    convert_pdf_to_markdown,
    convert_pptx_to_json,
    convert_xlsx_to_markdown,
    get_bedrock_agent,
    shared_base,
    split_and_upload_tabular,
    validate_file_size,
)

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 설정 / 클라이언트 (lazy — import 사이드이펙트 방지)
# ──────────────────────────────────────────────
def _cfg() -> dict:
    """shared_kb 설정 섹션. get_kb_config() 가 캐싱하므로 매번 같은 dict 를 돌려준다
    (폴더 레지스트리 갱신이 앱 프로세스에 즉시 반영되는 근거)."""
    return get_kb_config()["shared_kb"]


def _kb_id() -> str:
    return _cfg()["kb_id"]


def _bucket() -> str:
    return _cfg()["s3_bucket"]


def _s3():
    """S3 클라이언트 — storage_service 의 region 설정 클라이언트 재사용."""
    return storage_service.get_client()


# ──────────────────────────────────────────────
# S3 경로 헬퍼
# ──────────────────────────────────────────────
def split_folder(folder: str) -> tuple[str, str]:
    """'규정/인사' → ('규정', '인사'), '규정' → ('규정', '')."""
    parts = [p for p in folder.strip("/").split("/") if p]
    if not parts:
        return "", ""
    return parts[0], "/".join(parts[1:])


def raw_prefix(folder: str) -> str:
    """색인 대상 prefix. 소분류가 있으면 raw/ 안에 중첩한다."""
    top, sub = split_folder(folder)
    base = shared_base()
    return f"{base}/{top}/raw/{sub}/" if sub else f"{base}/{top}/raw/"


def originals_prefix(folder: str) -> str:
    """변환 원본 보관 prefix. raw/ 의 계층(소분류 포함)을 그대로 미러링한다
    (kb_retriever._map_to_original_uri 의 /raw/→/originals/ 치환과 일치).

    주의: kb_utils.get_originals_prefix 와 통합 금지 — 그쪽은 'raw/' 이후를 잘라내
    소분류를 잃는다(1단계 개인/팀 경로용). 여기선 소분류를 보존해야 한다.
    """
    return raw_prefix(folder).replace("/raw/", "/originals/", 1)


def staging_prefix(folder: str) -> str:
    """업로드 원본 임시 적재 prefix. raw/ 의 형제라 색인 대상(raw/) 밖이다.

    HTTP 업로드는 여기까지만 하고 변환·색인은 백그라운드에서 수행한다 — 다중 PDF 의
    Upstage 변환이 요청 안에서 돌면 프록시 타임아웃(504)에 걸린다. originals/ 와 같은
    이유로 소분류 계층을 보존한다.
    """
    return raw_prefix(folder).replace("/raw/", "/staging/", 1)


def processed_prefix(folder: str) -> str:
    """DS 의 중간 저장(intermediateStorage) prefix — 대분류 단위."""
    top, _ = split_folder(folder)
    return f"{shared_base()}/{top}/processed/"


def validate_folder(folder: str) -> tuple[str, str]:
    """folder 문자열을 검증하고 (대분류, 소분류) 로 분해. 위반 시 ValueError.

    S3 키를 만드는 값이라 경로 조작을 막는다 — 브라우저에서 오는 값이므로
    ``..`` 한 번이면 다른 대분류나 색인 밖 경로에 파일을 심을 수 있다.
    """
    top, sub = split_folder(folder)
    if not top:
        raise ValueError("대분류가 비어 있습니다.")
    for segment in [top, *(s for s in sub.split("/") if s)]:
        if segment in (".", "..") or "\\" in segment:
            raise ValueError(f"폴더 이름에 사용할 수 없는 값입니다: {segment!r}")
    return top, sub


def _data_source_name(top: str) -> str:
    """Bedrock 데이터소스 이름 (ASCII 안전, 대분류 단위).

    Bedrock 리소스 이름은 영문/숫자/하이픈/언더스코어만 허용하므로 한글 폴더명을
    그대로 쓸 수 없다. ASCII 슬러그 + 폴더명 해시 8자로 고유하고 유효한 이름을 만든다.
    한글 등 비ASCII 폴더는 슬러그가 비므로 해시만 사용 (콘솔 식별은 description 의
    한글 폴더명으로 한다). 영문 폴더는 슬러그가 남아 콘솔에서도 읽기 쉽다.
        'policy' → 'aiinno-bedrock-kb-ds-shared-policy-1a2b3c4d'
        '규정'   → 'aiinno-bedrock-kb-ds-shared-7e8f9a0b'

    개인/팀의 kb_utils.data_source_name 과 규칙이 다른 이유: 저쪽 owner 는 사번·부서
    코드라 항상 ASCII 다. 공용은 사람이 정한 한글 폴더명이 owner 자리에 온다.
    환경 분리는 이름이 아니라 KB id(.env KB_ID)로 갈리므로 env_suffix 를 붙이지 않는다.
    """
    ascii_slug = re.sub(r"[^a-zA-Z0-9]+", "-", top).strip("-").lower()
    digest = hashlib.md5(top.encode("utf-8")).hexdigest()[:8]
    suffix = f"{ascii_slug}-{digest}" if ascii_slug else digest
    return f"aiinno-bedrock-kb-ds-shared-{suffix}"[:100]


# ──────────────────────────────────────────────
# 폴더 → Data Source 레지스트리 (config/knowBase.yaml)
# ──────────────────────────────────────────────
def list_folders() -> dict[str, str]:
    """등록된 대분류 → Data Source id 매핑."""
    # yaml 에서 'folders:' 를 값 없이 비워두면 None 으로 파싱되므로 (키가 있어 .get
    # 기본값이 적용되지 않음) None 도 빈 dict 로 정규화한다.
    return _cfg().get("folders") or {}


def get_data_source_id(folder: str) -> str:
    """대분류의 Data Source id. 미등록이면 ValueError."""
    top, _ = split_folder(folder)
    folders = list_folders()
    if top not in folders:
        raise ValueError(
            f"대분류 '{top}'가 등록되어 있지 않습니다. "
            f"등록된 대분류: {list(folders.keys())}"
        )
    return folders[top]


def _cache_folder(top: str, data_source_id: str) -> None:
    """메모리 설정 캐시에 폴더를 반영 (folders 가 None/누락이어도 안전하게)."""
    cfg = _cfg()
    if not cfg.get("folders"):
        cfg["folders"] = {}
    cfg["folders"][top] = data_source_id


def _register_folder(top: str, data_source_id: str) -> None:
    """knowBase.yaml 의 shared_kb.folders 에 폴더를 추가.

    yaml.dump 대신 텍스트 삽입으로 기존 주석/형식을 보존한다.
    """
    content = KNOWBASE_YAML.read_text(encoding="utf-8")
    new_entry = f"    {top}: \"{data_source_id}\""

    # folders: {} (빈 dict) → folders:\n    top: "ds-id" 로 변환
    if "folders: {}" in content:
        content = content.replace("folders: {}", f"folders:\n{new_entry}")
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
        log.warning("knowBase.yaml 에 folders 키가 없어 yaml.dump 로 폴백 (주석 손실 가능)")
        full_config = yaml.safe_load(KNOWBASE_YAML.read_text(encoding="utf-8"))
        shared = full_config["shared_kb"]
        if not shared.get("folders"):
            shared["folders"] = {}
        shared["folders"][top] = data_source_id
        with open(KNOWBASE_YAML, "w", encoding="utf-8") as f:
            yaml.dump(full_config, f, allow_unicode=True, default_flow_style=False)
        _cache_folder(top, data_source_id)
        return

    KNOWBASE_YAML.write_text(content, encoding="utf-8")
    _cache_folder(top, data_source_id)
    log.info("[Config] 폴더 등록 완료: %s → %s", top, data_source_id)


def _rename_folder_in_yaml(old_top: str, new_top: str, data_source_id: str) -> None:
    """folders 레지스트리에서 대분류 키를 old→new 로 변경 (ds_id 동일 유지)."""
    content = KNOWBASE_YAML.read_text(encoding="utf-8")
    old_entry = f'{old_top}: "{data_source_id}"'
    new_entry = f'{new_top}: "{data_source_id}"'
    if old_entry in content:
        content = content.replace(old_entry, new_entry, 1)
        KNOWBASE_YAML.write_text(content, encoding="utf-8")
    else:
        # 따옴표/형식이 달라 텍스트 치환 실패 시 yaml 로드/덤프 폴백 (주석 손실 가능)
        log.warning("knowBase.yaml 텍스트 치환 실패 → yaml.dump 폴백")
        full = yaml.safe_load(KNOWBASE_YAML.read_text(encoding="utf-8"))
        fmap = (full.get("shared_kb", {}) or {}).get("folders") or {}
        if old_top in fmap:
            fmap[new_top] = fmap.pop(old_top)
        full["shared_kb"]["folders"] = fmap
        with open(KNOWBASE_YAML, "w", encoding="utf-8") as f:
            yaml.dump(full, f, allow_unicode=True, default_flow_style=False)

    cfg = _cfg()
    fmap = cfg.get("folders") or {}
    fmap.pop(old_top, None)
    fmap[new_top] = data_source_id
    cfg["folders"] = fmap
    log.info("[Config] 레지스트리 키 변경: %s → %s", old_top, new_top)


# ──────────────────────────────────────────────
# Data Source 생성 / 갱신
# ──────────────────────────────────────────────
def _ds_s3_config(top: str) -> dict:
    """대분류 DS 의 S3 설정. inclusionPrefix=shared{env}/{top}/raw/ 가 전 소분류 포함."""
    return {
        "type": "S3",
        "s3Configuration": {
            "bucketArn": f"arn:aws:s3:::{_bucket()}",
            "inclusionPrefixes": [f"{shared_base()}/{top}/raw/"],
        },
    }


def _ds_vector_config(top: str) -> dict:
    """대분류 DS 의 청킹/변환 설정 (NONE 청킹 + 커스텀 Lambda POST_CHUNKING)."""
    cfg = _cfg()
    return {
        "chunkingConfiguration": {
            "chunkingStrategy": "NONE",
        },
        "customTransformationConfiguration": {
            "intermediateStorage": {
                "s3Location": {
                    "uri": f"s3://{cfg['s3_intermediate_bucket']}/{processed_prefix(top)}",
                },
            },
            "transformations": [{
                "stepToApply": "POST_CHUNKING",
                "transformationFunction": {
                    "transformationLambdaConfiguration": {"lambdaArn": cfg["lambda_arn"]},
                },
            }],
        },
    }


def create_folder(folder: str) -> str:
    """대분류 Data Source 생성 + 레지스트리 등록. 반환: data_source_id.

    이미 등록된 대분류면 기존 id 를 그대로 반환한다(멱등). 소분류가 섞여 와도
    대분류의 DS 하나를 공유하므로 top 만 본다.
    """
    top, _ = split_folder(folder)
    folders = list_folders()
    if top in folders:
        log.info("[Folder] 이미 등록된 대분류: %s → %s", top, folders[top])
        return folders[top]

    log.info("[Folder] 새 대분류 Data Source 생성: top=%s", top)
    resp = get_bedrock_agent().create_data_source(
        knowledgeBaseId=_kb_id(),
        # 이름은 ASCII 슬러그+해시 (한글 폴더 지원). 한글 대분류명은 description 으로 식별.
        name=_data_source_name(top),
        description=f"공용 KB - {top} (대분류)",
        dataSourceConfiguration=_ds_s3_config(top),
        vectorIngestionConfiguration=_ds_vector_config(top),
    )
    data_source_id = resp["dataSource"]["dataSourceId"]
    _register_folder(top, data_source_id)
    return data_source_id


# ──────────────────────────────────────────────
# 파일 업로드
# ──────────────────────────────────────────────
def _put(bucket: str, key: str, body: bytes) -> str:
    """S3 단일 업로드. 반환: s3:// URI."""
    _s3().put_object(Bucket=bucket, Key=key, Body=body)
    return f"s3://{bucket}/{key}"


def delete_uris(uris: list[str]) -> None:
    """S3 URI 목록을 best-effort 삭제 (업로드 롤백용).

    호출자가 여러 번의 upload_files 결과를 모아 한꺼번에 되돌릴 때도 쓴다
    (CLI 의 디렉토리 일괄 업로드는 파일을 하나씩 올리므로 배치 원자성을 여기서 얻는다).
    """
    for uri in uris:
        bucket, _, key = uri.removeprefix("s3://").partition("/")
        try:
            _s3().delete_object(Bucket=bucket, Key=key)
        except Exception as del_err:  # noqa: BLE001 - 롤백은 best-effort
            log.warning("[S3] 롤백 실패: %s, %s", key, del_err)


def _stash_original(bucket: str, folder: str, file_bytes: bytes, filename: str) -> str:
    """변환 대상의 원본을 originals/ 에 보관(색인 제외). 반환: 보관 URI."""
    uri = _put(bucket, f"{originals_prefix(folder)}{filename}", file_bytes)
    log.info("[S3] 원본 보관(originals/): %s", uri)
    return uri


def stage_files(folder: str, files: list[tuple[bytes, str]]) -> list[str]:
    """원본을 staging/ 에만 적재(변환·색인 없음) — 업로드 HTTP 요청용.

    무거운 변환을 요청 밖으로 미뤄 프록시 타임아웃(504)을 피한다. 색인은 이후
    백그라운드에서 staging/ 의 원본을 읽어 수행한다.

    적재 **전에** 형식·크기를 전부 검증해 하나라도 어긋나면 S3 에 아무것도 올리지
    않는다(고아 방지). 부분 적재 중 실패하면 이미 올린 분을 되돌린다.
    파일명은 basename 만 취해 경로 조작을 막는다.

    반환: 적재된 파일명 목록.
    """
    validate_folder(folder)
    checked: list[tuple[bytes, str]] = []
    for file_bytes, filename in files:
        name = Path(filename).name
        if not name:
            raise ValueError("파일명이 비어 있습니다.")
        if Path(name).suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"지원하지 않는 파일 형식: {name}")
        validate_file_size(file_bytes, name)
        checked.append((file_bytes, name))

    bucket = _bucket()
    prefix = staging_prefix(folder)
    uploaded_uris: list[str] = []
    try:
        for file_bytes, name in checked:
            uploaded_uris.append(_put(bucket, f"{prefix}{name}", file_bytes))
    except Exception:
        delete_uris(uploaded_uris)
        raise
    log.info("[S3] staging 적재: %s (%d개)", prefix, len(uploaded_uris))
    return [name for _, name in checked]


def upload_files(
    folder: str,
    files: list[tuple[bytes, str]],
    policy: ParsePolicy = SHARED_PARSE_POLICY,
) -> list[str]:
    """파일 목록을 공용 KB 의 raw/ 에 업로드(색인은 하지 않음).

    - pptx: 원본 보관 + json 변환본을 색인
    - pdf/xlsx: policy 가 Upstage 후보로 보면 원본은 originals/, markdown 변환본만 색인
      (변환 실패 시 pdf=원본 색인, xlsx=pandas 행 분할로 폴백)
    - xlsx/csv(로컬 처리): ROWS_PER_SPLIT 행 단위로 분할 업로드
    - 그 외: 단일 업로드 (형식별 크기 제한 검증)

    미등록 대분류면 Data Source 를 자동 생성한다. 도중 실패 시 이 호출에서 올린
    객체를 모두 삭제(롤백)하고 예외를 전파한다.

    개인/팀 경로(kb_utils.upload_files_to_kb)와 달리 **배치 개수 상한이 없다** —
    관리자가 디렉토리를 통째로 올리는 운용을 지원해야 한다.

    반환: 업로드된 S3 URI 목록.
    """
    top, _ = validate_folder(folder)
    if top not in list_folders():
        log.info("[Upload] 미등록 대분류 → 자동 생성: %s", top)
        create_folder(top)

    for file_bytes, filename in files:
        validate_file_size(file_bytes, filename)

    bucket = _bucket()
    prefix = raw_prefix(folder)
    uploaded_uris: list[str] = []
    try:
        for file_bytes, filename in files:
            ext = Path(filename).suffix.lower()

            if ext in CONVERTIBLE_EXTS:
                # pptx: 원본은 originals/ 에 보관(색인 제외), json 변환본만 raw/ 에 색인.
                # 개인/팀(kb_utils.upload_files_to_kb)과 같은 배치이며, 인용 출처의
                # 다운로드 링크(kb_retriever._map_to_original_uri 의 /raw/→/originals/
                # 치환)가 실제 객체를 가리키려면 originals/ 여야 한다.
                uploaded_uris.append(_stash_original(bucket, folder, file_bytes, filename))
                file_bytes, filename = convert_pptx_to_json(file_bytes, filename)
                ext = ".json"
                log.info("[Convert] pptx → json 변환: %s", filename)

            elif ext == ".pdf" and policy.should_use_upstage(".pdf"):
                # PDF → Upstage markdown (이미지/스캔 내용까지 읽음). 원본은
                # originals/ 보관, 변환본 _pdf.md 만 색인. 실패 시 원본 PDF 를
                # 그대로 색인 → Lambda parse_pdf 폴백.
                try:
                    md_bytes, md_name = convert_pdf_to_markdown(file_bytes, filename)
                except Exception:
                    log.warning(
                        "Upstage PDF 변환 실패, 원본 PDF 색인으로 폴백: %s",
                        filename, exc_info=True,
                    )
                else:
                    uploaded_uris.append(_stash_original(bucket, folder, file_bytes, filename))
                    file_bytes, filename = md_bytes, md_name
                    ext = ".md"
                    log.info("[Convert] PDF → markdown 변환: %s", filename)

            elif ext == ".xlsx" and policy.should_use_upstage(".xlsx"):
                # xlsx → Upstage markdown (병합·공백 많은 표를 견고하게 처리).
                # 실패 시 아래 TABULAR_EXTS 분기(pandas 행 분할)로 폴백.
                try:
                    md_bytes, md_name = convert_xlsx_to_markdown(file_bytes, filename)
                except Exception:
                    log.warning(
                        "Upstage xlsx 변환 실패, pandas 분할로 폴백: %s",
                        filename, exc_info=True,
                    )
                else:
                    uploaded_uris.append(_stash_original(bucket, folder, file_bytes, filename))
                    file_bytes, filename = md_bytes, md_name
                    ext = ".md"
                    log.info("[Convert] xlsx → markdown 변환: %s", filename)

            if ext in TABULAR_EXTS:
                uris = split_and_upload_tabular(bucket, prefix, file_bytes, filename)
                log.info("[Upload] 분할 업로드 완료: %s → %d개 파트", filename, len(uris))
            else:
                uris = [_put(bucket, f"{prefix}{filename}", file_bytes)]
                log.info("[S3] 업로드 완료: %s", uris[0])
            uploaded_uris.extend(uris)

    except Exception:
        if uploaded_uris:
            log.warning("[S3] 업로드 실패, 롤백 시작: %d개 삭제", len(uploaded_uris))
            delete_uris(uploaded_uris)
        raise

    return uploaded_uris


# ──────────────────────────────────────────────
# Ingestion
# ──────────────────────────────────────────────
def start_ingestion(folder: str) -> str:
    """대분류 Data Source 의 Ingestion Job 실행. 반환: ingestion_job_id."""
    data_source_id = get_data_source_id(folder)
    resp = get_bedrock_agent().start_ingestion_job(
        knowledgeBaseId=_kb_id(),
        dataSourceId=data_source_id,
    )
    job_id = resp["ingestionJob"]["ingestionJobId"]
    log.info("[Bedrock] Ingestion 시작: folder=%s, job_id=%s", folder, job_id)
    return job_id


def poll_ingestion_status(folder: str, job_id: str) -> str:
    """Ingestion 완료까지 폴링. 반환: 최종 status (COMPLETE/FAILED/STOPPED).

    poll_timeout 을 넘기면 TimeoutError. 공용 KB 는 대용량을 가정해 기본 대기가 길다.
    """
    data_source_id = get_data_source_id(folder)
    cfg = _cfg()
    poll_interval = cfg.get("poll_interval", 5)
    poll_timeout = cfg.get("poll_timeout", 300)
    start = time.time()

    while time.time() - start < poll_timeout:
        resp = get_bedrock_agent().get_ingestion_job(
            knowledgeBaseId=_kb_id(),
            dataSourceId=data_source_id,
            ingestionJobId=job_id,
        )
        job = resp["ingestionJob"]
        status = job["status"]
        stats = job.get("statistics", {})
        log.info(
            "[Bedrock] Ingestion 상태: folder=%s, status=%s, scanned=%s, indexed=%s, failed=%s",
            folder, status,
            stats.get("numberOfDocumentsScanned", "-"),
            stats.get("numberOfNewDocumentsIndexed", "-"),
            stats.get("numberOfDocumentsFailed", "-"),
        )
        if status in ("COMPLETE", "FAILED", "STOPPED"):
            return status
        time.sleep(poll_interval)

    raise TimeoutError(f"Ingestion 완료 대기 타임아웃: job_id={job_id}")


# ──────────────────────────────────────────────
# 대분류 이름 변경 (S3 서버사이드 이동 + DS 갱신 + 재-ingest)
# ──────────────────────────────────────────────
def _copy_prefix(old_top: str, new_top: str) -> int:
    """S3 shared{env}/{old_top}/ 아래 전 객체를 shared{env}/{new_top}/ 로 서버사이드 복사."""
    bucket = _bucket()
    base = shared_base()
    src_prefix = f"{base}/{old_top}/"
    dst_prefix = f"{base}/{new_top}/"
    paginator = _s3().get_paginator("list_objects_v2")
    count = 0
    for page in paginator.paginate(Bucket=bucket, Prefix=src_prefix):
        for obj in page.get("Contents", []) or []:
            key = obj["Key"]
            _s3().copy_object(
                Bucket=bucket,
                CopySource={"Bucket": bucket, "Key": key},
                Key=dst_prefix + key[len(src_prefix):],
            )
            count += 1
    return count


def _delete_prefix(top: str) -> int:
    """S3 shared{env}/{top}/ 아래 전 객체 삭제."""
    bucket = _bucket()
    prefix = f"{shared_base()}/{top}/"
    paginator = _s3().get_paginator("list_objects_v2")
    count = 0
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        batch = [{"Key": o["Key"]} for o in page.get("Contents", []) or []]
        if batch:
            _s3().delete_objects(Bucket=bucket, Delete={"Objects": batch})
            count += len(batch)
    return count


def rename_folder(old: str, new: str) -> str:
    """대분류 이름 변경. S3 서버사이드 이동 + DS inclusionPrefix 갱신 + 재-ingest.

    DS(ds_id)는 유지하고 inclusionPrefix/이름/intermediateStorage 만 새 경로로
    갱신한다. 옛 경로 파일을 삭제한 뒤 재-ingest 하면 증분 동기화로 옛 경로 문서의
    벡터가 제거되고 새 경로가 색인된다 (DS 삭제/deletion policy 에 의존하지 않음).
    파일은 S3 서버사이드 복사라 로컬에서 다시 올릴 필요가 없다.

    반환: 재-ingest 의 최종 status.
    """
    old_top, _ = split_folder(old)
    new_top, _ = split_folder(new)
    if not old_top or not new_top:
        raise ValueError("old/new 대분류 이름이 비어 있습니다.")
    if old_top == new_top:
        raise ValueError("old 와 new 가 동일합니다.")
    folders = list_folders()
    if old_top not in folders:
        raise ValueError(
            f"대분류 '{old_top}' 가 등록되어 있지 않습니다. 등록됨: {list(folders.keys())}"
        )
    if new_top in folders:
        raise ValueError(f"대분류 '{new_top}' 가 이미 존재합니다. 병합은 지원하지 않습니다.")

    ds_id = folders[old_top]
    base = shared_base()
    log.info("[Rename] 대분류 이름 변경: %s → %s (ds_id=%s)", old_top, new_top, ds_id)

    # 1. S3 서버사이드 복사 (재업로드 없음) — raw/·originals/ 포함 전부
    copied = _copy_prefix(old_top, new_top)
    log.info("[S3] 복사 완료: %s/%s/ → %s/%s/ (%d개)", base, old_top, base, new_top, copied)

    # 2. 옛 경로 객체 삭제 (재-ingest 전 정리 → 증분 동기화가 옛 문서 벡터 제거)
    deleted = _delete_prefix(old_top)
    log.info("[S3] 옛 경로 삭제: %s/%s/ (%d개)", base, old_top, deleted)

    # 3. DS 를 새 경로로 갱신 (ds_id 유지)
    get_bedrock_agent().update_data_source(
        knowledgeBaseId=_kb_id(),
        dataSourceId=ds_id,
        name=_data_source_name(new_top),
        description=f"공용 KB - {new_top} (대분류)",
        dataSourceConfiguration=_ds_s3_config(new_top),
        vectorIngestionConfiguration=_ds_vector_config(new_top),
    )
    log.info("[Bedrock] Data Source 갱신: inclusionPrefix → %s/%s/raw/", base, new_top)

    # 4. yaml 레지스트리 키 변경 (ds_id 동일)
    _rename_folder_in_yaml(old_top, new_top, ds_id)

    # 5. 재-ingest → 새 경로 색인 + 옛 경로 문서 벡터 제거(증분 동기화)
    job_id = start_ingestion(new_top)
    log.info("[Rename] 재-ingest 시작: job_id=%s", job_id)
    return poll_ingestion_status(new_top, job_id)
