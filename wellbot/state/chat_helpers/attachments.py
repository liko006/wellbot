"""첨부파일 관련 순수 헬퍼.

State var 를 직접 조작하지 않고 인자/리턴 값으로만 동작.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from wellbot.services.ai.bedrock import fit_image_for_bedrock, image_format
from wellbot.services.files import attachment_service
from wellbot.state.chat_models import AttachmentInfo

log = logging.getLogger(__name__)


def _status_from_token_count(token_count: int | None) -> str:
    """token_count → UI 상태. None=처리중 / 음수=실패 / 0 이상=완료."""
    if token_count is None:
        return "processing"
    if token_count < 0:
        return "failed"
    return "ready"


def row_to_attachment_info(row: Any) -> AttachmentInfo:
    """ORM/DTO 행을 AttachmentInfo 로 변환"""
    return AttachmentInfo(
        file_no=row.file_no,
        name=row.file_name,
        mime=row.mime,
        token_count=row.token_count or 0,
        status=_status_from_token_count(row.token_count),
    )


def rows_to_attachment_infos(rows: Iterable[Any]) -> list[AttachmentInfo]:
    """행 목록을 AttachmentInfo 목록으로 변환"""
    return [row_to_attachment_info(r) for r in rows]


def fetch_pending_attachments(
    emp_no: str,
    conv_id: str,
    pending_msg_id: str,
    already_sent: set[int],
) -> list[AttachmentInfo] | None:
    """업로드 직후 표시할 pending 첨부 목록을 DB 에서 조회.

    Returns:
        list: 새 pending_attachments 로 그대로 대입할 수 있는 목록
        None: emp_no/conv_id 가 비었거나 조회 실패. 상태 갱신을 건너뜀
    """
    if not emp_no or not conv_id:
        return None
    try:
        if pending_msg_id:
            rows = attachment_service.get_attachments_by_msg_id(pending_msg_id)
        else:
            rows = attachment_service.get_conversation_attachments(conv_id)
    except Exception:
        log.warning("첨부 조회 실패 conv_id=%s msg_id=%s", conv_id, pending_msg_id, exc_info=True)
        return None

    return [
        row_to_attachment_info(r)
        for r in rows
        if r.file_no not in already_sent
    ]


def collect_image_blocks(
    attachments: list[AttachmentInfo],
    model: Any,
) -> list[dict]:
    """첨부 목록에서 이미지만 골라 Bedrock Converse image block 으로 변환"""
    if not attachments:
        return []

    supports_vision = getattr(model, "supports_vision", False)
    blocks: list[dict] = []

    for a in attachments:
        fmt = image_format(a.name)
        if not fmt:
            continue
        if not supports_vision:
            # vision 미지원 모델 - UI 에서 사용자에게 이미 안내되었다고 가정
            continue
        try:
            data = attachment_service.download_original_bytes(a.file_no)
        except Exception:
            data = None
        if not data:
            continue
        # Bedrock 제약(≤5MB, ≤8000px)에 맞게 정규화 — 초과 시 하드 실패 대신 다운스케일
        fitted = fit_image_for_bedrock(data, fmt)
        if not fitted:
            log.warning("이미지 '%s' 정규화 실패 → 전송 제외", a.name)
            continue
        out_bytes, out_fmt = fitted
        blocks.append({"format": out_fmt, "bytes": out_bytes})

    return blocks
