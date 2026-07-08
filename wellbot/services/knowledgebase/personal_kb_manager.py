"""
personal_kb_manager.py

사용자별 개인 Bedrock Knowledge Base 생성 및 관리.

공통 인프라(클라이언트, 설정, KB 생성 함수)는 kb_utils.py 에서 import.
이 모듈은 개인 KB 특화 부분(DB 조회/저장, public API)만 담당.
"""

from datetime import datetime
from typing import Optional

from wellbot.constants import KST
from wellbot.logger import log_context
from wellbot.models import AgntMmryUseN

from wellbot.services.core.database import get_session
from wellbot.services.knowledgebase.kb_utils import (
    AGNT_ID_KB,
    create_bedrock_kb,
    create_data_source,
    create_vector_index,
    decode_kb_info,
    delete_files_from_kb,
    encode_kb_info,
    find_existing_kb,
    get_s3_bucket,
    poll_ingestion_status,
    raw_prefix,
    start_ingestion,
    upload_files_to_kb,
    wait_until_kb_ready,
)

# ──────────────────────────────────────────────
# 고정값
# ──────────────────────────────────────────────
TYPE_PERSONAL = "PERSONAL"
SEQ_PERSONAL  = 1                  # AGNT_SEQ: 1=personal, 2=team


# ──────────────────────────────────────────────
# DB 조회 / 저장
# ──────────────────────────────────────────────
def get_user_kb(emp_no: str) -> Optional[dict]:
    """AGNT_MMRY_USE_N 테이블에서 개인 KB 정보 조회. 미등록이면 None."""
    with get_session() as session:
        row = (
            session.query(AgntMmryUseN)
            .filter(
                AgntMmryUseN.agnt_id == AGNT_ID_KB,
                AgntMmryUseN.emp_no == emp_no,
                AgntMmryUseN.agnt_seq == SEQ_PERSONAL,
                AgntMmryUseN.agnt_type_dscr_cntt == TYPE_PERSONAL,
            )
            .first()
        )
        if not row or not row.agnt_mmry_path_addr:
            return None
        path_addr = row.agnt_mmry_path_addr
    kb_id, data_source_id = decode_kb_info(path_addr)
    return {"kb_id": kb_id, "data_source_id": data_source_id}


def _insert_user_kb(emp_no: str, kb_id: str, data_source_id: str) -> None:
    """KB 생성 + ingest 완료 후 최초 1회 INSERT."""
    now = datetime.now(KST)
    with get_session() as session:
        session.add(AgntMmryUseN(
            agnt_id=AGNT_ID_KB,
            agnt_seq=SEQ_PERSONAL,
            emp_no=emp_no,
            agnt_type_dscr_cntt=TYPE_PERSONAL,
            agnt_mmry_path_addr=encode_kb_info(kb_id, data_source_id),
            rgst_dtm=now,
            rgsr_id=emp_no,
            upd_dtm=now,
            uppr_id=emp_no,
        ))


# ──────────────────────────────────────────────
# KB 생성 / 조회
# ──────────────────────────────────────────────
def create_personal_kb(emp_no: str) -> dict:
    """개인 KB 전체 생성 흐름. DB insert 는 ingest 완료 후 별도 수행."""
    existing = find_existing_kb("personal", emp_no)
    if existing:
        return existing

    vector_index_arn = create_vector_index("personal", emp_no)
    kb_id = create_bedrock_kb("personal", emp_no, vector_index_arn)
    wait_until_kb_ready(kb_id)
    data_source_id = create_data_source("personal", emp_no, kb_id)
    return {"kb_id": kb_id, "data_source_id": data_source_id}


def get_or_create_personal_kb(emp_no: str) -> dict:
    """KB 가 DB 에 있으면 조회, 없으면 생성 (DB insert 는 아직 안 함)."""
    record = get_user_kb(emp_no)
    if record:
        return record
    return create_personal_kb(emp_no)


# ──────────────────────────────────────────────
# 파일 업로드 / 삭제
# ──────────────────────────────────────────────
def upload_files_to_personal_kb(
    emp_no: str,
    files: list[tuple[bytes, str]],
) -> list[str]:
    """개인 KB 에 파일 업로드. 실패 시 부분 업로드 롤백."""
    return upload_files_to_kb(
        bucket=get_s3_bucket(),
        prefix=raw_prefix("personal", emp_no),
        files=files,
        with_rollback=True,
    )


def delete_files_from_personal_kb(emp_no: str, filenames: list[str]) -> None:
    """선택된 파일들을 개인 KB S3 에서 삭제."""
    delete_files_from_kb(
        bucket=get_s3_bucket(),
        prefix=raw_prefix("personal", emp_no),
        filenames=filenames,
    )


# ──────────────────────────────────────────────
# Public 진입점
# ──────────────────────────────────────────────
def upload_and_ingest(
    emp_no: str,
    files: list[tuple[bytes, str]],
) -> dict:
    """
    파일 업로드 + KB 생성(최초) + Ingestion + DB INSERT(최초).
    반환: { "kb_id", "data_source_id", "ingestion_status" }
    """
    log_context.bind(emp_no=emp_no)
    is_first = get_user_kb(emp_no) is None
    kb_info  = get_or_create_personal_kb(emp_no)
    kb_id          = kb_info["kb_id"]
    data_source_id = kb_info["data_source_id"]

    upload_files_to_personal_kb(emp_no, files)
    job_id           = start_ingestion(kb_id, data_source_id)
    ingestion_status = poll_ingestion_status(kb_id, data_source_id, job_id)

    # 부분 실패(COMPLETE_WITH_ERRORS)도 KB 는 생성되어 일부 문서가 색인되므로
    # 최초 1회 DB 에 등록 (등록 누락 시 get_user_kb 가 계속 None 을 반환).
    if is_first and ingestion_status.startswith("COMPLETE"):
        _insert_user_kb(emp_no, kb_id, data_source_id)

    return {
        "kb_id":            kb_id,
        "data_source_id":   data_source_id,
        "ingestion_status": ingestion_status,
    }
