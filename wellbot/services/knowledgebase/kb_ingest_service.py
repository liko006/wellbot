"""
kb_ingest_service.py

업로드 후처리(축2) 오케스트레이션: staging/ 원본 → 변환+raw/ 적재 → KB 확보 →
ingestion → 상태. 개인/팀(scope)으로 분기하며, UI(ChatState)는 결과(IngestOutcome)를
화면 상태로 매핑만 한다.

HTTP 업로드 엔드포인트(kb_upload.py)는 원본을 staging/ 에 적재만 하고 즉시 반환하고,
무거운 변환(Upstage 등)·색인은 이 서비스가 백그라운드(websocket 이벤트)에서 수행한다
→ 다중 PDF 동시 업로드 시 프록시 타임아웃(504)과 분리.

이 함수는 blocking(boto3/Upstage 동기 호출) — 호출 측에서 run_in_executor 로 실행한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from wellbot.logger import log_context
from wellbot.services.knowledgebase.kb_utils import (
    get_s3_bucket,
    is_ingestion_in_progress,
    poll_ingestion_status,
    process_staged_files,
    raw_prefix,
    start_ingestion,
)
from wellbot.services.knowledgebase.personal_kb_manager import (
    _insert_user_kb,
    delete_files_from_personal_kb,
    get_or_create_personal_kb,
    get_user_kb,
)
from wellbot.services.knowledgebase.team_kb_manager import (
    delete_files_from_team_kb,
    get_dept_cd,
    get_or_create_team_kb,
)

log = logging.getLogger(__name__)


@dataclass
class IngestOutcome:
    """축2 결과. UI 는 이걸 ingestion_status/메시지/kb_exists 로 매핑."""
    status: str           # Bedrock job status: COMPLETE / COMPLETE_WITH_ERRORS / FAILED ...
    busy: bool = False    # team: 다른 멤버가 처리 중(색인 대기 → 롤백 안 함)


def _rollback_orphans(scope: str, emp_no: str, dept_cd: str | None, names: list[str]) -> None:
    """ingestion 실패로 색인되지 못한 이번 turn 의 raw/originals 파일을 삭제.

    S3 에 올라가 목록엔 보이지만 색인 실패로 검색 안 되는 '고아'를 정리.
    best-effort (삭제 실패해도 본 흐름은 진행).
    """
    if not names:
        return
    try:
        if scope == "team":
            d = dept_cd or get_dept_cd(emp_no)
            if d:
                delete_files_from_team_kb(d, names)
        else:
            delete_files_from_personal_kb(emp_no, names)
        log.warning("KB ingestion 실패 → 업로드 파일 롤백 삭제: %s", names)
    except Exception:
        log.exception("KB ingestion 실패 후 S3 롤백 삭제 실패")


def ingest_staged(scope: str, emp_no: str, names: list[str]) -> IngestOutcome:
    """staging/ 원본 → 변환+raw/ 적재 → KB 확보 → ingestion → 결과.

    scope: 'personal' | 'team'. blocking — run_in_executor 로 호출.

    실패 정책(고아 방지):
      - 변환 실패: process_staged_files 가 raw·originals·staging 자체 롤백 후 raise.
      - ingestion FAILED / 예외: 여기서 raw/originals 롤백.
      - 타임아웃 / team busy: 롤백 안 함(job 이 진행 중·예정이라 소스 삭제 시 유실/깨짐).
    """
    # run_in_executor 스레드에서는 contextvar 가 전파되지 않으므로 여기서 재바인딩
    # (KB 인제스트/롤백 로그의 emp_no 상관관계 확보).
    log_context.bind(emp_no=emp_no)
    bucket = get_s3_bucket()
    dept_cd: str | None = None
    if scope == "team":
        # dept_cd 는 클라이언트 입력이 아니라 emp_no 로 서버 도출(업로드 prefix 규칙과
        # 동일 → 방금 올린 파일이 다른 KB 로 색인되는 desync 방지).
        dept_cd = get_dept_cd(emp_no)
        if not dept_cd:
            raise ValueError("소속 팀 정보가 없습니다.")
        prefix = raw_prefix("team", dept_cd)
    else:
        prefix = raw_prefix("personal", emp_no)

    try:
        # staging → 변환(Upstage 등)+raw/originals 적재. 무거운 변환이 여기서 발생.
        process_staged_files(bucket, prefix, names)

        if scope == "team":
            kb_info = get_or_create_team_kb(emp_no, dept_cd)
            # 같은 DS 동시 ingestion 은 Bedrock 이 거부(ConflictException). 진행 중이면
            # 그 job 이 teams/{dept}/raw/ 전체를 스캔하며 방금 올린 파일도 색인하므로
            # (=고아 아님) 롤백 없이 안내만 하고 종료.
            if is_ingestion_in_progress(kb_info["kb_id"], kb_info["data_source_id"]):
                return IngestOutcome(status="", busy=True)
            is_first = False
        else:
            is_first = get_user_kb(emp_no) is None
            kb_info = get_or_create_personal_kb(emp_no)

        job_id = start_ingestion(kb_info["kb_id"], kb_info["data_source_id"])
        status = poll_ingestion_status(kb_info["kb_id"], kb_info["data_source_id"], job_id)

        # 개인 최초 등록: 부분 실패(COMPLETE_WITH_ERRORS)도 KB 는 생성되어 일부 색인되므로
        # DB 등록. 그래야 personal_kb_exists 와 DB 가 일치하고, 다음 on_load 에서 그 값이
        # False 로 뒤집혀 retrieve 가 개인 KB 를 건너뛰는 desync 를 방지.
        if scope == "personal" and is_first and status.startswith("COMPLETE"):
            _insert_user_kb(emp_no, kb_info["kb_id"], kb_info["data_source_id"])

        if not status.startswith("COMPLETE"):  # FAILED 등
            log.error("KB ingestion 실패: %s", status)
            _rollback_orphans(scope, emp_no, dept_cd, names)
        return IngestOutcome(status=status)

    except TimeoutError:
        # job 이 아직 진행 중일 수 있어 롤백하지 않음(진행 중 소스 삭제 시 인덱스/job 깨짐).
        raise
    except Exception:
        _rollback_orphans(scope, emp_no, dept_cd, names)
        raise
