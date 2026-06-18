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
    - files: 파일 목록 (최대 5개)
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

from wellbot.logger import log_context
from wellbot.services.auth import auth_service
from wellbot.services.knowledgebase.config import get_kb_config
from wellbot.services.knowledgebase.kb_utils import upload_files_to_kb
from wellbot.services.knowledgebase.team_kb_manager import get_dept_cd

log = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/upload_kb_files")
async def upload_kb_files(
    files: list[UploadFile] = File(...),
    upload_target: str = Form("personal"),
    wellbot_auth: str | None = Cookie(default=None),
):
    """
    파일을 S3에 바로 업로드.
    - pptx: json 으로 변환 후 업로드 (Bedrock KB 미지원 형식)
    - xlsx/csv: 분할 업로드
    - personal: s3://{bucket}/users/{emp_no}/raw/{filename}
    - team:     s3://{bucket}/teams/{dept_cd}/raw/{filename}

    emp_no / dept_cd 는 클라이언트 입력을 신뢰하지 않고 wellbot_auth 세션
    쿠키에서 서버가 도출 (타인 KB 에 임의 파일 주입 방지).
    """
    # 1. 인증 — 세션 쿠키에서 emp_no 도출
    if not wellbot_auth:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="로그인이 필요합니다.",
        )
    user = auth_service.validate_session_token(wellbot_auth)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="세션이 만료되었습니다. 다시 로그인해주세요.",
        )
    emp_no = user["emp_no"]
    log_context.bind(emp_no=emp_no, upload_target=upload_target)

    if len(files) > 5:
        return {"uploaded": [], "error": "한 번에 최대 5개 파일만 업로드 가능합니다."}

    # 2. 업로드 경로 결정 — team 은 본인 소속 부서로만 (서버에서 도출)
    if upload_target == "team":
        dept_cd = get_dept_cd(emp_no)
        if not dept_cd:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="소속 팀 정보가 없어 팀 업로드를 할 수 없습니다.",
            )
        prefix = f"teams/{dept_cd}/raw/"
    else:
        prefix = f"users/{emp_no}/raw/"

    kb_cfg = get_kb_config().get("personal_kb", {})
    bucket = kb_cfg.get("s3_bucket", "")
    if not bucket:
        return {"uploaded": [], "error": "S3 버킷 설정이 없습니다."}

    # 파일 바이트를 읽어 kb_utils.upload_files_to_kb 로 위임 — 변환(pptx→json)·
    # 분할(xlsx·csv)·형식/크기 검증·롤백 로직의 단일 출처. 검증은 업로드 전
    # 전체에 선수행되고, 실패 시 originals/ 원본까지 포함해 롤백.
    file_tuples: list[tuple[bytes, str]] = []
    for file in files:
        file_tuples.append((await file.read(), file.filename))

    try:
        uris = upload_files_to_kb(bucket, prefix, file_tuples, with_rollback=True)
    except ValueError as e:
        # 지원하지 않는 형식 / 크기 초과 / 개수 초과 등 입력 검증 오류
        return {"uploaded": [], "error": str(e)}
    except Exception as e:
        log.exception("KB API 업로드 실패")
        return {"uploaded": [], "error": str(e)}

    # originals/ 원본(pptx)은 내부 자원이라 응답 목록에서 제외
    uploaded = [
        {"name": uri.rsplit("/", 1)[-1], "s3_uri": uri}
        for uri in uris
        if "/originals/" not in uri
    ]
    return {"uploaded": uploaded, "error": None}
