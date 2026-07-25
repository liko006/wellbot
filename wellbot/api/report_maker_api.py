"""보고서 문구 작성 지원 — 파일 업로드 커스텀 API.

rx.upload 의 크기 제한을 우회하기 위한 FastAPI 엔드포인트(report_checker 와 동일 패턴).
스타일 학습 문서 또는 주제 첨부를 수신해 잡별 prefix 로 S3 에 저장하고 key 를 반환한다.
실제 분석/사용은 ReportMakerState 가 이 key 로 수행한다.

엔드포인트:
    POST /api/report_maker/upload
    - multipart/form-data: file(단일), template(보고서 유형 id), kind("style"|"topic")
    - 사번(emp_no)은 wellbot_auth 세션 쿠키에서 서버가 도출(클라이언트 값 신뢰 금지)

응답: {"key": "...", "filename": "...", "error": null}
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Cookie, File, Form, HTTPException, UploadFile, status

from wellbot.logger import log_context
from wellbot.services.auth import auth_service
from wellbot.services.files import attachment_service, file_parser
from wellbot.services.report_maker import storage
from wellbot.services.report_maker.config import get_config
from wellbot.services.report_maker.parsing import magic_bytes_ok

log = logging.getLogger(__name__)

router = APIRouter()


def _require_emp_no(wellbot_auth: str | None) -> str:
    """세션 쿠키에서 emp_no 도출. 실패 시 401."""
    if not wellbot_auth:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "로그인이 필요합니다.")
    user = auth_service.validate_session_token(wellbot_auth)
    if not user:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "세션이 만료되었습니다. 다시 로그인해주세요."
        )
    return user["emp_no"]


@router.post("/api/report_maker/upload")
async def upload_file(
    file: UploadFile = File(...),
    template: str = Form(...),
    kind: Literal["style", "topic"] = Form("style"),
    session_id: str = Form(""),
    msg_id: str = Form(""),
    wellbot_auth: str | None = Cookie(default=None),
):
    """스타일 문서/주제 첨부를 S3 에 적재하고 key 반환."""
    emp_no = _require_emp_no(wellbot_auth)
    log_context.bind(emp_no=emp_no)

    if not template.strip():
        return {"key": "", "filename": file.filename, "error": "보고서 유형이 필요합니다."}

    cfg = get_config()

    # 확장자 검증
    ext = Path(file.filename or "").suffix.lower()
    if ext not in cfg.allowed_extensions:
        allowed = ", ".join(cfg.allowed_extensions)
        return {"key": "", "filename": file.filename, "error": f"지원 형식: {allowed}"}

    # 크기 검증
    data = await file.read()
    size_mb = len(data) / (1024 * 1024)
    if size_mb > cfg.max_upload_mb:
        return {
            "key": "",
            "filename": file.filename,
            "error": f"파일 크기 {size_mb:.1f}MB 가 제한 {cfg.max_upload_mb}MB 를 초과합니다.",
        }
    if not data:
        return {"key": "", "filename": file.filename, "error": "빈 파일입니다."}

    # 매직바이트 검증 — 확장자 위조/형식 불일치 차단
    if not magic_bytes_ok(ext, data):
        return {
            "key": "",
            "filename": file.filename,
            "error": "파일 내용이 확장자와 일치하지 않습니다.",
        }

    # 주제 첨부 → 정식 첨부(atch_file_m)로 등록: DB 기록 + 재조회·다운로드 지원.
    #   메시지(msg_id)에 매핑되어, 전송 시 append_message 가 같은 msg_id 로 저장하면 연결된다.
    #   RAG 파싱(process_attachment)은 하지 않는다(report_maker 는 텍스트를 인라인 추출).
    if kind == "topic":
        if not session_id.strip():
            return {"file_no": 0, "filename": file.filename, "error": "세션 정보가 필요합니다."}
        fd, tmp = tempfile.mkstemp(suffix=ext, prefix="rptmk_up_")
        os.close(fd)
        try:
            with open(tmp, "wb") as f:
                f.write(data)
            file_no = attachment_service.register_attachment(
                emp_no=emp_no,
                smry_id=session_id.strip(),
                filename=file.filename or "file",
                content_type=file_parser.guess_mime(file.filename or ""),
                file_path=Path(tmp),
                msg_id=msg_id.strip(),
            )
        except Exception:
            log.exception("report_maker 주제 첨부 등록 실패")
            return {"file_no": 0, "filename": file.filename, "error": "파일 저장에 실패했습니다."}
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        log.info("report_maker 주제 첨부 등록 완료 file_no=%s msg_id=%s", file_no, msg_id)
        return {"file_no": file_no, "filename": file.filename, "error": None}

    # 스타일 문서 → report_maker 자체 S3 저장(스타일 학습 전용)
    try:
        key = storage.save_style_doc(emp_no, template, file.filename or "file", data)
    except Exception:
        log.exception("report_maker 스타일 업로드 저장 실패")
        return {"key": "", "filename": file.filename, "error": "파일 저장에 실패했습니다."}

    log.info("report_maker 스타일 업로드 완료 key=%s", key)
    return {"key": key, "filename": file.filename, "error": None}
