"""KB 출처 문서 다운로드 프록시 엔드포인트.

채팅 답변의 출처(citation) 박스에서 클릭한 KB 문서를
백엔드가 S3 에서 읽어 클라이언트로 스트리밍.

흐름:
    브라우저 → POST /api/download_kb → 백엔드 → S3 → 브라우저

기존 /api/download/{file_no} 는 채팅 첨부파일(atch_file_m 기반)용이라
KB 출처(S3 URI 기반)에는 사용할 수 없어 별도 엔드포인트로 분리.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from urllib.parse import quote

import boto3
from fastapi import APIRouter, Cookie, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from wellbot.services.auth import auth_service
from wellbot.services.knowledgebase.team_kb_manager import get_dept_cd

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["kb-download"])

_CHUNK_SIZE = 8192


@lru_cache(maxsize=1)
def _get_s3():
    """S3 클라이언트 (싱글턴)"""
    return boto3.client("s3")


class KbDownloadRequest(BaseModel):
    s3_uri: str
    filename: str = ""


def _check_access(key: str, emp_no: str) -> None:
    """S3 key 기반 접근 권한 확인, 거부 시 HTTPException"""
    if key.startswith(f"users/{emp_no}/"):
        return  # 본인 개인 KB
    if key.startswith("shared/"):
        return  # 공용 KB - 인증된 사용자라면 누구나
    if key.startswith("teams/"):
        parts = key.split("/")
        if len(parts) >= 2:
            user_dept = get_dept_cd(emp_no)
            if user_dept and parts[1] == user_dept:
                return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="접근 권한이 없습니다.",
    )


@router.post("/download_kb")
async def download_kb_file(
    req: KbDownloadRequest,
    wellbot_auth: str | None = Cookie(default=None),
) -> StreamingResponse:
    """KB 출처 문서를 S3 에서 읽어 클라이언트로 스트리밍.

    Body:
        s3_uri:   "s3://bucket/key" 형식의 S3 URI
        filename: 다운로드 시 사용할 파일명 (없으면 key 의 마지막 segment)
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

    # 2. s3_uri 파싱
    if not req.s3_uri.startswith("s3://"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="잘못된 S3 URI 형식입니다.",
        )
    path = req.s3_uri.removeprefix("s3://")
    if "/" not in path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="잘못된 S3 URI 형식입니다.",
        )
    bucket, key = path.split("/", 1)

    # 3. 경로 기반 접근 권한 확인
    _check_access(key, emp_no)

    # 4. S3 head_object 로 존재 + content_type 확인
    s3 = _get_s3()
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="파일을 찾을 수 없습니다.",
        ) from exc
    content_type = head.get("ContentType", "application/octet-stream")

    # 5. 파일명 결정 + 인코딩 (한글 등 비ASCII 대응)
    filename = req.filename or key.rsplit("/", 1)[-1]
    encoded = quote(filename)

    # 6. 스트리밍 응답
    def _iter_chunks():
        resp = s3.get_object(Bucket=bucket, Key=key)
        body = resp["Body"]
        try:
            while True:
                chunk = body.read(_CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk
        finally:
            body.close()

    return StreamingResponse(
        content=_iter_chunks(),
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
