"""채팅 서비스 - 대화 및 메시지 DB CRUD (삭제 시 첨부 S3 파생물 정리 포함)"""

import logging
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from wellbot.constants import CONVERSATION_LIMIT, KST, MESSAGE_SEQ_MAX_RETRIES
from wellbot.models.chat_message import ChatMessage
from wellbot.models.chat_summary import ChatSummary
from wellbot.services.core.database import get_session
from wellbot.services.files import storage_service

log = logging.getLogger(__name__)


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


def can_attach(smry_id: str, emp_no: str) -> bool:
    """대화 저장 전 첨부 업로드를 허용해도 되는지 판정 (IDOR 방어).

    첨부는 첫 메시지가 chtb_msg_d 에 저장되기 전에 올라갈 수 있어, 이 시점엔 chtb_smry_d
    행이 아직 없다. '소유 대화'만 허용하면 정상 흐름이 막히고, 검증을 생략하면 타인 대화
    ID 를 넣어 첨부를 끼워 넣을 수 있다(첨부 조회는 emp_no 가 아닌 대화 ID 기준). 그래서:

      · 대화 행이 있으면  → 소유자만 허용
      · 대화 행이 없으면  → 메시지도 없어야(=진짜 새 세션) 허용
    """
    if not smry_id:
        return False
    with get_session() as session:
        owner = (
            session.query(ChatSummary.emp_no)
            .filter(ChatSummary.chtb_tlk_smry_id == smry_id)
            .first()
        )
        if owner is not None:
            return owner.emp_no == emp_no
        has_messages = (
            session.query(ChatMessage.chtb_tlk_id)
            .filter(ChatMessage.chtb_tlk_smry_id == smry_id)
            .first()
            is not None
        )
    return not has_messages


def list_conversations(emp_no: str) -> list[dict]:
    """사원의 대화 목록 조회 (최근 30개, 메시지 제외).

    AI 서비스/에이전트가 생성한 기록(메시지에 agnt_id 태깅)은 사람의 채팅이 아니므로
    사이드바 목록에서 제외한다. (예: 보고서 오류 검출 사용 내역)
    """
    with get_session() as session:
        agent_smry = (
            session.query(ChatMessage.chtb_tlk_smry_id)
            .filter(ChatMessage.agnt_id.isnot(None))
            .distinct()
        )
        rows = (
            session.query(ChatSummary)
            .filter(
                ChatSummary.emp_no == emp_no,
                ChatSummary.chtb_tlk_smry_id.notin_(agent_smry),
            )
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


def get_conversation_messages(
    smry_id: str,
    emp_no: str,
    *,
    limit: int | None = None,
    before_seq: int | None = None,
) -> tuple[list[dict], bool]:
    """대화의 메시지 목록 조회 (소유권 검증 포함).

    Args:
        limit: 반환할 최근 메시지 최대 개수. None 이면 전체(레거시 동작).
        before_seq: 이 seq 미만(더 오래된) 메시지만 조회 — "이전 더 보기" 커서.

    Returns:
        (messages, has_more_older):
          - messages: 시간순(오름차순) dict 목록
          - has_more_older: 더 오래된 메시지가 남아있는지 여부

    첨부파일은 GNB 팝오버에서 별도 표시하므로 메시지에 미포함.
    """
    with get_session() as session:
        if not _verify_ownership(session, smry_id, emp_no):
            return [], False

        base = session.query(ChatMessage).filter(
            ChatMessage.chtb_tlk_smry_id == smry_id,
            ChatMessage.msg_role_nm != "system",
        )
        if before_seq is not None:
            base = base.filter(ChatMessage.chtb_tlk_seq < before_seq)

        if limit is None:
            rows = base.order_by(
                ChatMessage.chtb_tlk_seq.asc(),
                ChatMessage.rgst_dtm.asc(),
                ChatMessage.chtb_tlk_id.asc(),
            ).all()
            has_more = False
        else:
            # 최근 limit+1 개를 내림차순으로 가져와 has_more 판정 후 시간순 복원.
            rows_desc = (
                base.order_by(
                    ChatMessage.chtb_tlk_seq.desc(),
                    ChatMessage.rgst_dtm.desc(),
                    ChatMessage.chtb_tlk_id.desc(),
                )
                .limit(limit + 1)
                .all()
            )
            has_more = len(rows_desc) > limit
            rows = list(reversed(rows_desc[:limit]))

        messages = [
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
        return messages, has_more


def get_message_content(smry_id: str, emp_no: str, seq: int) -> str | None:
    """대화 내 단일 메시지의 본문 조회 (소유권 검증 포함).

    보고서 생성 핸드오프용 — 채팅에서 고른 AI 메시지 1건을 report_maker 로 넘길 때
    사용한다. 소유자가 아니거나(IDOR 방지) 해당 seq 메시지가 없으면 None 을 반환한다.
    system 메시지는 제외한다.
    """
    with get_session() as session:
        if not _verify_ownership(session, smry_id, emp_no):
            return None

        row = (
            session.query(ChatMessage)
            .filter(
                ChatMessage.chtb_tlk_smry_id == smry_id,
                ChatMessage.chtb_tlk_seq == seq,
                ChatMessage.msg_role_nm != "system",
            )
            .first()
        )
        if row is None:
            return None
        return row.chtb_msg_cntt or ""


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
    """대화 및 관련 메시지·첨부파일 삭제 (소유권 검증 포함).

    DB 행과 함께 첨부의 **S3 파생물(원본·청크·인덱스)도 지운다.** 개별 첨부 삭제
    (`attachment_service.delete_attachment`)는 이미 S3 를 지우는데 대화 삭제만 DB 만
    지워서, 대화를 지울수록 S3 에 고아 객체가 쌓이던 비대칭을 없앤다.

    삭제 대상은 **DB 에 기록된 prefix 뿐**이다(경로를 조합해 지우지 않는다). 이 값은
    업로드 시 `build_prefix(emp_no, smry_id, file_no)` 로만 기록되므로 사용자 본인의
    첨부 경로만 가리킨다 — 공용 KB 문서는 애초에 이 테이블에 행이 없어 대상이 될 수 없다.

    S3 삭제는 DB 커밋 뒤 best-effort 로 수행한다(실패해도 대화 삭제 자체는 완료).
    """
    from wellbot.models.attachment import Attachment
    from wellbot.models.chat_message_attachment import ChatMessageAttachment

    s3_prefixes: list[str] = []

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

            if file_nos:
                # 행 삭제 전에 S3 경로를 확보한다(삭제 후에는 조회 불가)
                s3_prefixes = [
                    row[0]
                    for row in session.query(Attachment.atch_file_url_addr)
                    .filter(Attachment.atch_file_no.in_(file_nos))
                    .all()
                    if row[0]
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

    # DB 커밋 후 S3 정리 — 실패해도 대화 삭제는 이미 끝났으므로 로그만 남긴다
    # (여기서 예외를 올리면 "삭제했는데 실패했다"는 모순된 상태가 사용자에게 보인다).
    for prefix in s3_prefixes:
        try:
            deleted = storage_service.delete_prefix(prefix)
            log.info(
                "conversation attachment objects deleted",
                extra={"smry_id": smry_id, "prefix": prefix, "deleted": deleted},
            )
        except Exception:
            log.warning(
                "conversation attachment S3 cleanup failed",
                extra={"smry_id": smry_id, "prefix": prefix},
                exc_info=True,
            )
