"""
team_kb_manager.py

팀별 Bedrock Knowledge Base 생성 및 관리.

공통 인프라(클라이언트, 설정, KB 생성 함수)는 kb_utils.py 에서 import
이 모듈은 팀 KB 특화 부분(부서코드 조회, 팀원 KB 발견, 동시 ingest 방지)만 담당

S3 경로: teams/{dept_cd}/raw/
"""

import threading
from datetime import datetime
from typing import Optional

from wellbot.constants import KST
from wellbot.models import AgntMmryUseN, EmpM

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
    is_ingestion_in_progress,
    poll_ingestion_status,
    raw_prefix,
    start_ingestion,
    upload_files_to_kb,
    wait_until_kb_ready,
)

# ──────────────────────────────────────────────
# 고정값
# ──────────────────────────────────────────────
TYPE_TEAM = "TEAM"
SEQ_TEAM  = 2                       # AGNT_SEQ: 1=personal, 2=team


# ──────────────────────────────────────────────
# 부서 단위 직렬화 락
#   같은 팀의 두 팀원이 동시에 업로드하면 둘 다 "팀 KB 없음"으로 판단해
#   중복 Bedrock KB 를 생성할 위험. 부서코드별 락으로 조회→생성→등록을
#   원자화해 방지 (단일 백엔드 프로세스 기준이며, 다중 프로세스 환경에서는
#   create_team_kb 의 find_existing_kb 가 백스톱)
# ──────────────────────────────────────────────
_dept_locks: dict[str, threading.Lock] = {}
_dept_locks_guard = threading.Lock()


def _dept_lock(dept_cd: str) -> threading.Lock:
    """부서코드별 락 인스턴스를 반환(없으면 생성)"""
    with _dept_locks_guard:
        lock = _dept_locks.get(dept_cd)
        if lock is None:
            lock = threading.Lock()
            _dept_locks[dept_cd] = lock
        return lock


# ──────────────────────────────────────────────
# DB 조회 / 저장
# ──────────────────────────────────────────────
def get_user_team_kb(emp_no: str) -> Optional[dict]:
    """현재 사용자의 TEAM 행에서 KB 정보 조회. 미등록이면 None"""
    with get_session() as session:
        row = (
            session.query(AgntMmryUseN)
            .filter(
                AgntMmryUseN.agnt_id == AGNT_ID_KB,
                AgntMmryUseN.emp_no == emp_no,
                AgntMmryUseN.agnt_seq == SEQ_TEAM,
                AgntMmryUseN.agnt_type_dscr_cntt == TYPE_TEAM,
            )
            .first()
        )
        if not row or not row.agnt_mmry_path_addr:
            return None
        path_addr = row.agnt_mmry_path_addr
    kb_id, data_source_id = decode_kb_info(path_addr)
    return {"kb_id": kb_id, "data_source_id": data_source_id}


def _find_team_kb_from_teammates(dept_cd: str) -> Optional[dict]:
    """
    같은 팀(부서)의 다른 팀원이 이미 팀 KB 를 등록했는지 DB 에서 검색
    EMP_NO → PSTN_DEPT_CD 조인으로 같은 부서 사용자의 TEAM 행을 조회
    """
    with get_session() as session:
        row = (
            session.query(AgntMmryUseN)
            .join(EmpM, AgntMmryUseN.emp_no == EmpM.emp_no)
            .filter(
                EmpM.pstn_dept_cd == dept_cd,
                AgntMmryUseN.agnt_id == AGNT_ID_KB,
                AgntMmryUseN.agnt_seq == SEQ_TEAM,
                AgntMmryUseN.agnt_type_dscr_cntt == TYPE_TEAM,
                AgntMmryUseN.agnt_mmry_path_addr.isnot(None),
            )
            .first()
        )
        if not row or not row.agnt_mmry_path_addr:
            return None
        path_addr = row.agnt_mmry_path_addr
    kb_id, data_source_id = decode_kb_info(path_addr)
    return {"kb_id": kb_id, "data_source_id": data_source_id}


def _insert_user_team_kb(emp_no: str, kb_id: str, data_source_id: str) -> None:
    """사용자의 TEAM 행 INSERT"""
    now = datetime.now(KST)
    with get_session() as session:
        session.add(AgntMmryUseN(
            agnt_id=AGNT_ID_KB,
            agnt_seq=SEQ_TEAM,
            emp_no=emp_no,
            agnt_type_dscr_cntt=TYPE_TEAM,
            agnt_mmry_path_addr=encode_kb_info(kb_id, data_source_id),
            rgst_dtm=now,
            rgsr_id=emp_no,
            upd_dtm=now,
            uppr_id=emp_no,
        ))


def get_dept_cd(emp_no: str) -> Optional[str]:
    """사용자의 소속 부서코드(=팀 ID)를 DB 에서 조회"""
    with get_session() as session:
        row = (
            session.query(EmpM.pstn_dept_cd)
            .filter(EmpM.emp_no == emp_no)
            .first()
        )
    return row.pstn_dept_cd if row else None


# ──────────────────────────────────────────────
# KB 생성 / 조회
# ──────────────────────────────────────────────
def create_team_kb(dept_cd: str) -> dict:
    """팀 KB 전체 생성 흐름"""
    existing = find_existing_kb("team", dept_cd)
    if existing:
        return existing

    vector_index_arn = create_vector_index("team", dept_cd)
    kb_id = create_bedrock_kb("team", dept_cd, vector_index_arn)
    wait_until_kb_ready(kb_id)
    data_source_id = create_data_source("team", dept_cd, kb_id)
    return {"kb_id": kb_id, "data_source_id": data_source_id}


def ensure_team_kb_membership(emp_no: str, dept_cd: str) -> Optional[dict]:
    """본인이 속한 팀의 기존 KB 가 있는지 확인하고, 있으면 본인 행을 자동 등록.

    get_or_create_team_kb 와 달리 **새로운 KB 를 생성하지 않고** 조회/등록만 수행.
    팀 KB 존재 여부 체크 (검색 범위 활성화 등) 용도.
    """
    with _dept_lock(dept_cd):
        record = get_user_team_kb(emp_no)
        if record:
            return record
        teammate_record = _find_team_kb_from_teammates(dept_cd)
        if teammate_record:
            _insert_user_team_kb(
                emp_no,
                teammate_record["kb_id"],
                teammate_record["data_source_id"],
            )
            return teammate_record
        return None


def get_or_create_team_kb(emp_no: str, dept_cd: str) -> dict:
    """
    팀 KB 조회/생성 흐름:
    1. 본인 TEAM 행 조회 → 있으면 반환
    2. 같은 팀 다른 팀원의 TEAM 행 조회 → 있으면 본인 행 INSERT 후 반환
    3. Bedrock API 에서 기존 팀 KB 검색 → 있으면 본인 행 INSERT 후 반환
    4. 없으면 팀 KB 신규 생성 → 본인 행 INSERT 후 반환
    """
    with _dept_lock(dept_cd):
        record = get_user_team_kb(emp_no)
        if record:
            return record

        teammate_record = _find_team_kb_from_teammates(dept_cd)
        if teammate_record:
            _insert_user_team_kb(emp_no, teammate_record["kb_id"], teammate_record["data_source_id"])
            return teammate_record

        kb_info = create_team_kb(dept_cd)
        _insert_user_team_kb(emp_no, kb_info["kb_id"], kb_info["data_source_id"])
        return kb_info


# ──────────────────────────────────────────────
# 파일 업로드 / 삭제
# ──────────────────────────────────────────────
def upload_files_to_team_kb(
    dept_cd: str,
    files: list[tuple[bytes, str]],
) -> list[str]:
    """팀 KB 에 파일 업로드. 롤백 없음 (팀 공유 자원이라 부분 실패 시에도 보존)"""
    return upload_files_to_kb(
        bucket=get_s3_bucket(),
        prefix=raw_prefix("team", dept_cd),
        files=files,
        with_rollback=False,
    )


def delete_files_from_team_kb(dept_cd: str, filenames: list[str]) -> None:
    """선택된 팀 KB 파일들을 S3 에서 삭제"""
    delete_files_from_kb(
        bucket=get_s3_bucket(),
        prefix=raw_prefix("team", dept_cd),
        filenames=filenames,
    )


# ──────────────────────────────────────────────
# Public 진입점
# ──────────────────────────────────────────────
def upload_and_ingest(
    emp_no: str,
    dept_cd: str,
    files: list[tuple[bytes, str]],
) -> dict:
    """
    팀 파일 업로드 + KB 생성(최초) + Ingestion
    진행 중인 ingestion 이 있으면 에러 반환
    """
    kb_info = get_or_create_team_kb(emp_no, dept_cd)
    kb_id = kb_info["kb_id"]
    data_source_id = kb_info["data_source_id"]

    if is_ingestion_in_progress(kb_id, data_source_id):
        raise RuntimeError(
            "현재 다른 팀원이 문서를 처리 중입니다. 잠시 후 다시 시도해주세요."
        )

    upload_files_to_team_kb(dept_cd, files)
    job_id = start_ingestion(kb_id, data_source_id)
    ingestion_status = poll_ingestion_status(kb_id, data_source_id, job_id)

    return {
        "kb_id": kb_id,
        "data_source_id": data_source_id,
        "ingestion_status": ingestion_status,
    }
