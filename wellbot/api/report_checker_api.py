"""보고서 오류 검출 — 파일 업로드 커스텀 API.

rx.upload 의 크기 제한을 우회하기 위한 FastAPI 엔드포인트(KB 업로드와 동일 패턴).
PDF 를 수신하면 잡별 prefix 로 S3 에 저장하고 job_id 를 반환한다.
실제 분석은 ReportCheckerState 백그라운드 태스크가 이 job_id 로 수행한다.

엔드포인트:
    POST /api/report_checker/upload
    - multipart/form-data, file: 단일 PDF
    - 사번(emp_no)은 wellbot_auth 세션 쿠키에서 서버가 도출

응답: {"job_id": "...", "filename": "...", "error": null}
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Cookie, File, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from wellbot.logger import log_context
from wellbot.services.auth import auth_service
from wellbot.services.files import storage_service
from wellbot.services.report_checker import storage
from wellbot.services.report_checker.config import get_config

log = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/report_checker/upload")
async def upload_report(
    file: UploadFile = File(...),
    wellbot_auth: str | None = Cookie(default=None),
):
    """PDF 원본을 S3 에 적재하고 job_id 반환."""
    # 1. 인증 — 세션 쿠키에서 emp_no 도출
    if not wellbot_auth:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "로그인이 필요합니다.")
    user = auth_service.validate_session_token(wellbot_auth)
    if not user:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "세션이 만료되었습니다. 다시 로그인해주세요."
        )
    emp_no = user["emp_no"]
    log_context.bind(emp_no=emp_no)

    cfg = get_config()

    # 2. 확장자 검증
    ext = Path(file.filename or "").suffix.lower()
    if ext not in cfg.allowed_extensions:
        allowed = ", ".join(cfg.allowed_extensions)
        return {"job_id": "", "filename": file.filename, "error": f"지원 형식: {allowed}"}

    # 3. 크기 검증 (전체 바이트 읽기 — max_upload_mb 이내)
    data = await file.read()
    size_mb = len(data) / (1024 * 1024)
    if size_mb > cfg.max_upload_mb:
        return {
            "job_id": "",
            "filename": file.filename,
            "error": f"파일 크기 {size_mb:.1f}MB 가 제한 {cfg.max_upload_mb}MB 를 초과합니다.",
        }
    if not data:
        return {"job_id": "", "filename": file.filename, "error": "빈 파일입니다."}

    # 4. S3 저장
    job_id = uuid.uuid4().hex
    try:
        storage.save_source(emp_no, job_id, data)
    except Exception as e:
        log.exception("report_checker 업로드 저장 실패")
        return {"job_id": "", "filename": file.filename, "error": str(e)}

    log.info("report_checker 업로드 완료 job_id=%s file=%s", job_id, file.filename)
    return {"job_id": job_id, "filename": file.filename, "error": None}


class DownloadRequest(BaseModel):
    job_id: str
    filename: str = ""


@router.post("/api/report_checker/download")
async def download_report(
    req: DownloadRequest,
    wellbot_auth: str | None = Cookie(default=None),
) -> StreamingResponse:
    """결과 HTML 을 S3 에서 읽어 클라이언트로 스트리밍.

    presigned URL 대신 프록시 스트리밍을 쓰는 이유: S3 의 response-content-disposition
    은 ISO-8859-1 만 허용해 한글 파일명이 불가하지만, 여기서는 RFC 5987
    (build_content_disposition_header) 로 인코딩해 한글 파일명을 안전하게 내려준다.
    emp_no 는 세션 쿠키에서 도출하므로 사용자는 본인 잡 결과에만 접근한다.
    """
    if not wellbot_auth:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "로그인이 필요합니다.")
    user = auth_service.validate_session_token(wellbot_auth)
    if not user:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "세션이 만료되었습니다. 다시 로그인해주세요."
        )
    emp_no = user["emp_no"]

    if not req.job_id or not storage.result_exists(emp_no, req.job_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "결과 파일을 찾을 수 없습니다.")

    filename = req.filename or "report_result.html"
    return StreamingResponse(
        content=storage.iter_result(emp_no, req.job_id),
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Disposition": storage_service.build_content_disposition_header(filename),
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )
