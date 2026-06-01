"""파일 다운로드 프록시 엔드포인트.

백엔드가 S3 에서 파일을 읽어 클라이언트로 스트리밍 응답.

흐름:
    브라우저 → POST /api/download/{file_no} → 백엔드 → S3 → 브라우저
"""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Cookie, HTTPException, status
from fastapi.responses import StreamingResponse

from wellbot.logger import log_context
from wellbot.services.auth import auth_service
from wellbot.services.files import attachment_service, storage_service

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["download"])


@router.post("/download/{file_no}")
async def download_file(
    file_no: int,
    wellbot_auth: str | None = Cookie(default=None),
) -> StreamingResponse:
    """첨부파일을 S3 에서 읽어 클라이언트로 스트리밍.

    일부 프록시/SPA 환경에서 GET /api/* 요청이
    프론트엔드 라우터에 의해 가로채질 수 있으므로 POST 로 통일.

    Path:
        file_no: 첨부파일 번호 (atch_file_m PK)
    Cookie:
        wellbot_auth: 로그인 세션 토큰 (JWT)
    """
    # 1. 인증
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
    log_context.bind(emp_no=emp_no)

    # 2. 소유권 확인
    if not attachment_service.verify_ownership(file_no, emp_no):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="접근 권한이 없습니다.",
        )

    # 3. 첨부파일 정보 조회
    att = attachment_service.get_attachment(file_no)
    if not att or not att.s3_prefix:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="파일을 찾을 수 없습니다.",
        )

    ext = Path(att.file_name).suffix.lower()
    s3_key = f"{att.s3_prefix}original{ext}"
    content_type = att.mime or "application/octet-stream"

    # 4. S3 존재 확인
    if not storage_service.object_exists(s3_key):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="S3 에서 파일을 찾을 수 없습니다.",
        )

    # 5. 파일명 인코딩 (한글 등 비ASCII 대응)
    filename = att.file_name or f"file_{file_no}{ext}"
    encoded = quote(filename)

    # 6. S3 스트리밍 응답
    # cross-origin 환경에서 JS 가 Content-Disposition 헤더를 읽으려면
    # Access-Control-Expose-Headers 에 명시 필요
    return StreamingResponse(
        content=storage_service.iter_download_stream(s3_key),
        media_type=content_type,
        headers={
            "Content-Disposition": (
                f"attachment; "
                f'filename="{encoded}"; '
                f"filename*=UTF-8''{encoded}"
            ),
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )
