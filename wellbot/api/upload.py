"""파일 업로드 엔드포인트.

흐름:
    1. JWT 쿠키(wellbot_auth) 검증 → emp_no
    2. 대화당 개수/용량 한도 검증
    3. 파일 스트리밍으로 임시 파일 저장 (메모리 부담 최소화)
    4. attachment_service.register_attachment() → S3 원본 + DB
    5. attachment_service.process_attachment() 를 백그라운드 실행
       (파싱 + 청킹 + 임베딩 + S3 파생물)
    6. 즉시 {file_no, name, mime, status} 응답

응답:
    200: {"file_no": int, "name": str, "mime": str, "status": "processing"}
    400: validation 실패 (타입/크기/개수 초과)
    401: 인증 실패
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Cookie,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from fastapi import File as FastAPIFile

from wellbot.constants import (
    FILE_MAX_PER_CONVERSATION,
    FILE_MAX_PER_MESSAGE,
    FILE_MAX_SIZE_MB,
    FILE_MAX_TOTAL_SIZE_MB,
    FILE_PARSER_MODE,
    LOCAL_SUPPORTED_EXTS,
    UPSTAGE_SUPPORTED_EXTS,
)
from wellbot.logger import log_context
from wellbot.services.auth import auth_service
from wellbot.services.files import attachment_service, file_parser

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["upload"])

# 메모리 점유 최소화를 위한 스트리밍 청크 크기
_STREAM_CHUNK = 1024 * 1024  # 1MB


def _allowed_extensions() -> frozenset[str]:
    """현재 파서 모드에서 허용되는 확장자 집합."""
    mode = (FILE_PARSER_MODE or "local").lower()
    if mode == "local":
        return LOCAL_SUPPORTED_EXTS | file_parser.IMAGE_EXTS
    if mode == "upstage":
        return UPSTAGE_SUPPORTED_EXTS | frozenset({".txt", ".md"}) | file_parser.IMAGE_EXTS
    # hybrid
    return LOCAL_SUPPORTED_EXTS | UPSTAGE_SUPPORTED_EXTS


def _validate_file(upload: UploadFile) -> tuple[str, str]:
    """확장자/타입 검증. (filename, ext) 반환"""
    filename = (upload.filename or "").strip()
    if not filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="파일명이 비어있습니다.",
        )

    ext = Path(filename).suffix.lower()
    if not ext:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"확장자 없는 파일은 지원하지 않습니다: {filename}",
        )

    allowed = _allowed_extensions()
    if ext not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"지원하지 않는 파일 형식입니다: {ext}. "
                f"지원 형식: {', '.join(sorted(allowed))}"
            ),
        )

    return filename, ext


def _stream_to_tempfile(upload: UploadFile, max_bytes: int) -> tuple[Path, int]:
    """업로드를 스트리밍으로 임시파일에 저장.

    max_bytes 초과 시 413 에러.

    Returns:
        (임시파일 경로, 실제 바이트 크기)
    """
    tmp_dir = Path(tempfile.gettempdir()) / "wellbot_upload"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    tmp_path = Path(
        tempfile.mkstemp(
            prefix="upload_",
            suffix=Path(upload.filename or "").suffix,
            dir=str(tmp_dir),
        )[1]
    )

    total = 0
    try:
        with open(tmp_path, "wb") as out:
            while True:
                chunk = upload.file.read(_STREAM_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=(
                            f"파일 크기가 제한 {FILE_MAX_SIZE_MB}MB 를 초과합니다."
                        ),
                    )
                out.write(chunk)
    except HTTPException:
        tmp_path.unlink(missing_ok=True)
        raise
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    return tmp_path, total


@router.post("/upload")
async def upload_attachment(
    background: BackgroundTasks,
    conversation_id: str = Form(...),
    message_id: str = Form(""),
    file: UploadFile = FastAPIFile(...),
    wellbot_auth: str | None = Cookie(default=None),
) -> dict:
    """대화에 첨부파일 업로드.

    Form 필드:
        conversation_id: 대화 세션 ID (chtb_tlk_smry_id)
        message_id: 메시지 고유 ID (chtb_tlk_id). 미지정 시 빈 문자열
        file: 업로드 파일
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

    # 2. conversation_id 정규화
    smry_id = (conversation_id or "").strip()
    if not smry_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="conversation_id 가 필요합니다.",
        )

    # 2-1. message_id 정규화
    msg_id = (message_id or "").strip()

    # 이후 모든 로그에 emp/conv/msg 상관관계 표기
    log_context.bind(emp_no=emp_no, conversation_id=smry_id, message_id=msg_id or None)

    # 3. 파일 확장자 검증
    filename, ext = _validate_file(file)

    # 4. 대화당 개수/용량 한도 체크
    file_count, total_tokens = (
        attachment_service.count_conversation_attachments(smry_id, pending_msg_id=msg_id)
    )
    if file_count >= FILE_MAX_PER_CONVERSATION:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"대화당 첨부 가능한 최대 파일 개수({FILE_MAX_PER_CONVERSATION})"
                "를 초과했습니다."
            ),
        )

    # 5. 스트리밍으로 임시 파일 저장 (사이즈 한도 동시 체크)
    max_bytes = FILE_MAX_SIZE_MB * 1024 * 1024
    tmp_path, size_bytes = _stream_to_tempfile(file, max_bytes)

    # 6. 대화 누적 용량 체크 (Upstage/임베딩 비용 가드는 별도 토큰 단위로 수행)
    total_size_mb = (size_bytes / (1024 * 1024))
    if total_size_mb > FILE_MAX_TOTAL_SIZE_MB:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"대화당 누적 용량 제한({FILE_MAX_TOTAL_SIZE_MB}MB)을 초과합니다."
            ),
        )

    try:
        # 7. MIME 판별
        content_type = file_parser.guess_mime(filename)

        # 8. S3 원본 업로드 + DB 등록 (동기)
        try:
            file_no = attachment_service.register_attachment(
                emp_no=emp_no,
                smry_id=smry_id,
                filename=filename,
                content_type=content_type,
                file_path=tmp_path,
                msg_id=msg_id,
            )
        except Exception as exc:
            log.exception("register_attachment 실패: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"업로드 실패: {exc}",
            ) from exc

        # 9. 파싱/임베딩은 백그라운드로 — 클라이언트 응답 즉시 반환
        background.add_task(_run_process, file_no, emp_no, tmp_path)

        return {
            "file_no": file_no,
            "name": filename,
            "mime": content_type,
            "size_bytes": size_bytes,
            "status": "processing",
        }

    except HTTPException:
        tmp_path.unlink(missing_ok=True)
        raise


def _run_process(file_no: int, emp_no: str, tmp_path: Path) -> None:
    """백그라운드 파싱·임베딩 작업. 실패해도 로깅 후 종료.

    요청 스코프가 이미 해제된 뒤 실행되므로 로그 상관관계를 재바인딩.
    """
    log_context.bind(emp_no=emp_no, request_id=log_context.new_request_id())
    try:
        attachment_service.process_attachment(file_no, emp_no)
    except Exception as exc:
        log.exception("process_attachment 백그라운드 실패: file_no=%s %s", file_no, exc)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            log.debug("임시파일 정리 실패 path=%s", tmp_path, exc_info=True)
