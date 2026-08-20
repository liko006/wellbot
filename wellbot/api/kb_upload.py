"""
kb_upload.py — KB 파일 업로드 커스텀 API

rx.upload 의 10MB 제한을 우회하기 위한 FastAPI 엔드포인트.
파일을 수신하면 바로 S3 에 업로드하고, 메타데이터를 반환.
Reflex state 에서는 이 메타데이터로 pending_files 를 업데이트하고,
confirm_upload 시 ingestion 만 트리거.

처리 흐름:
    - pptx: json 으로 변환 후 업로드 (Bedrock KB 미지원 형식)
    - xlsx/csv: 행 기준 분할 업로드
    - 그 외: 단일 업로드

엔드포인트:
    POST /api/upload_kb_files
    - multipart/form-data
    - files: 파일 목록 (한 요청당 KB_UPLOAD_MAX_PER_REQUEST 개)
    - upload_target: "personal" | "team"
    사번(emp_no)과 팀 부서코드(dept_cd)는 클라이언트 입력이 아니라
    wellbot_auth 세션 쿠키에서 서버가 도출.

응답:
    {
        "uploaded": [
            {"name": "report.pdf", "s3_uri": "s3://bucket/..."},
            ...
        ],
        "error": null
    }
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Cookie, File, Form, HTTPException, UploadFile, status

from wellbot.api.guards import require_admin, require_user
from wellbot.constants import KB_UPLOAD_MAX_PER_REQUEST
from wellbot.logger import log_context
from wellbot.services.knowledgebase import shared_kb_service
from wellbot.services.knowledgebase.config import get_kb_config
from wellbot.services.knowledgebase.kb_utils import raw_prefix, stage_raw_files
from wellbot.services.knowledgebase.team_kb_manager import get_dept_cd

log = logging.getLogger(__name__)

router = APIRouter()


# 동기 핸들러(`def`) — 세션 검증(DB)·S3 적재가 전부 블로킹이라 이벤트 루프에서 돌리면
# 채팅 스트리밍이 업로드 시간만큼 멈춘다. FastAPI 스레드풀에 위임.
@router.post("/api/upload_kb_files")
def upload_kb_files(
    files: list[UploadFile] = File(...),
    upload_target: str = Form("personal"),
    wellbot_auth: str | None = Cookie(default=None),
):
    """
    원본 파일을 S3 staging/ 에만 빠르게 적재하고 반환.

    변환(pptx→json, PDF/xlsx Upstage 등)·분할·색인은 이 요청 안에서 하지 않고
    백그라운드(ChatState.on_upload_complete)에서 staging/ 원본을 읽어 수행한다.
    다중 PDF 동시 업로드 시 동기 Upstage 변환이 프록시 타임아웃(504)을 넘기던
    문제를 구조적으로 분리하기 위함.
    - personal: s3://{bucket}/users{env}/{emp_no}/staging/{filename}
    - team:     s3://{bucket}/teams{env}/{dept_cd}/staging/{filename}

    emp_no / dept_cd 는 클라이언트 입력을 신뢰하지 않고 wellbot_auth 세션
    쿠키에서 서버가 도출 (타인 KB 에 임의 파일 주입 방지).
    """
    # 1. 인증 — 세션 쿠키에서 emp_no 도출
    emp_no = require_user(wellbot_auth)["emp_no"]
    log_context.bind(upload_target=upload_target)

    # 2. 업로드 경로 결정 — team 은 본인 소속 부서로만 (서버에서 도출)
    if upload_target == "team":
        dept_cd = get_dept_cd(emp_no)
        if not dept_cd:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="소속 팀 정보가 없어 팀 업로드를 할 수 없습니다.",
            )
        prefix = raw_prefix("team", dept_cd)
    else:
        prefix = raw_prefix("personal", emp_no)

    kb_cfg = get_kb_config().get("personal_kb", {})
    bucket = kb_cfg.get("s3_bucket", "")
    if not bucket:
        return {"uploaded": [], "error": "S3 버킷 설정이 없습니다."}

    # 원본 바이트를 읽어 staging/ 에만 적재(stage_raw_files) — 변환·분할·색인은
    # 백그라운드에서. 형식/크기·누적 상한은 stage_raw_files 가 적재 전에 선검증해
    # 상한 초과 시 S3 적재 없이 즉시 거부(고아 방지), 부분 적재 실패 시 롤백.
    # 동기 핸들러이므로 UploadFile 의 async read 대신 내부 파일 객체를 직접 읽는다.
    file_tuples: list[tuple[bytes, str]] = []
    for file in files:
        file_tuples.append((file.file.read(), file.filename))

    try:
        staged = stage_raw_files(bucket, prefix, file_tuples)
    except ValueError as e:
        # 지원하지 않는 형식 / 크기 초과 / 개수 초과 / 누적 상한 초과 등 입력 검증 오류
        return {"uploaded": [], "error": str(e)}
    except Exception as e:
        log.exception("KB staging 적재 실패")
        return {"uploaded": [], "error": str(e)}

    return {"uploaded": [{"name": n} for n in staged], "error": None}


# 동기 핸들러(`def`) — 세션 검증(DB)·S3 적재가 전부 블로킹. 위 사용자 업로드와 같은 이유.
@router.post("/api/admin/upload_shared_kb")
def upload_shared_kb_files(
    files: list[UploadFile] = File(...),
    folder: str = Form(...),
    wellbot_auth: str | None = Cookie(default=None),
):
    """공용(회사) KB 원본을 S3 staging/ 에 적재. **DB ADMIN 전용.**

    사용자 업로드(/api/upload_kb_files)와 같은 2단계 구조다 — 이 요청은 적재만 하고
    변환(Upstage 등)·색인은 백그라운드에서 staging/ 원본을 읽어 수행한다.

    Form:
        folder: "대분류" 또는 "대분류/소분류". **이미 등록된 대분류여야 한다**
                (새 대분류 생성은 별도 작업 — 오타로 Data Source 가 생기지 않도록).
        files:  한 요청당 최대 KB_UPLOAD_MAX_PER_REQUEST 개. 그 이상은 클라이언트가
                끊어서 순차 전송한다.
    Cookie:
        wellbot_auth: 로그인 세션 토큰 (JWT). ADMIN 역할이 아니면 403.

    응답: {"staged": ["a.pdf", ...], "folder": "규정/인사", "error": null}
    """
    require_admin(wellbot_auth)
    log_context.bind(kb_folder=folder)

    if len(files) > KB_UPLOAD_MAX_PER_REQUEST:
        return {
            "staged": [],
            "folder": folder,
            "error": f"한 번에 최대 {KB_UPLOAD_MAX_PER_REQUEST}개까지 전송할 수 있습니다.",
        }

    try:
        # 미등록 대분류면 여기서 거부 — staging 만 하는 요청이라 이 검증이 없으면
        # 오타 폴더에 파일이 쌓였다가 색인 시점에 엉뚱한 DS 가 생성된다.
        shared_kb_service.get_data_source_id(folder)
    except ValueError as e:
        return {"staged": [], "folder": folder, "error": str(e)}

    file_tuples = [(file.file.read(), file.filename or "") for file in files]

    try:
        staged = shared_kb_service.stage_files(folder, file_tuples)
    except ValueError as e:
        # 지원하지 않는 형식 / 크기 초과 / 잘못된 폴더 등 입력 검증 오류
        return {"staged": [], "folder": folder, "error": str(e)}
    except Exception as e:
        log.exception("공용 KB staging 적재 실패")
        return {"staged": [], "folder": folder, "error": str(e)}

    return {"staged": staged, "folder": folder, "error": None}
