"""첨부파일 서비스.

업로드 파이프라인:
    1. register_attachment(): 파일 업로드 직후 S3 원본 저장 + DB 레코드 생성
    2. process_attachment(): 파싱 → 청킹 → 임베딩 → S3 파생물 저장

atch_file_no 는 BigInteger PK 이며 DB AUTO_INCREMENT 로 자동 발급.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from wellbot.models.attachment import Attachment
from wellbot.models.chat_message import ChatMessage
from wellbot.models.chat_message_attachment import ChatMessageAttachment
from wellbot.constants import (
    DB_UPDATE_RETRIES,
    DB_UPDATE_RETRY_BASE_DELAY,
    KST,
    S3_DERIVATIVE_UPLOAD_RETRIES,
)
from wellbot.logger import log_timing
from wellbot.paths import wellbot_temp_dir
from wellbot.services.ai import embedding_service
from wellbot.services.core import cpu_pool
from wellbot.services.core.database import get_session
from wellbot.services.files import chunker, file_parser, storage_service

log = logging.getLogger(__name__)

# atch_file_tokn_ecnt(=token_count) 센티널.
#   None: 처리 중 / 0 이상: 처리 완료(0=이미지·빈 결과) / 음수: 처리 실패
# 실패를 영속화해 UI 가 '처리중'에 무한 대기하지 않고 '실패'로 표시되게 한다.
TOKEN_COUNT_FAILED = -1


# ── 데이터 전송 객체 ──


@dataclass(frozen=True)
class AttachmentRecord:
    """첨부파일 레코드 (조회 결과)"""

    file_no: int
    file_name: str
    s3_prefix: str
    token_count: int | None  # None = 처리 중, 0 이상 = 처리 완료
    mime: str


# ── 원본 파일 S3 업로드 + DB 등록 ──


def register_attachment(
    emp_no: str,
    smry_id: str,
    filename: str,
    content_type: str,
    file_path: Path,
    msg_id: str = "",
) -> int:
    """업로드된 원본 파일을 S3 에 저장하고 DB 에 등록.

    Args:
        emp_no: 업로드한 사원 번호
        smry_id: 대화 세션 ID (S3 prefix 생성용)
        filename: 원본 파일명 (확장자 포함)
        content_type: MIME 타입
        file_path: 서버에 임시 저장된 파일 경로
        msg_id: 메시지 고유 ID (chtb_tlk_id). 첨부파일-메시지 매핑에 사용

    Returns:
        생성된 atch_file_no
    """
    now = datetime.now(KST)

    with get_session() as session:
        # atch_file_m INSERT (file_no 는 DB AUTO_INCREMENT)
        record = Attachment(
            atch_file_nm=filename[:300],
            atch_file_url_addr="",  # S3 업로드 후 갱신
            atch_file_tokn_ecnt=None,  # 파싱 후 업데이트
            rgst_dtm=now,
            rgsr_id=emp_no[:20],
            upd_dtm=now,
            uppr_id=emp_no[:20],
        )
        session.add(record)
        session.flush()  # auto-increment PK 발급을 위해 flush
        file_no = record.atch_file_no

        s3_prefix = storage_service.build_prefix(emp_no, smry_id, file_no)
        ext = Path(filename).suffix.lower()
        original_key = f"{s3_prefix}original{ext}"
        with open(file_path, "rb") as f:
            storage_service.upload_streaming(f, original_key, content_type)

        record.atch_file_url_addr = s3_prefix[:500]

        mapping = ChatMessageAttachment(
            chtb_tlk_id=msg_id,
            atch_file_no=file_no,
            rgst_dtm=now,
            rgsr_id=emp_no[:20],
            upd_dtm=now,
            uppr_id=emp_no[:20],
        )
        session.add(mapping)

    return file_no


# ── 파생물 S3 원자적 업로드 ──


def _safe_delete(key: str) -> None:
    """S3 오브젝트를 best-effort 삭제. 실패해도 예외를 전파하지 않음"""
    try:
        storage_service.delete_object(key)
    except Exception as exc:
        log.warning("부분 잔존물 정리 실패 key=%s err=%s", key, exc)


def _upload_derivatives_atomic(
    chunks_key: str,
    chunks_bytes: bytes,
    index_key: str,
    index_bytes: bytes,
    max_retries: int = S3_DERIVATIVE_UPLOAD_RETRIES,
) -> None:
    """chunks.jsonl 와 index.faiss 를 원자적으로 업로드.

    한쪽이라도 실패하면 방금 올린 다른 쪽을 정리하고 재시도.
    모든 시도가 실패하면 마지막 예외를 전파.

    Raises:
        Exception: 재시도 한도 초과 시 마지막 업로드 예외
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            storage_service.upload_bytes(
                chunks_bytes,
                chunks_key,
                content_type="application/x-jsonlines",
            )
            try:
                storage_service.upload_bytes(
                    index_bytes,
                    index_key,
                    content_type="application/octet-stream",
                )
                return  # 양쪽 모두 성공 → commit point 진입 가능
            except Exception:
                # index 업로드 실패 시 이미 올라간 chunks 를 롤백
                _safe_delete(chunks_key)
                raise
        except Exception as exc:
            last_exc = exc
            log.warning(
                "파생물 업로드 실패 (시도 %d/%d): %s",
                attempt + 1, max_retries, exc,
            )
    assert last_exc is not None
    raise last_exc


# ── 파싱 + 청킹 + 임베딩 + S3 파생물 저장 ──


def process_attachment(file_no: int, emp_no: str) -> bool:
    """첨부파일 파싱 후 파생물(청크/인덱스)을 S3 에 저장.

    이미지 파일은 파싱·임베딩을 건너뛰고 토큰 수만 0 으로 기록.
    이미지는 Bedrock Converse vision block 으로 직접 전달되므로 별도 임베딩 불필요.

    Args:
        file_no: atch_file_m PK
        emp_no: 업데이트 주체

    Returns:
        성공 여부
    """
    with get_session() as session:
        record = session.get(Attachment, file_no)
        if not record:
            log.warning("process_attachment: file_no=%s 레코드 없음", file_no)
            return False
        s3_prefix = record.atch_file_url_addr or ""
        filename = record.atch_file_nm or f"file_{file_no}"

    if not s3_prefix:
        log.error("process_attachment: file_no=%s S3 prefix 비어있음", file_no)
        return False

    ext = Path(filename).suffix.lower()
    original_key = f"{s3_prefix}original{ext}"

    if file_parser.is_image(filename):
        _update_token_count(file_no, emp_no, 0)
        log.info("process_attachment: 이미지 파일 스킵 (file_no=%s)", file_no)
        return True

    tmp_dir = wellbot_temp_dir("wellbot_attachment_process")
    tmp_path = tmp_dir / f"{file_no}_{Path(filename).name}"
    overall_start = time.time()
    try:
        with log_timing("attachment.download", logger=log, file_no=file_no):
            storage_service.download_to_file(original_key, tmp_path)

        with log_timing("attachment.parse", logger=log, file_no=file_no):
            # CPU 바운드 파싱을 별도 프로세스로 오프로드(GIL 분리).
            # 풀 비활성/실패 시 현재 스레드에서 파싱으로 자동 폴백.
            parsed = cpu_pool.parse_document(None, tmp_path)

        if not parsed.text.strip():
            log.warning("process_attachment: file_no=%s 파싱 결과 비어있음", file_no)
            _update_token_count(file_no, emp_no, 0)
            return True  # 파싱은 "성공" 했으나 내용 없음

        with log_timing("attachment.chunk", logger=log, file_no=file_no) as ctx:
            chunks = chunker.chunk_text(parsed.text)
            total_tokens = sum(c.token_count for c in chunks)
            ctx["chunks"] = len(chunks)
            ctx["tokens"] = total_tokens

        with log_timing("attachment.embed", logger=log, file_no=file_no, chunks=len(chunks)):
            embeddings = embedding_service.embed_texts([c.text for c in chunks])

        index = embedding_service.build_index(embeddings)
        index_bytes = embedding_service.serialize_index(index)

        # 원자적 업로드: 한쪽 실패 시 다른 쪽 정리 후 재시도
        chunks_key = f"{s3_prefix}chunks.jsonl"
        index_key = f"{s3_prefix}index.faiss"
        with log_timing("attachment.upload_derivatives", logger=log, file_no=file_no):
            _upload_derivatives_atomic(
                chunks_key=chunks_key,
                chunks_bytes=chunker.chunks_to_jsonl(chunks),
                index_key=index_key,
                index_bytes=index_bytes,
            )

        # 전체 문서 텍스트를 read_attachment(전체 읽기) 용 파생물로 저장 (best-effort).
        # 실패해도 read_attachment 가 chunks.jsonl 재조립으로 폴백하므로 본 흐름은 계속.
        text_key = f"{s3_prefix}text.txt"
        try:
            storage_service.upload_bytes(
                parsed.text.encode("utf-8"),
                text_key,
                content_type="text/plain; charset=utf-8",
            )
        except Exception:
            log.warning("text.txt 업로드 실패(무시) file_no=%s", file_no, exc_info=True)

        # commit point: 이 라인 이전에는 검색에서 스킵됨
        _update_token_count(file_no, emp_no, total_tokens)

        smry_id = _smry_id_from_record(file_no)
        if smry_id:
            embedding_service.get_cache().invalidate(smry_id)

        log.info(
            "process_attachment 완료: file_no=%s chunks=%d tokens=%d",
            file_no,
            len(chunks),
            total_tokens,
            extra={
                "file_no": file_no,
                "chunks": len(chunks),
                "tokens": total_tokens,
                "elapsed_ms": int((time.time() - overall_start) * 1000),
            },
        )
        return True

    except Exception as exc:
        log.exception("process_attachment 실패: file_no=%s err=%s", file_no, exc)
        # 실패 상태를 영속화 → UI 가 '처리중'에 멈추지 않고 '실패'로 표시.
        try:
            _update_token_count(file_no, emp_no, TOKEN_COUNT_FAILED)
        except Exception:
            log.warning("첨부 실패 상태 마킹 실패 file_no=%s", file_no, exc_info=True)
        return False
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            log.debug("임시파일 정리 실패 path=%s", tmp_path, exc_info=True)


def _update_token_count(
    file_no: int,
    emp_no: str,
    total_tokens: int,
    max_retries: int = DB_UPDATE_RETRIES,
) -> None:
    """atch_file_m.atch_file_tokn_ecnt 갱신.

    S3 파생물 업로드 성공 직후 호출되는 commit point 이므로 일시 DB 장애에
    대비해 지수 백오프 재시도를 수행. 모든 시도가 실패하면 마지막
    예외를 전파 (호출부가 process_attachment 실패로 처리).
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            now = datetime.now(KST)
            with get_session() as session:
                record = session.get(Attachment, file_no)
                if not record:
                    return
                record.atch_file_tokn_ecnt = total_tokens
                record.upd_dtm = now
                record.uppr_id = emp_no[:20]
            return
        except Exception as exc:
            last_exc = exc
            log.warning(
                "token_count 갱신 실패 (시도 %d/%d) file_no=%s: %s",
                attempt + 1, max_retries, file_no, exc,
            )
            if attempt < max_retries - 1:
                time.sleep(DB_UPDATE_RETRY_BASE_DELAY * (2 ** attempt))
    assert last_exc is not None
    raise last_exc


def _smry_id_from_record(file_no: int) -> str:
    """DB 매핑 테이블에서 smry_id 조회.

    1차: chtb_msg_atch_file_d → chtb_msg_d 경유 (메시지 저장 완료 상태)
    2차: atch_file_m.atch_file_url_addr (S3 prefix) 에서 추출 (폴백)
         prefix 구조: {KEY_PREFIX}/{emp_no}/{smry_id}/{file_no}/
    """
    with get_session() as session:
        # 1차: 메시지 경유 조회
        row = (
            session.query(ChatMessage.chtb_tlk_smry_id)
            .join(
                ChatMessageAttachment,
                ChatMessageAttachment.chtb_tlk_id == ChatMessage.chtb_tlk_id,
            )
            .filter(ChatMessageAttachment.atch_file_no == file_no)
            .first()
        )
        if row and row[0]:
            return row[0]

        # 2차: S3 prefix 에서 smry_id 추출 (폴백)
        record = session.get(Attachment, file_no)
        if record and record.atch_file_url_addr:
            parts = record.atch_file_url_addr.strip("/").split("/")
            # prefix 구조: {KEY_PREFIX}/{emp_no}/{smry_id}/{file_no}/
            # smry_id 는 뒤에서 두 번째 세그먼트
            if len(parts) >= 3:
                return parts[-2]

        return ""


# ── 조회 ──


def get_conversation_attachments(smry_id: str) -> list[AttachmentRecord]:
    """대화에 연결된 모든 첨부파일 목록 반환.

    chtb_msg_atch_file_d → chtb_msg_d 를 경유하여
    해당 세션(smry_id)에 속한 첨부파일을 조회.
    """
    with get_session() as session:
        rows = (
            session.query(Attachment)
            .join(
                ChatMessageAttachment,
                ChatMessageAttachment.atch_file_no == Attachment.atch_file_no,
            )
            .join(
                ChatMessage,
                ChatMessage.chtb_tlk_id == ChatMessageAttachment.chtb_tlk_id,
            )
            .filter(ChatMessage.chtb_tlk_smry_id == smry_id)
            .order_by(Attachment.atch_file_no.asc())
            .all()
        )
        return [
            AttachmentRecord(
                file_no=int(r.atch_file_no),
                file_name=r.atch_file_nm or "",
                s3_prefix=r.atch_file_url_addr or "",
                token_count=(
                    int(r.atch_file_tokn_ecnt)
                    if r.atch_file_tokn_ecnt is not None
                    else None
                ),
                mime=file_parser.guess_mime(r.atch_file_nm or ""),
            )
            for r in rows
        ]


def get_attachments_by_msg_id(msg_id: str) -> list[AttachmentRecord]:
    """메시지 ID(chtb_tlk_id) 로 첨부파일 목록 직접 조회.

    메시지가 아직 chtb_msg_d 에 저장되기 전(업로드 직후 polling)에도
    chtb_msg_atch_file_d 에서 바로 조회 가능.
    """
    if not msg_id:
        return []
    with get_session() as session:
        rows = (
            session.query(Attachment)
            .join(
                ChatMessageAttachment,
                ChatMessageAttachment.atch_file_no == Attachment.atch_file_no,
            )
            .filter(ChatMessageAttachment.chtb_tlk_id == msg_id)
            .order_by(Attachment.atch_file_no.asc())
            .all()
        )
        return [
            AttachmentRecord(
                file_no=int(r.atch_file_no),
                file_name=r.atch_file_nm or "",
                s3_prefix=r.atch_file_url_addr or "",
                token_count=(
                    int(r.atch_file_tokn_ecnt)
                    if r.atch_file_tokn_ecnt is not None
                    else None
                ),
                mime=file_parser.guess_mime(r.atch_file_nm or ""),
            )
            for r in rows
        ]


def get_attachment(file_no: int) -> AttachmentRecord | None:
    """단일 첨부파일 조회"""
    with get_session() as session:
        record = session.get(Attachment, file_no)
        if not record:
            return None
        return AttachmentRecord(
            file_no=int(record.atch_file_no),
            file_name=record.atch_file_nm or "",
            s3_prefix=record.atch_file_url_addr or "",
            token_count=(
                int(record.atch_file_tokn_ecnt)
                if record.atch_file_tokn_ecnt is not None
                else None
            ),
            mime=file_parser.guess_mime(record.atch_file_nm or ""),
        )



def download_original_bytes(file_no: int) -> bytes | None:
    """원본 파일 바이너리를 S3 에서 다운로드.

    이미지 첨부를 Bedrock Converse image block 으로 전달할 때 사용.
    """
    att = get_attachment(file_no)
    if not att or not att.s3_prefix:
        return None
    ext = Path(att.file_name).suffix.lower()
    s3_key = f"{att.s3_prefix}original{ext}"
    try:
        return storage_service.download_bytes(s3_key)
    except Exception as exc:
        log.warning("원본 다운로드 실패 (file_no=%s): %s", file_no, exc)
        return None


def verify_ownership(file_no: int, emp_no: str) -> bool:
    """사원이 해당 파일의 대화 소유자인지 여부.

    1차: chtb_msg_atch_file_d → chtb_msg_d → chtb_smry_d 경유 (메시지 저장 완료 상태)
    2차: atch_file_m.rgsr_id 확인 (메시지 미저장 pending 상태 폴백)
    """
    from wellbot.models.chat_summary import ChatSummary

    with get_session() as session:
        # 1차: 메시지 경유 소유권 확인
        row = (
            session.query(ChatSummary)
            .join(
                ChatMessage,
                ChatMessage.chtb_tlk_smry_id == ChatSummary.chtb_tlk_smry_id,
            )
            .join(
                ChatMessageAttachment,
                ChatMessageAttachment.chtb_tlk_id == ChatMessage.chtb_tlk_id,
            )
            .filter(
                ChatMessageAttachment.atch_file_no == file_no,
                ChatSummary.emp_no == emp_no,
            )
            .first()
        )
        if row is not None:
            return True

        # 2차: 메시지 미저장 상태 (pending) — 등록자로 폴백
        record = session.get(Attachment, file_no)
        if record and record.rgsr_id == emp_no[:20]:
            return True

        return False


def delete_attachment(file_no: int, emp_no: str) -> bool:
    """첨부파일 삭제 (DB + S3). 소유권 확인 포함"""
    if not verify_ownership(file_no, emp_no):
        return False

    att = get_attachment(file_no)
    if not att:
        return False

    if att.s3_prefix:
        try:
            storage_service.delete_prefix(att.s3_prefix)
        except Exception as exc:
            log.warning("S3 삭제 실패 (file_no=%s): %s", file_no, exc)

    # DB 삭제 전에 smry_id 조회 (삭제 후에는 조회 불가)
    smry_id = _smry_id_from_record(file_no)

    with get_session() as session:
        session.query(ChatMessageAttachment).filter(
            ChatMessageAttachment.atch_file_no == file_no
        ).delete()
        session.query(Attachment).filter(Attachment.atch_file_no == file_no).delete()

    if smry_id:
        embedding_service.get_cache().invalidate(smry_id)
    return True


def count_conversation_attachments(
    smry_id: str,
    pending_msg_id: str = "",
) -> tuple[int, int]:
    """대화 내 첨부파일 개수 및 총 토큰 수.

    chtb_msg_d 에 저장된 메시지 경유 조회 + 아직 메시지 미저장 상태인
    pending_msg_id 의 첨부파일을 합산.

    Args:
        smry_id: 대화 세션 ID
        pending_msg_id: 아직 chtb_msg_d 에 없는 메시지 ID (업로드 중)

    Returns:
        (file_count, total_tokens)
    """
    with get_session() as session:
        base_query = (
            session.query(Attachment.atch_file_no, Attachment.atch_file_tokn_ecnt)
            .join(
                ChatMessageAttachment,
                ChatMessageAttachment.atch_file_no == Attachment.atch_file_no,
            )
            .join(
                ChatMessage,
                ChatMessage.chtb_tlk_id == ChatMessageAttachment.chtb_tlk_id,
            )
            .filter(ChatMessage.chtb_tlk_smry_id == smry_id)
        )

        if pending_msg_id:
            pending_query = (
                session.query(Attachment.atch_file_no, Attachment.atch_file_tokn_ecnt)
                .join(
                    ChatMessageAttachment,
                    ChatMessageAttachment.atch_file_no == Attachment.atch_file_no,
                )
                .filter(ChatMessageAttachment.chtb_tlk_id == pending_msg_id)
            )
            rows = base_query.union(pending_query).all()
        else:
            rows = base_query.all()

        file_count = len(rows)
        total_tokens = sum(int(r[1] or 0) for r in rows)
        return file_count, total_tokens
