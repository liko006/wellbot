"""첨부파일 관련 순수 헬퍼.

State var 를 직접 조작하지 않고, 인자/리턴 값으로만 동작한다.
"""

from __future__ import annotations

from typing import Any, Iterable

from wellbot.services import attachment_service
from wellbot.services.bedrock_client import image_format
from wellbot.state.chat_models import AttachmentInfo


def row_to_attachment_info(row: Any) -> AttachmentInfo:
    """ORM/DTO 행을 AttachmentInfo 로 변환."""
    return AttachmentInfo(
        file_no=row.file_no,
        name=row.file_name,
        mime=row.mime,
        token_count=row.token_count or 0,
        status="ready" if row.token_count is not None else "processing",
    )


def rows_to_attachment_infos(rows: Iterable[Any]) -> list[AttachmentInfo]:
    """행 목록을 AttachmentInfo 목록으로 변환."""
    return [row_to_attachment_info(r) for r in rows]


def fetch_pending_attachments(
    emp_no: str,
    conv_id: str,
    pending_msg_id: str,
    already_sent: set[int],
) -> list[AttachmentInfo] | None:
    """업로드 직후 표시할 pending 첨부 목록을 DB 에서 조회한다.

    반환값:
        list — 새 pending_attachments 로 그대로 대입할 수 있는 목록
        None — emp_no/conv_id 가 비었거나 조회 실패 (상태 갱신을 건너뜀)
    """
    if not emp_no or not conv_id:
        return None
    try:
        if pending_msg_id:
            rows = attachment_service.get_attachments_by_msg_id(pending_msg_id)
        else:
            rows = attachment_service.get_conversation_attachments(conv_id)
    except Exception:
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
    """첨부 목록에서 이미지만 골라 Bedrock Converse image block 으로 변환."""
    if not attachments:
        return []

    supports_vision = getattr(model, "supports_vision", False)
    blocks: list[dict] = []

    for a in attachments:
        fmt = image_format(a.name)
        if not fmt:
            continue  # 이미지 아님
        if not supports_vision:
            # vision 미지원 모델 - UI 에서 사용자에게 이미 알렸다고 가정, 스킵
            continue
        try:
            data = attachment_service.download_original_bytes(a.file_no)
        except Exception:
            data = None
        if not data:
            continue
        blocks.append({"format": fmt, "bytes": data})

    return blocks
