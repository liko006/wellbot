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

from fastapi import APIRouter, Cookie, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from wellbot.services.auth import auth_service
from wellbot.services.files import storage_service
from wellbot.services.knowledgebase.config import get_kb_config
from wellbot.services.knowledgebase.team_kb_manager import get_dept_cd

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["kb-download"])


class KbDownloadRequest(BaseModel):
    s3_uri: str
    filename: str = ""


def _kb_bucket(section: str) -> str:
    """KB 섹션(personal_kb/shared_kb)의 S3 버킷명 조회. 미설정 시 빈 문자열."""
    return get_kb_config().get(section, {}).get("s3_bucket", "")


def _check_access(bucket: str, key: str, emp_no: str) -> None:
    """S3 bucket+key 기반 접근 권한 확인, 거부 시 HTTPException.

    버킷을 검증하지 않으면 사용자가 s3_uri 의 버킷을 임의로 지정해 IAM 역할이
    읽을 수 있는 다른 버킷의 객체까지 받아갈 수 있으므로(교차 버킷 유출),
    prefix 별로 허용 버킷을 고정. users/·teams/ 는 개인 KB 버킷, shared/ 는
    공용 KB 버킷에만 존재.
    """
    personal_bucket = _kb_bucket("personal_kb")
    shared_bucket = _kb_bucket("shared_kb")
    if key.startswith(f"users/{emp_no}/"):
        if personal_bucket and bucket == personal_bucket:
            return  # 본인 개인 KB
    elif key.startswith("shared/"):
        if shared_bucket and bucket == shared_bucket:
            return  # 공용 KB - 인증된 사용자라면 누구나
    elif key.startswith("teams/"):
        parts = key.split("/")
        if len(parts) >= 2 and personal_bucket and bucket == personal_bucket:
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

    # 3. 버킷+경로 기반 접근 권한 확인
    _check_access(bucket, key, emp_no)

    # 4. S3 head_object 로 존재 + content_type 확인 (동적 버킷)
    head = storage_service.head_object(key, bucket=bucket)
    if head is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="파일을 찾을 수 없습니다.",
        )
    content_type = head.get("ContentType", "application/octet-stream")

    # 5. 파일명 결정 (없으면 key 의 마지막 segment)
    filename = req.filename or key.rsplit("/", 1)[-1]

    # 6. 스트리밍 응답
    return StreamingResponse(
        content=storage_service.iter_download_stream(key, bucket=bucket),
        media_type=content_type,
        headers={
            "Content-Disposition": storage_service.build_content_disposition_header(filename),
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )
