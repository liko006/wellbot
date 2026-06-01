"""채팅 서비스 - 대화 및 메시지 DB CRUD"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from wellbot.constants import CONVERSATION_LIMIT, KST, MESSAGE_SEQ_MAX_RETRIES
from wellbot.models.chat_message import ChatMessage
from wellbot.models.chat_summary import ChatSummary
from wellbot.services.core.database import get_session


def _verify_ownership(session, smry_id: str, emp_no: str) -> ChatSummary | None:
    """대화 소유권 검증. 소유자가 아니면 None 반환"""
    record = (
        session.query(ChatSummary)
        .filter(
            ChatSummary.chtb_tlk_smry_id == smry_id,
            ChatSummary.emp_no == emp_no,
        )
        .first()
    )
    return record


def list_conversations(emp_no: str) -> list[dict]:
    """사원의 대화 목록 조회 (최근 30개, 메시지 제외)"""
    with get_session() as session:
        rows = (
            session.query(ChatSummary)
            .filter(ChatSummary.emp_no == emp_no)
            .order_by(ChatSummary.rgst_dtm.desc())
            .limit(CONVERSATION_LIMIT)
            .all()
        )
        return [
            {
                "id": r.chtb_tlk_smry_id,
                "title": r.chtb_tlk_smry_ttl or "새 대화",
                "model_name": r.chtb_mdl_nm or "",
                "created_at": r.rgst_dtm.timestamp() if r.rgst_dtm else 0.0,
            }
            for r in rows
        ]


def get_conversation_messages(smry_id: str, emp_no: str) -> list[dict]:
    """대화의 메시지 목록 조회 (소유권 검증 포함).

    첨부파일은 GNB 팝오버에서 별도 표시하므로 메시지에 미포함.
    """
    with get_session() as session:
        if not _verify_ownership(session, smry_id, emp_no):
            return []

        rows = (
            session.query(ChatMessage)
            .filter(
                ChatMessage.chtb_tlk_smry_id == smry_id,
                ChatMessage.msg_role_nm != "system",
            )
            .order_by(
                ChatMessage.chtb_tlk_seq.asc(),
                ChatMessage.rgst_dtm.asc(),
                ChatMessage.chtb_tlk_id.asc(),
            )
            .all()
        )

        return [
            {
                "role": r.msg_role_nm or "user",
                "content": r.chtb_msg_cntt or "",
                "timestamp": r.rgst_dtm.timestamp() if r.rgst_dtm else 0.0,
                "model_name": r.chtb_mdl_nm or "",
                "seq": int(r.chtb_tlk_seq) if r.chtb_tlk_seq is not None else 0,
                "msg_id": r.chtb_tlk_id or "",
            }
            for r in rows
        ]


def save_conversation(
    emp_no: str,
    conv_id: str,
    title: str,
    model_name: str = "",
) -> None:
    """대화 저장 (없으면 INSERT, 있으면 소유자 확인 후 UPDATE)"""
    now = datetime.now(KST)
    with get_session() as session:
        existing = _verify_ownership(session, conv_id, emp_no)
        if existing:
            existing.chtb_tlk_smry_ttl = title
            existing.chtb_mdl_nm = model_name or existing.chtb_mdl_nm
            existing.upd_dtm = now
            existing.uppr_id = emp_no[:20]
        else:
            record = ChatSummary(
                chtb_tlk_smry_id=conv_id,
                emp_no=emp_no,
                chtb_tlk_smry_ttl=title,
                chtb_mdl_nm=model_name or None,
                bkmr_yn="N",
                rgsr_id=emp_no[:20],
                rgst_dtm=now,
                uppr_id=emp_no[:20],
                upd_dtm=now,
            )
            session.add(record)


def update_conversation_title(smry_id: str, title: str, emp_no: str) -> None:
    """대화 제목 업데이트 (소유권 검증 포함)"""
    with get_session() as session:
        record = _verify_ownership(session, smry_id, emp_no)
        if record:
            record.chtb_tlk_smry_ttl = title
            record.upd_dtm = datetime.now(KST)
            record.uppr_id = emp_no[:20]


def append_message(
    smry_id: str,
    role: str,
    content: str,
    emp_no: str,
    model_name: str = "",
    provider: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    reply_time: float | None = None,
    msg_id: str | None = None,
) -> str:
    """메시지 DB 저장 (seq 자동 발급).

    동일 트랜잭션 내에서 MAX(chtb_tlk_seq) + 1 계산과 INSERT 수행.
    UNIQUE(smry_id, seq) 충돌 시 제한된 횟수만큼 재시도.

    Args:
        msg_id: 메시지 고유 ID. 미지정 시 UUID 자동 생성

    Returns:
        저장된 메시지의 chtb_tlk_id (개별 메시지 고유 ID)

    Raises:
        IntegrityError: 재시도 한도 초과 시 마지막 충돌 전파
    """
    total_tokens = input_tokens + output_tokens
    tlk_id = msg_id or uuid.uuid4().hex[:50]

    for attempt in range(MESSAGE_SEQ_MAX_RETRIES):
        try:
            now = datetime.now(KST)
            with get_session() as session:
                max_seq = (
                    session.query(func.max(ChatMessage.chtb_tlk_seq))
                    .filter(ChatMessage.chtb_tlk_smry_id == smry_id)
                    .scalar()
                )
                seq = (int(max_seq) if max_seq is not None else 0) + 1
                record = ChatMessage(
                    chtb_tlk_smry_id=smry_id,
                    chtb_tlk_id=tlk_id,
                    chtb_tlk_seq=seq,
                    msg_role_nm=role,
                    chtb_msg_cntt=content,
                    chtb_mdl_nm=model_name or None,
                    chtb_offr_mdl_nm=provider or None,
                    chtb_inpt_tokn_ecnt=input_tokens or None,
                    chtb_otpt_tokn_ecnt=output_tokens or None,
                    chtb_tot_tokn_ecnt=total_tokens or None,
                    rply_time=Decimal(str(reply_time)) if reply_time else None,
                    rgsr_id=emp_no[:20],
                    rgst_dtm=now,
                    uppr_id=emp_no[:20],
                    upd_dtm=now,
                )
                session.add(record)
            return tlk_id
        except IntegrityError:
            if attempt == MESSAGE_SEQ_MAX_RETRIES - 1:
                raise
            continue

    return tlk_id  # unreachable: 위 루프는 성공/예외로만 종료


def delete_conversation(smry_id: str, emp_no: str) -> None:
    """대화 및 관련 메시지·첨부파일 삭제 (소유권 검증 포함)"""
    from wellbot.models.attachment import Attachment
    from wellbot.models.chat_message_attachment import ChatMessageAttachment

    with get_session() as session:
        if not _verify_ownership(session, smry_id, emp_no):
            return

        msg_ids = [
            row[0]
            for row in session.query(ChatMessage.chtb_tlk_id)
            .filter(ChatMessage.chtb_tlk_smry_id == smry_id)
            .all()
        ]

        if msg_ids:
            file_nos = [
                row[0]
                for row in session.query(ChatMessageAttachment.atch_file_no)
                .filter(ChatMessageAttachment.chtb_tlk_id.in_(msg_ids))
                .all()
            ]

            session.query(ChatMessageAttachment).filter(
                ChatMessageAttachment.chtb_tlk_id.in_(msg_ids)
            ).delete(synchronize_session="fetch")

            if file_nos:
                session.query(Attachment).filter(
                    Attachment.atch_file_no.in_(file_nos)
                ).delete(synchronize_session="fetch")

        session.query(ChatMessage).filter(
            ChatMessage.chtb_tlk_smry_id == smry_id
        ).delete()

        session.query(ChatSummary).filter(
            ChatSummary.chtb_tlk_smry_id == smry_id
        ).delete()
